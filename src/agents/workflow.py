# src/agents/workflow.py

import logging
import re
from typing import Optional, TypedDict

import sqlglot
from langchain_community.chat_models import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from modules.context_builder import RAGContextBuilder
from modules.postgres_verifier import VerifierAgent
from agents.prompts import CONVERTER_SYSTEM_PROMPT, REVIEWER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    file_path: str
    source_sql: str
    target_sql: Optional[str]
    status: str
    error_msg: Optional[str]
    retry_count: int
    rag_context: Optional[str]
    schema_refs: Optional[list[str]]
    skipped_statements: Optional[list[str]]
    executed_statements: int


class MigrationWorkflow:
    def __init__(self, config: dict, rag: RAGContextBuilder, verifier: VerifierAgent):
        self.config = config
        self.rag = rag
        self.verifier = verifier
        self.max_retries = config["project"]["max_retries"]
        self.source_dialect = config["database"]["source"].get("type", "oracle")
        self.target_dialect = config["database"]["target"].get("type", "postgres")

        self.llm = ChatOllama(
            base_url=config["llm"]["base_url"],
            model=config["llm"]["model"],
            temperature=config["llm"].get("temperature", 0.1),
        )

        self.app = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(AgentState)

        workflow.add_node("transpiler", self.transpiler_node)
        workflow.add_node("reviewer", self.reviewer_node)
        workflow.add_node("verifier", self.verifier_node)
        workflow.add_node("converter", self.converter_node)
        workflow.add_node("fail", self.fail_node)

        workflow.set_entry_point("transpiler")
        workflow.add_edge("transpiler", "reviewer")

        workflow.add_conditional_edges(
            "reviewer",
            self.check_review,
            {"pass": "verifier", "fail": "converter"},
        )

        workflow.add_conditional_edges(
            "verifier",
            self.check_verification,
            {"success": END, "fail": "converter"},
        )

        workflow.add_conditional_edges(
            "converter",
            self.check_retry,
            {"retry": "reviewer", "abort": "fail"},
        )

        return workflow.compile()

    def transpiler_node(self, state: AgentState):
        logger.info(f"[{state['file_path']}] Transpiling...")
        try:
            transpiled = sqlglot.transpile(
                state["source_sql"], read=self.source_dialect, write=self.target_dialect
            )[0]
            logger.debug(
                "[%s] Transpile success (source len=%d, target len=%d)",
                state['file_path'],
                len(state["source_sql"] or ""),
                len(transpiled or ""),
            )
            return {"target_sql": transpiled, "status": "PENDING"}
        except Exception as e:
            logger.exception("[%s] Transpile failed", state['file_path'])
            return {
                "target_sql": state["source_sql"],
                "error_msg": str(e),
                "status": "FAILED",
            }

    def reviewer_node(self, state: AgentState):
        logger.info(f"[{state['file_path']}] Reviewing...")

        messages = [
            SystemMessage(content=REVIEWER_SYSTEM_PROMPT),
            HumanMessage(content=f"SQL: {state['target_sql']}"),
        ]
        response = self.llm.invoke(messages).content.strip()
        logger.debug("[%s] Reviewer response: %s", state['file_path'], response)

        first_line = response.splitlines()[0].strip().upper() if response else ""
        if first_line.startswith("PASS"):
            return {"status": "REVIEW_PASS"}
        return {"status": "REVIEW_FAIL", "error_msg": response}

    def verifier_node(self, state: AgentState):
        logger.info(f"[{state['file_path']}] Verifying in DB...")

        result = self.verifier.verify_sql(state["target_sql"])

        if result.success:
            return {
                "status": "DONE",
                "error_msg": result.notes,
                "skipped_statements": result.skipped_statements,
                "executed_statements": result.executed_statements,
            }
        return {
            "status": "VERIFY_FAIL",
            "error_msg": result.error,
            "skipped_statements": result.skipped_statements,
            "executed_statements": result.executed_statements,
        }

    def converter_node(self, state: AgentState):
        logger.info(f"[{state['file_path']}] Converting (Retry {state['retry_count'] + 1})...")

        context = state.get("rag_context")
        schema_refs = state.get("schema_refs") or []
        if not context:
            ctx_result = self.rag.build_context(state["source_sql"])
            context = ctx_result.context
            schema_refs = sorted(ctx_result.referenced_schemas)

        prompt = CONVERTER_SYSTEM_PROMPT.format(
            rag_context=context,
            current_sql=state["target_sql"],
            error_msg=state["error_msg"],
        )

        response = self.llm.invoke([HumanMessage(content=prompt)]).content
        cleaned_sql = self._extract_sql(response)

        normalized_sql = cleaned_sql
        try:
            parsed = sqlglot.parse(cleaned_sql, read=self.target_dialect)
            if parsed:
                normalized_sql = ";\n".join(
                    stmt.sql(dialect=self.target_dialect) for stmt in parsed
                )
        except Exception as parse_err:
            logger.warning(
                "[%s] Converter output could not be parsed: %s",
                state["file_path"],
                parse_err,
            )
        logger.debug(
            "[%s] Converter produced len=%d (retry=%d)",
            state['file_path'],
            len(normalized_sql or ""),
            state["retry_count"] + 1,
        )

        return {
            "target_sql": normalized_sql,
            "retry_count": state["retry_count"] + 1,
            "rag_context": context,
            "schema_refs": schema_refs,
        }

    @staticmethod
    def _extract_sql(response: str) -> str:
        if not response:
            return ""

        match = re.search(r"```(?:sql)?\s*(.*?)```", response, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
        return response.replace("```sql", "").replace("```", "").strip()

    def check_review(self, state: AgentState):
        if state["status"] == "REVIEW_PASS":
            return "pass"
        return "fail"

    def check_verification(self, state: AgentState):
        if state["status"] == "DONE":
            return "success"
        return "fail"

    def check_retry(self, state: AgentState):
        if state["retry_count"] < self.max_retries:
            return "retry"
        logger.error(f"[{state['file_path']}] Max retries exceeded.")
        return "abort"

    def fail_node(self, state: AgentState):
        max_retry_msg = (
            f"Reached max retries ({self.max_retries}) without passing verification."
        )
        error_msg = state.get("error_msg") or max_retry_msg
        if error_msg and max_retry_msg not in error_msg:
            error_msg = f"{error_msg} | {max_retry_msg}"

        logger.error("[%s] Marking migration as FAILED: %s", state["file_path"], error_msg)
        return {"status": "FAILED", "error_msg": error_msg}
