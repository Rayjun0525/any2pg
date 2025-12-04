from sqlalchemy import inspect, text
from .base import BaseDBAdapter

class SnowflakeAdapter(BaseDBAdapter):
    def get_tables_and_views(self, schema: str = None) -> list[dict]:
        inspector = inspect(self.engine)
        target_schema = schema.upper() if schema else 'PUBLIC'
        results = []

        # 1. Tables
        for t_name in inspector.get_table_names(schema=target_schema):
            columns = inspector.get_columns(t_name, schema=target_schema)
            col_defs = [f"  {col['name']} {col['type']}" for col in columns]
            ddl = f"CREATE TABLE {t_name} (\n" + ",\n".join(col_defs) + "\n);"
            results.append({"name": t_name, "type": "TABLE", "ddl": ddl, "source": None})

        # 2. Views
        for v_name in inspector.get_view_names(schema=target_schema):
            # Snowflake Inspector가 view definition을 잘 지원함
            v_def = inspector.get_view_definition(v_name, schema=target_schema)
            results.append({"name": v_name, "type": "VIEW", "ddl": None, "source": v_def})
            
        return results

    def get_procedures(self, schema: str = None) -> list[dict]:
        target_schema = schema.upper() if schema else 'PUBLIC'
        
        # Snowflake는 SHOW FUNCTIONS / SHOW PROCEDURES 명령어를 써야 목록이 나옴
        # 하지만 SQL로 조회하려면 INFORMATION_SCHEMA를 써야 함
        sql = """
        SELECT ROUTINE_NAME, ROUTINE_TYPE
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_SCHEMA = :schema
        """
        
        results = []
        with self.engine.connect() as conn:
            # 1. 목록 조회
            rows = conn.execute(text(sql), {"schema": target_schema}).fetchall()
            
            # 2. 각 객체별 DDL 추출 (GET_DDL 함수 사용)
            for row in rows:
                r_name = row[0]
                r_type = row[1] # 'FUNCTION' or 'PROCEDURE'
                
                # GET_DDL('TYPE', 'NAME')
                ddl_query = f"SELECT GET_DDL('{r_type}', '{target_schema}.{r_name}')"
                try:
                    ddl_res = conn.execute(text(ddl_query)).scalar()
                    results.append({
                        "name": r_name,
                        "type": r_type,
                        "ddl": None,
                        "source": ddl_res
                    })
                except Exception:
                    # 권한 없음 등
                    continue
                    
        return results