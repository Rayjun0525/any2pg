
import logging
from typing import Dict, List, Set, Tuple, Optional
import json

import sqlglot
from sqlglot import exp, optimizer

logger = logging.getLogger(__name__)

class DependencyAnalyzer:
    """Static analyzer for extracting SQL dependencies."""
    
    @staticmethod
    def analyze(sql: str, dialect: str = "oracle") -> Dict:
        """
        Analyze SQL to find referenced tables, views, and functions.
        Returns a dictionary suitable for JSON serialization.
        """
        refs: Set[str] = set()
        schema_refs: Set[str] = set()
        
        try:
            parsed_statements = sqlglot.parse(sql, read=dialect)
        except Exception as e:
            logger.warning(f"Failed to parse SQL: {e}")
            return {"error": str(e), "tables": [], "schemas": []}

        for stmt in parsed_statements:
            try:
                # Use Scope optimizer
                root = optimizer.scope.build_scope(stmt)
                
                if not root:
                    # Fallback for non-optimizable naming
                    for table in stmt.find_all(exp.Table):
                        DependencyAnalyzer._add_table_ref(table, refs, schema_refs)
                else:
                    for scope in root.traverse():
                        for source in scope.sources.values():
                            if isinstance(source, exp.Table):
                                table_name = source.name.upper()
                                # Check CTEs in current and parent scopes
                                is_cte = False
                                current = scope
                                while current:
                                    if table_name in [c.alias_or_name.upper() for c in current.ctes]:
                                        is_cte = True
                                        break
                                    current = current.parent
                                
                                if not is_cte:
                                    DependencyAnalyzer._add_table_ref(source, refs, schema_refs)
            except Exception as w:
                 logger.debug(f"Scope analysis failed, using fallback: {w}")
                 for table in stmt.find_all(exp.Table):
                    DependencyAnalyzer._add_table_ref(table, refs, schema_refs)

            # Function calls
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

        return {
            "tables": sorted(list(refs)),
            "schemas": sorted(list(schema_refs)),
            "dialect": dialect
        }

    @staticmethod
    def _add_table_ref(table: exp.Table, refs: Set[str], schema_refs: Set[str]):
        if table.name:
            refs.add(table.name.upper())
        schema_token = table.args.get("db")
        if getattr(schema_token, "name", None):
            schema_refs.add(schema_token.name.upper())
