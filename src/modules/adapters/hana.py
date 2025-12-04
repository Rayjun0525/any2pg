import logging

from sqlalchemy import inspect, text

from .base import BaseDBAdapter


class HANAAdapter(BaseDBAdapter):
    def __init__(self, engine):
        super().__init__(engine)
        self.logger = logging.getLogger(__name__)

    def get_tables_and_views(self, schema: str | None = None) -> list[dict]:
        inspector = inspect(self.engine)
        target_schema = schema.upper() if schema else None
        results = []

        try:
            for t_name in inspector.get_table_names(schema=target_schema):
                columns = inspector.get_columns(t_name, schema=target_schema)
                col_defs = [f"  {col['name']} {col['type']}" for col in columns]
                ddl = f"CREATE TABLE {t_name} (\n" + ",\n".join(col_defs) + "\n);"
                results.append({"name": t_name, "type": "TABLE", "ddl": ddl, "source": None})
        except Exception:
            self.logger.warning("Failed to fetch HANA tables for schema %s", target_schema)

        try:
            for v_name in inspector.get_view_names(schema=target_schema):
                v_def = inspector.get_view_definition(v_name, schema=target_schema)
                results.append({"name": v_name, "type": "VIEW", "ddl": None, "source": v_def})
        except Exception:
            self.logger.warning("Failed to fetch HANA views for schema %s", target_schema)

        return results

    def get_procedures(self, schema: str | None = None) -> list[dict]:
        target_schema = schema.upper() if schema else None

        if not target_schema:
            try:
                with self.engine.connect() as conn:
                    target_schema = conn.execute(text("SELECT CURRENT_SCHEMA FROM DUMMY")).scalar_one_or_none()
            except Exception:
                self.logger.warning("Failed to resolve current HANA schema; defaulting to SYSTEM")
                target_schema = "SYSTEM"

        sql = """
        SELECT PROCEDURE_NAME AS NAME, 'PROCEDURE' AS TYPE, DEFINITION
        FROM SYS.PROCEDURES
        WHERE SCHEMA_NAME = :schema
        UNION ALL
        SELECT FUNCTION_NAME AS NAME, 'FUNCTION' AS TYPE, DEFINITION
        FROM SYS.FUNCTIONS
        WHERE SCHEMA_NAME = :schema
        """

        results = []
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(sql), {"schema": target_schema})
                for row in rows:
                    results.append(
                        {
                            "name": row[0],
                            "type": row[1],
                            "ddl": None,
                            "source": row[2],
                            "schema": target_schema,
                        }
                    )
        except Exception:
            self.logger.warning("Failed to fetch HANA routines for schema %s", target_schema)
        return results
