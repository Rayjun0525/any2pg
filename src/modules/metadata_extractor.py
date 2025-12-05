import logging
from typing import List, Dict, Optional

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
        # schemas may be None/empty meaning default schema for connection
        self.schemas: List[Optional[str]] = source_conf.get("schemas") or [None]

    def run(self):
        """Extract metadata for configured schemas and persist to SQLite."""
        logger.info("Starting metadata extraction")
        for schema in self.schemas:
            self._extract_schema(schema)

    def _extract_schema(self, schema: Optional[str]):
        schema_label = schema if schema else "DEFAULT"
        logger.info(f"Extracting schema metadata for {schema_label}")

        tables_and_views = self.adapter.get_tables_and_views(schema)
        procedures = self.adapter.get_procedures(schema)
        all_objects: List[Dict] = tables_and_views + procedures

        logger.debug(
            "Schema %s -> %d tables/views, %d routines", 
            schema_label,
            len(tables_and_views),
            len(procedures),
        )

        if not all_objects:
            logger.warning(f"No objects found for schema {schema_label}")
            return

        try:
            with self.db_mngr.get_cursor(commit=True) as cur:
                for obj in all_objects:
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
                            obj["name"].upper(),
                            obj["type"].upper(),
                            obj.get("ddl"),
                            obj.get("source"),
                        ),
                    )
            logger.info(f"Schema {schema_label}: {len(all_objects)} objects stored")
        except Exception as e:
            logger.error(f"Failed to persist metadata for schema {schema_label}: {e}")
            raise
