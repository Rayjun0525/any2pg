from sqlalchemy import inspect, text

from .base import BaseDBAdapter


class DB2Adapter(BaseDBAdapter):
    def get_tables_and_views(self, schema: str | None = None) -> list[dict]:
        inspector = inspect(self.engine)
        results = []

        target_schema = schema.upper() if schema else None

        try:
            for t_name in inspector.get_table_names(schema=target_schema):
                columns = inspector.get_columns(t_name, schema=target_schema)
                col_defs = [f"  {col['name']} {col['type']}" for col in columns]
                ddl = f"CREATE TABLE {t_name} (\n" + ",\n".join(col_defs) + "\n);"
                results.append({"name": t_name, "type": "TABLE", "ddl": ddl, "source": None})
        except Exception as e:
            print(f"Warning: Failed to fetch tables for schema {target_schema}: {e}")

        try:
            for v_name in inspector.get_view_names(schema=target_schema):
                v_def = inspector.get_view_definition(v_name, schema=target_schema)
                results.append({"name": v_name, "type": "VIEW", "ddl": None, "source": v_def})
        except Exception:
            pass

        return results

    def get_procedures(self, schema: str | None = None) -> list[dict]:
        target_schema = schema.upper() if schema else None

        sql = """
        SELECT ROUTINENAME, ROUTINETYPE, TEXT
        FROM SYSCAT.ROUTINES
        WHERE OWNERSCHEM = :schema
          AND ROUTINETYPE IN ('P', 'F')
        """

        if not target_schema:
            sql = sql.replace(":schema", "CURRENT SCHEMA")
            params = {}
        else:
            params = {"schema": target_schema}

        results = []
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(sql), params)
                for row in rows:
                    r_name = row[0]
                    r_type_code = row[1]
                    r_source = row[2]

                    obj_type = "PROCEDURE" if r_type_code == "P" else "FUNCTION"

                    results.append({
                        "name": r_name,
                        "type": obj_type,
                        "ddl": None,
                        "source": r_source,
                    })
        except Exception as e:
            print(f"Warning: Failed to fetch DB2 routines: {e}")

        return results
