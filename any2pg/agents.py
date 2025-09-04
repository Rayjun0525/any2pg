"""Agent definitions for SQL translation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import json
import logging
import psycopg
from autogen import AssistantAgent


@dataclass
class BaseAgent:
    """Base wrapper around :class:`autogen.AssistantAgent`."""

    name: str
    system_message: str
    llm_config: dict
    agent: AssistantAgent = field(init=False)

    def __post_init__(self) -> None:  # pragma: no cover - simple initialization
        self.agent = AssistantAgent(
            name=self.name, llm_config=self.llm_config, system_message=self.system_message
        )

    def run(self, prompt: str) -> str:
        try:
            reply = self.agent.generate_reply(messages=[{"role": "user", "content": prompt}])
        except Exception as exc:
            logging.getLogger("any2pg").error("Agent %s failed: %s", self.name, exc)
            raise
        if isinstance(reply, dict):
            return reply.get("content", "")
        return str(reply)


class SemanticAnalysisAgent(BaseAgent):
    def analyze(self, sql: str) -> str:
        prompt = f"""Analyze the semantics of the following SQL and describe any dialect specifics:

{sql}
"""
        return self.run(prompt)


class QueryWritingAgent(BaseAgent):
    def write_postgres(
        self, sql: str, analysis: str, patterns: list[dict[str, str]] | None = None
    ) -> str:
        pattern_txt = "\n\n".join(
            f"Source Pattern:\n{p['source_pattern']}\nPostgres Pattern:\n{p['postgres_pattern']}"
            for p in patterns or []
        )
        prompt = f"""Using the analysis below, rewrite the SQL so that it runs on PostgreSQL.

Analysis:
{analysis}

Original SQL:
{sql}

Known Patterns:
{pattern_txt}
"""
        return self.run(prompt)


class ValidationAgent(BaseAgent):
    def validate(self, postgres_sql: str) -> dict:
        prompt = f"""Check whether the following SQL is valid PostgreSQL syntax. Return a JSON object with keys
"approved" (true if the SQL is high quality), "sql" (the corrected SQL), and optional "reason" when not approved.

{postgres_sql}
"""
        try:
            return json.loads(self.run(prompt))
        except Exception:  # pragma: no cover - model deviations
            return {"approved": False, "sql": postgres_sql, "reason": "invalid response"}


class KnowledgeAgent(BaseAgent):
    """Agent responsible for internalizing knowledge from conversions."""

    def internalize(self, analysis: str, result: str) -> str:
        prompt = f"""Summarize the key learnings from the analysis and result to aid future translations.

Analysis:
{analysis}

Result:
{result}
"""
        return self.run(prompt)


class MetaInfoAgent(BaseAgent):
    """Agent that extracts and stores generalized SQL patterns."""

    def __init__(
        self,
        name: str,
        system_message: str,
        llm_config: dict,
        path: Optional[Path] = None,
        enabled: bool = False,
    ) -> None:
        super().__init__(name=name, system_message=system_message, llm_config=llm_config)
        self.path = Path(path) if path else None
        self.enabled = enabled

    def load_patterns(self) -> list[dict[str, str]]:
        if not (self.enabled and self.path and self.path.exists()):
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        patterns = data.get("patterns", []) if isinstance(data, dict) else data

        unique: list[dict[str, str]] = []
        seen = set()
        for p in patterns:
            key = (p.get("source_pattern"), p.get("postgres_pattern"))
            if key not in seen:
                seen.add(key)
                unique.append(p)

        if len(unique) != len(patterns):
            self.path.write_text(
                json.dumps({"patterns": unique}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return unique

    def save_pattern(self, source_sql: str, postgres_sql: str) -> None:
        if not (self.enabled and self.path):
            return

        data = {"patterns": []}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
        patterns: list[dict[str, str]] = data.setdefault("patterns", [])

        source_pattern = self.run(
            "Generalize the SQL into a pattern using placeholders for identifiers and literals. "
            "Do not include schema or table names.\n\nSQL:\n" + source_sql
        )
        postgres_pattern = self.run(
            "Generalize the SQL into a pattern using placeholders for identifiers and literals. "
            "Do not include schema or table names.\n\nSQL:\n" + postgres_sql
        )

        import hashlib

        pid = hashlib.sha256(
            (source_pattern + "->" + postgres_pattern).encode("utf-8")
        ).hexdigest()[:8]

        for p in patterns:
            if (
                p.get("source_pattern") == source_pattern
                and p.get("postgres_pattern") == postgres_pattern
            ):
                return

        patterns.append(
            {
                "id": pid,
                "source_pattern": source_pattern,
                "postgres_pattern": postgres_pattern,
            }
        )
        self.path.write_text(
            json.dumps({"patterns": patterns}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


class ExecutionAgent(BaseAgent):
    """Agent that executes queries against PostgreSQL for verification."""

    def __init__(
        self,
        name: str,
        system_message: str,
        llm_config: dict,
        dsn: Optional[str] = None,
        enabled: bool = False,
    ) -> None:
        super().__init__(name=name, system_message=system_message, llm_config=llm_config)
        self.dsn = dsn
        self.enabled = enabled

    def execute(self, postgres_sql: str) -> str:
        """Execute ``postgres_sql`` against the configured PostgreSQL database."""

        if not (self.enabled and self.dsn):
            return "Execution disabled"

        try:
            with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
                cur.execute(postgres_sql)
                if cur.description:
                    rows = cur.fetchall()
                    return f"Returned {len(rows)} rows"
                return f"{cur.rowcount} rows affected"
        except Exception as exc:  # pragma: no cover - connection errors
            return f"Execution error: {exc}"
