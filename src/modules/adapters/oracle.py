from sqlalchemy import inspect, text
from .base import BaseDBAdapter

class OracleAdapter(BaseDBAdapter):
    def get_tables_and_views(self, schema: str = None) -> list[dict]:
        inspector = inspect(self.engine)
        results = []
        
        # 1. Tables
        # schema가 None이면 default schema 사용
        for t_name in inspector.get_table_names(schema=schema):
            columns = inspector.get_columns(t_name, schema=schema)
            # LLM 참고용 간이 DDL 생성
            col_defs = [f"  {col['name']} {col['type']}" for col in columns]
            ddl = f"CREATE TABLE {t_name} (\n" + ",\n".join(col_defs) + "\n);"
            
            results.append({"name": t_name, "type": "TABLE", "ddl": ddl, "source": None})
            
        # 2. Views
        for v_name in inspector.get_view_names(schema=schema):
            # Oracle은 view_definition이 쿼리문 자체
            v_def = inspector.get_view_definition(v_name, schema=schema)
            results.append({"name": v_name, "type": "VIEW", "ddl": None, "source": v_def})
            
        return results

    def get_procedures(self, schema: str = None) -> list[dict]:
        # Oracle은 USER_SOURCE (현재 유저) 혹은 ALL_SOURCE (권한 있는 모든 유저)
        # 여기서는 편의상 USER_SOURCE 기준
        sql = """
        SELECT NAME, TYPE, LINE, TEXT 
        FROM USER_SOURCE 
        WHERE TYPE IN ('PROCEDURE', 'FUNCTION', 'PACKAGE', 'PACKAGE BODY')
        ORDER BY NAME, TYPE, LINE
        """
        
        objects_map = {}
        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            for row in result:
                name, obj_type, line, txt = row[0], row[1], row[2], row[3]
                key = (name, obj_type)
                if key not in objects_map:
                    objects_map[key] = []
                objects_map[key].append(txt)

        results = []
        for (name, obj_type), lines in objects_map.items():
            full_source = "".join(lines)
            results.append({
                "name": name, 
                "type": obj_type, 
                "ddl": None, 
                "source": full_source
            })
            
        return results