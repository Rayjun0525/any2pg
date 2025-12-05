from sqlalchemy import inspect, text

from .base import BaseDBAdapter


class MSSQLAdapter(BaseDBAdapter):
    def get_tables_and_views(self, schema: str | None = None) -> list[dict]:
        inspector = inspect(self.engine)
        target_schema = schema if schema else "dbo"
        results = []

        for t_name in inspector.get_table_names(schema=target_schema):
            columns = inspector.get_columns(t_name, schema=target_schema)
            col_defs = [f"  {col['name']} {col['type']}" for col in columns]
            ddl = f"CREATE TABLE {t_name} (\n" + ",\n".join(col_defs) + "\n);"
            results.append({"name": t_name, "type": "TABLE", "ddl": ddl, "source": None})

        for v_name in inspector.get_view_names(schema=target_schema):
            v_def = inspector.get_view_definition(v_name, schema=target_schema)
            results.append({"name": v_name, "type": "VIEW", "ddl": None, "source": v_def})

        return results

    def get_procedures(self, schema: str | None = None) -> list[dict]:
        target_schema = schema if schema else "dbo"

        sql = """
        SELECT o.name, o.type, m.definition
        FROM sys.objects o
        JOIN sys.sql_modules m ON o.object_id = m.object_id
        WHERE o.type IN ('P', 'FN', 'IF', 'TF')
          AND SCHEMA_NAME(o.schema_id) = :schema
        """

        results = []
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), {"schema": target_schema})
            for row in rows:
                results.append(
                    {
                        "name": row[0],
                        "type": "PROCEDURE" if row[1].strip() == "P" else "FUNCTION",
                        "ddl": None,
                        "source": row[2],
                    }
                )
        return results
