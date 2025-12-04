# src/agents/workflow.py

import logging
import sqlglot
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_community.chat_models import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage

from modules.rag_engine import RAGContextBuilder
from modules.verifier import VerifierAgent
from agents.prompts import REVIEWER_SYSTEM_PROMPT, CONVERTER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# 1. 상태(State) 정의
class AgentState(TypedDict):
    file_path: str
    source_sql: str
    target_sql: Optional[str]
    status: str          # 'PENDING', 'REVIEW_FAIL', 'VERIFY_FAIL', 'DONE'
    error_msg: Optional[str]
    retry_count: int
    rag_context: Optional[str]

class MigrationWorkflow:
    def __init__(self, config: dict, rag: RAGContextBuilder, verifier: VerifierAgent):
        self.config = config
        self.rag = rag
        self.verifier = verifier
        self.max_retries = config['project']['max_retries']
        
        # LLM 초기화 (Ollama)
        self.llm = ChatOllama(
            base_url=config['llm']['base_url'],
            model=config['llm']['model'],
            temperature=0.1
        )
        
        self.app = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(AgentState)

        # 노드 등록
        workflow.add_node("transpiler", self.transpiler_node)
        workflow.add_node("reviewer", self.reviewer_node)
        workflow.add_node("verifier", self.verifier_node)
        workflow.add_node("converter", self.converter_node)

        # 흐름 정의
        # Start -> Transpiler -> Reviewer
        workflow.set_entry_point("transpiler")
        workflow.add_edge("transpiler", "reviewer")

        # Reviewer 분기
        workflow.add_conditional_edges(
            "reviewer",
            self.check_review,
            {
                "pass": "verifier",
                "fail": "converter"
            }
        )

        # Verifier 분기
        workflow.add_conditional_edges(
            "verifier",
            self.check_verification,
            {
                "success": END,
                "fail": "converter"
            }
        )

        # Converter 분기 (재시도 횟수 체크)
        workflow.add_conditional_edges(
            "converter",
            self.check_retry,
            {
                "retry": "reviewer",  # 수정 후 다시 검수
                "abort": END          # 횟수 초과 시 종료
            }
        )

        return workflow.compile()

    # --- Nodes ---

    def transpiler_node(self, state: AgentState):
        """1차 변환: SQLGlot (Rule-based)"""
        logger.info(f"[{state['file_path']}] Transpiling...")
        try:
            # Oracle -> Postgres 변환
            transpiled = sqlglot.transpile(
                state['source_sql'], 
                read="oracle", 
                write="postgres"
            )[0]
            return {"target_sql": transpiled, "status": "PENDING"}
        except Exception as e:
            return {"target_sql": state['source_sql'], "error_msg": str(e), "status": "FAILED"}

    def reviewer_node(self, state: AgentState):
        """2차 검수: LLM Review"""
        logger.info(f"[{state['file_path']}] Reviewing...")
        
        messages = [
            SystemMessage(content=REVIEWER_SYSTEM_PROMPT),
            HumanMessage(content=f"SQL: {state['target_sql']}")
        ]
        response = self.llm.invoke(messages).content.strip()
        
        if "PASS" in response.upper():
            return {"status": "REVIEW_PASS"}
        else:
            return {"status": "REVIEW_FAIL", "error_msg": response}

    def verifier_node(self, state: AgentState):
        """3차 검증: Target DB Simulation"""
        logger.info(f"[{state['file_path']}] Verifying in DB...")
        
        success, error = self.verifier.verify_sql(state['target_sql'])
        
        if success:
            return {"status": "DONE", "error_msg": None}
        else:
            return {"status": "VERIFY_FAIL", "error_msg": error}

    def converter_node(self, state: AgentState):
        """에러 수정: LLM with RAG"""
        logger.info(f"[{state['file_path']}] Converting (Retry {state['retry_count'] + 1})...")
        
        # RAG Context 로드 (최초 1회만 하거나 매번 갱신)
        context = state.get('rag_context')
        if not context:
            context = self.rag.get_context(state['source_sql'])
        
        prompt = CONVERTER_SYSTEM_PROMPT.format(
            rag_context=context,
            current_sql=state['target_sql'],
            error_msg=state['error_msg']
        )
        
        response = self.llm.invoke([HumanMessage(content=prompt)]).content
        
        # 마크다운 코드 블록 제거 (`sql ... `)
        cleaned_sql = response.replace("```sql", "").replace("```", "").strip()
        
        return {
            "target_sql": cleaned_sql, 
            "retry_count": state['retry_count'] + 1,
            "rag_context": context
        }

    # --- Conditional Logic (Edges) ---

    def check_review(self, state: AgentState):
        if state['status'] == "REVIEW_PASS":
            return "pass"
        return "fail"

    def check_verification(self, state: AgentState):
        if state['status'] == "DONE":
            return "success"
        return "fail"

    def check_retry(self, state: AgentState):
        if state['retry_count'] < self.max_retries:
            return "retry"
        logger.error(f"[{state['file_path']}] Max retries exceeded.")
        return "abort"