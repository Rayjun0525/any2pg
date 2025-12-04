import logging
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

import sqlglot
from sqlglot import exp

from .sqlite_store import DBManager

logger = logging.getLogger(__name__)


@dataclass
class ContextResult:
    context: str
    referenced_names: Set[str]
    referenced_schemas: Set[str]


class RAGContextBuilder:
    def __init__(
        self, db_manager: DBManager, source_dialect: str = "oracle", project_name: str = "default"
    ):
        self.db_mngr = db_manager
        self.source_dialect = source_dialect
        self.project_name = project_name

    def build_context(self, sql_script: str) -> ContextResult:
        referenced_names, schema_refs = self._extract_references(sql_script)
        if not referenced_names:
            return ContextResult(context="", referenced_names=set(), referenced_schemas=set())

        metadata_list = self._fetch_metadata(referenced_names)
        if not metadata_list:
            return ContextResult(context="", referenced_names=referenced_names, referenced_schemas=schema_refs)

        logger.debug(
            "RAG context built for references: %s", ", ".join(sorted(referenced_names))
        )
        return ContextResult(
            context=self._format_output(metadata_list),
            referenced_names=referenced_names,
            referenced_schemas=schema_refs,
        )

    def get_context(self, sql_script: str) -> str:
        return self.build_context(sql_script).context

    def _extract_references(self, sql: str) -> Tuple[Set[str], Set[str]]:
        refs: Set[str] = set()
        schema_refs: Set[str] = set()
        try:
            parsed_statements = sqlglot.parse(sql, read=self.source_dialect)
            for stmt in parsed_statements:
                for table in stmt.find_all(exp.Table):
                    if table.name:
                        refs.add(table.name.upper())
                    schema_token = getattr(table, "db", None)
                    if getattr(schema_token, "name", None):
                        schema_refs.add(schema_token.name.upper())
                for func in stmt.find_all(exp.Func):
                    func_name = func.sql_name() or ""
                    if func_name.upper() == "ANONYMOUS":
                        func_root = getattr(func, "this", None)
                        if isinstance(func_root, str):
                            func_name = func_root
                        elif getattr(func_root, "name", None):
                            func_name = func_root.name
                    if func_name:
                        refs.add(func_name.upper())
        except Exception as e:
            logger.warning(f"Failed to parse SQL for RAG context: {e}")
        return refs, schema_refs

    def _fetch_metadata(self, names: Set[str]) -> List[Dict]:
        if not names:
            return []

        placeholders = ",".join(["?"] * len(names))
        sql = f"""
            SELECT schema_name, obj_name, obj_type, ddl_script, source_code
            FROM schema_objects
            WHERE obj_name IN ({placeholders}) AND project_name = ?
            ORDER BY
                CASE obj_type WHEN 'TABLE' THEN 0 WHEN 'VIEW' THEN 1 ELSE 2 END,
                schema_name,
                obj_name
        """

        results: List[Dict] = []
        try:
            with self.db_mngr.get_cursor() as cur:
                cur.execute(sql, list(names) + [self.project_name])
                rows = cur.fetchall()
                for row in rows:
                    results.append(
                        {
                            "schema_name": row["schema_name"],
                            "name": row["obj_name"],
                            "type": row["obj_type"],
                            "ddl": row["ddl_script"],
                            "source": row["source_code"],
                        }
                    )
                logger.debug("RAG fetch matched %d objects", len(results))
        except Exception as e:
            logger.error(f"Error fetching metadata: {e}")
        return results

    def _format_output(self, metadata_list: List[Dict]) -> str:
        context_parts = ["\n--- [Related Schema Information] ---"]

        for meta in metadata_list:
            full_name = f"{meta['schema_name']}.{meta['name']}"

            if meta["type"] in ("TABLE", "VIEW") and meta.get("ddl"):
                context_parts.append(f"-- Table/View: {full_name}")
                context_parts.append(meta["ddl"])

            elif meta["type"] in ("PROCEDURE", "FUNCTION", "PACKAGE") and meta.get("source"):
                context_parts.append(f"-- Source Code: {full_name}")
                context_parts.append(meta["source"])

        context_parts.append("------------------------------------\n")
        return "\n".join(context_parts)
