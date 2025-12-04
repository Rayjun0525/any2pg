from sqlalchemy import inspect, text

from .base import BaseDBAdapter


class HANAAdapter(BaseDBAdapter):
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
            pass

        try:
            for v_name in inspector.get_view_names(schema=target_schema):
                v_def = inspector.get_view_definition(v_name, schema=target_schema)
                results.append({"name": v_name, "type": "VIEW", "ddl": None, "source": v_def})
        except Exception:
            pass

        return results

    def get_procedures(self, schema: str | None = None) -> list[dict]:
        target_schema = schema.upper() if schema else "SYSTEM"

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
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), {"schema": target_schema})
            for row in rows:
                results.append(
                    {
                        "name": row[0],
                        "type": row[1],
                        "ddl": None,
                        "source": row[2],
                    }
                )
        return results
