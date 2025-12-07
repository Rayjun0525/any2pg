import logging
import traceback
from typing import List, Dict, Optional, Any
from sqlalchemy import inspect
from sqlalchemy.schema import CreateTable
from sqlalchemy import MetaData, Table

from .sqlite_store import DBManager
from .adapters import get_adapter

logger = logging.getLogger(__name__)


class MetadataExtractor:
    def __init__(self, config: dict, db_manager: DBManager):
        self.config = config
        self.db_mngr = db_manager
        source_conf = self.config["database"]["source"]
        self.adapter = get_adapter(source_conf)
        self.project_name = self.config["project"]["name"]
        self.schemas: List[Optional[str]] = source_conf.get("schemas") or [None]
        if not self.schemas or self.schemas == [None]:
             # Try to detect schema if not provided
             # But for now keep default behavior
             pass

    def run(self):
        """Extract metadata for configured schemas and persist to SQLite."""
        logger.info(f"Starting metadata extraction for project: {self.project_name}")
        try:
            inspector = inspect(self.adapter.engine)
        except Exception as e:
            logger.error(f"Failed to create inspector: {e}")
            return

        for schema in self.schemas:
            self._extract_schema(inspector, schema)

    def _extract_schema(self, inspector, schema: Optional[str]):
        schema_label = schema if schema else "DEFAULT"
        logger.info(f"Extracting schema metadata for {schema_label}")

        objects_to_store = []

        # 1. Tables
        try:
            table_names = inspector.get_table_names(schema=schema)
            for t_name in table_names:
                try:
                    # Construct simple DDL or fetch columns
                    cols = inspector.get_columns(t_name, schema=schema)
                    col_defs = []
                    for c in cols:
                        c_type = str(c['type'])
                        nullable = "NULL" if c.get('nullable', True) else "NOT NULL"
                        col_defs.append(f"{c['name']} {c_type} {nullable}")
                    
                    ddl = f"CREATE TABLE {t_name} (\n  " + ",\n  ".join(col_defs) + "\n);"
                    
                    objects_to_store.append({
                        "name": t_name,
                        "type": "TABLE",
                        "ddl": ddl,
                        "source": None
                    })
                    
                    # Indexes for this table
                    indexes = inspector.get_indexes(t_name, schema=schema)
                    for idx in indexes:
                        idx_name = idx['name']
                        if idx_name:
                            idx_ddl = f"CREATE INDEX {idx_name} ON {t_name} ({', '.join(idx['column_names'])});"
                            objects_to_store.append({
                                "name": idx_name,
                                "type": "INDEX",
                                "ddl": idx_ddl,
                                "source": None
                            })

                    # PK/FK Constraints could also be extracted as separate objects or part of table DDL
                    # For now, let's stick to basic objects
                except Exception as t_err:
                    logger.warning(f"Error extracting table {t_name}: {t_err}")

        except Exception as e:
            logger.error(f"Error getting tables for {schema_label}: {e}")

        # 2. Views
        try:
            view_names = inspector.get_view_names(schema=schema)
            for v_name in view_names:
                try:
                    v_def = inspector.get_view_definition(v_name, schema=schema)
                    objects_to_store.append({
                        "name": v_name,
                        "type": "VIEW",
                        "ddl": None,
                        "source": v_def
                    })
                except Exception as v_err:
                    logger.warning(f"Error extracting view {v_name}: {v_err}")
        except Exception as e:
            logger.error(f"Error getting views for {schema_label}: {e}")

        # 3. Adapter specific extractions (Procedures, Functions, Triggers, Sequences)
        # We rely on the adapter having these methods. If not, we skip.
        
        self._safe_append(objects_to_store, schema, "get_procedures", "PROCEDURE")
        self._safe_append(objects_to_store, schema, "get_functions", "FUNCTION")
        self._safe_append(objects_to_store, schema, "get_triggers", "TRIGGER")
        self._safe_append(objects_to_store, schema, "get_sequences", "SEQUENCE")

        # Save to DB
        if objects_to_store:
            self._save_objects(schema_label, objects_to_store)
        else:
            logger.info(f"No objects found for {schema_label}")

    def _safe_append(self, target_list, schema, method_name, default_type):
        if hasattr(self.adapter, method_name):
            try:
                method = getattr(self.adapter, method_name)
                results = method(schema)
                for r in results:
                    # Normalize format
                    if "type" not in r:
                        r["type"] = default_type
                    target_list.append(r)
            except Exception as e:
                logger.warning(f"Adapter method {method_name} failed: {e}")

    def _save_objects(self, schema_label: str, objects: List[Dict]):
        try:
            with self.db_mngr.get_cursor(commit=True) as cur:
                count = 0
                for obj in objects:
                    cur.execute(
                        """
                        INSERT INTO schema_objects (project_name, schema_name, obj_name, obj_type, ddl_script, source_code)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(project_name, schema_name, obj_name, obj_type)
                            DO UPDATE SET ddl_script=excluded.ddl_script,
                                          source_code=excluded.source_code,
                                          extracted_at=CURRENT_TIMESTAMP
                        """,
                        (
                            self.project_name,
                            schema_label,
                            obj.get("name", "UNKNOWN").upper(),
                            obj.get("type", "UNKNOWN").upper(),
                            obj.get("ddl"),
                            obj.get("source"),
                        ),
                    )
                    count += 1
            logger.info(f"Schema {schema_label}: saved {count} objects")
        except Exception as e:
            logger.error(f"Failed to persist metadata: {e}")
            raise
