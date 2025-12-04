from sqlalchemy import inspect, text

from .base import BaseDBAdapter


class MySQLAdapter(BaseDBAdapter):
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
        where_clause = "WHERE ROUTINE_SCHEMA = DATABASE()"
        params = {}

        if schema:
            where_clause = "WHERE ROUTINE_SCHEMA = :schema"
            params = {"schema": schema}

        sql = f"""
        SELECT ROUTINE_NAME, ROUTINE_TYPE, ROUTINE_DEFINITION
        FROM information_schema.ROUTINES
        {where_clause}
        """

        results = []
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), params)
            for row in rows:
                r_name, r_type, r_def = row[0], row[1], row[2]
                results.append(
                    {
                        "name": r_name,
                        "type": r_type,
                        "ddl": None,
                        "source": r_def,
                    }
                )

        return results
