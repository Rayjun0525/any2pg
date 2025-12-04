# src/agents/workflow.py

import logging
from typing import Optional, TypedDict

import sqlglot
from langchain_community.chat_models import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from modules.rag_engine import RAGContextBuilder
from modules.verifier import VerifierAgent
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
            {"retry": "reviewer", "abort": END},
        )

        return workflow.compile()

    def transpiler_node(self, state: AgentState):
        logger.info(f"[{state['file_path']}] Transpiling...")
        try:
            transpiled = sqlglot.transpile(
                state["source_sql"], read=self.source_dialect, write=self.target_dialect
            )[0]
            return {"target_sql": transpiled, "status": "PENDING"}
        except Exception as e:
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

        if "PASS" in response.upper():
            return {"status": "REVIEW_PASS"}
        return {"status": "REVIEW_FAIL", "error_msg": response}

    def verifier_node(self, state: AgentState):
        logger.info(f"[{state['file_path']}] Verifying in DB...")

        success, error = self.verifier.verify_sql(state["target_sql"])

        if success:
            return {"status": "DONE", "error_msg": None}
        return {"status": "VERIFY_FAIL", "error_msg": error}

    def converter_node(self, state: AgentState):
        logger.info(f"[{state['file_path']}] Converting (Retry {state['retry_count'] + 1})...")

        context = state.get("rag_context")
        if not context:
            context = self.rag.get_context(state["source_sql"])

        prompt = CONVERTER_SYSTEM_PROMPT.format(
            rag_context=context,
            current_sql=state["target_sql"],
            error_msg=state["error_msg"],
        )

        response = self.llm.invoke([HumanMessage(content=prompt)]).content
        cleaned_sql = response.replace("```sql", "").replace("```", "").strip()

        return {
            "target_sql": cleaned_sql,
            "retry_count": state["retry_count"] + 1,
            "rag_context": context,
        }

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
