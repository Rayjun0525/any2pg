import logging
from typing import Dict, List, Set

import sqlglot
from sqlglot import exp

from .db_manager import DBManager

logger = logging.getLogger(__name__)


class RAGContextBuilder:
    def __init__(self, db_manager: DBManager, source_dialect: str = "oracle"):
        self.db_mngr = db_manager
        self.source_dialect = source_dialect

    def get_context(self, sql_script: str) -> str:
        referenced_names = self._extract_references(sql_script)
        if not referenced_names:
            return ""

        metadata_list = self._fetch_metadata(referenced_names)
        if not metadata_list:
            return ""

        return self._format_output(metadata_list)

    def _extract_references(self, sql: str) -> Set[str]:
        refs: Set[str] = set()
        try:
            parsed_statements = sqlglot.parse(sql, read=self.source_dialect)
            for stmt in parsed_statements:
                for table in stmt.find_all(exp.Table):
                    if table.name:
                        refs.add(table.name.upper())
                for func in stmt.find_all(exp.Func):
                    if func.sql_name():
                        refs.add(func.sql_name().upper())
        except Exception as e:
            logger.warning(f"Failed to parse SQL for RAG context: {e}")
        return refs

    def _fetch_metadata(self, names: Set[str]) -> List[Dict]:
        if not names:
            return []

        placeholders = ",".join(["?"] * len(names))
        sql = f"""
            SELECT schema_name, obj_name, obj_type, ddl_script, source_code
            FROM schema_objects
            WHERE obj_name IN ({placeholders})
        """

        results: List[Dict] = []
        try:
            with self.db_mngr.get_cursor() as cur:
                cur.execute(sql, list(names))
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
