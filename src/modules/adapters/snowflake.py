from sqlalchemy import inspect, text

from .base import BaseDBAdapter


class SnowflakeAdapter(BaseDBAdapter):
    def get_tables_and_views(self, schema: str | None = None) -> list[dict]:
        inspector = inspect(self.engine)
        results = []

        for t_name in inspector.get_table_names(schema=schema):
            columns = inspector.get_columns(t_name, schema=schema)
            col_defs = [f"  {col['name']} {col['type']}" for col in columns]
            ddl = f"CREATE TABLE {t_name} (\n" + ",\n".join(col_defs) + "\n);"
            results.append({"name": t_name, "type": "TABLE", "ddl": ddl, "source": None})

        for v_name in inspector.get_view_names(schema=schema):
            v_def = inspector.get_view_definition(v_name, schema=schema)
            results.append({"name": v_name, "type": "VIEW", "ddl": None, "source": v_def})

        return results

    def get_procedures(self, schema: str | None = None) -> list[dict]:
        target_schema = schema.upper() if schema else None

        sql = """
        SELECT ROUTINE_NAME, ROUTINE_TYPE, ROUTINE_DEFINITION
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_TYPE IN ('PROCEDURE', 'FUNCTION')
        {schema_filter}
        ORDER BY ROUTINE_NAME, ROUTINE_TYPE
        """

        schema_filter = "AND ROUTINE_SCHEMA = :schema" if target_schema else ""
        params = {"schema": target_schema} if target_schema else {}
        query = sql.format(schema_filter=schema_filter)

        results = []
        with self.engine.connect() as conn:
            rows = conn.execute(text(query), params)
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
        return results
