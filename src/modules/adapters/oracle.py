from sqlalchemy import inspect, text

from .base import BaseDBAdapter


class OracleAdapter(BaseDBAdapter):
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
        SELECT {owner_expr} AS OWNER, NAME, TYPE, LINE, TEXT
        FROM {source_view}
        WHERE TYPE IN ('PROCEDURE', 'FUNCTION', 'PACKAGE', 'PACKAGE BODY')
        {schema_filter}
        ORDER BY OWNER, NAME, TYPE, LINE
        """

        source_view = "ALL_SOURCE" if target_schema else "USER_SOURCE"
        owner_expr = "OWNER" if target_schema else "USER"
        schema_filter = "AND OWNER = :owner" if target_schema else ""
        params = {"owner": target_schema} if target_schema else {}

        query = sql.format(source_view=source_view, schema_filter=schema_filter, owner_expr=owner_expr)

        objects_map: dict[tuple[str, str, str], list[str]] = {}
        with self.engine.connect() as conn:
            result = conn.execute(text(query), params)
            for row in result:
                owner, name, obj_type, line, txt = row[0], row[1], row[2], row[3], row[4]
                key = (owner, name, obj_type)
                objects_map.setdefault(key, []).append(txt)

        results = []
        for (owner, name, obj_type), lines in objects_map.items():
            full_source = "".join(lines)
            results.append({
                "name": name,
                "type": obj_type,
                "ddl": None,
                "source": full_source,
                "schema": owner,
            })

        return results
