from sqlalchemy import inspect, text
from .base import BaseDBAdapter

class HANAAdapter(BaseDBAdapter):
    def get_tables_and_views(self, schema: str = None) -> list[dict]:
        inspector = inspect(self.engine)
        # HANA는 스키마 대문자 필수
        target_schema = schema.upper() if schema else None 
        results = []

        # Tables (HANA Inspector 활용)
        try:
            for t_name in inspector.get_table_names(schema=target_schema):
                columns = inspector.get_columns(t_name, schema=target_schema)
                col_defs = [f"  {col['name']} {col['type']}" for col in columns]
                ddl = f"CREATE TABLE {t_name} (\n" + ",\n".join(col_defs) + "\n);"
                results.append({"name": t_name, "type": "TABLE", "ddl": ddl, "source": None})
        except Exception: 
            pass # 권한 문제 등 예외 처리

        # Views
        try:
            for v_name in inspector.get_view_names(schema=target_schema):
                # HANA는 View Definition 조회가 까다로울 수 있어 SQL로 대체 가능하나 시도
                v_def = inspector.get_view_definition(v_name, schema=target_schema)
                results.append({"name": v_name, "type": "VIEW", "ddl": None, "source": v_def})
        except Exception:
            pass
            
        return results

    def get_procedures(self, schema: str = None) -> list[dict]:
        target_schema = schema.upper() if schema else 'SYSTEM'
        
        # HANA Procedure/Function 통합 조회
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
                results.append({
                    "name": row[0],
                    "type": row[1],
                    "ddl": None,
                    "source": row[2] # CLOB 타입일 수 있음
                })
        return results