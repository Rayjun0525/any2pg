from sqlalchemy import inspect, text
from .base import BaseDBAdapter

class DB2Adapter(BaseDBAdapter):
    def get_tables_and_views(self, schema: str = None) -> list[dict]:
        """
        DB2 테이블 및 뷰 정보 추출
        """
        inspector = inspect(self.engine)
        results = []
        
        # DB2에서 스키마 대문자 처리는 필수적인 경우가 많음
        target_schema = schema.upper() if schema else None
        
        # 1. Tables
        try:
            for t_name in inspector.get_table_names(schema=target_schema):
                columns = inspector.get_columns(t_name, schema=target_schema)
                col_defs = [f"  {col['name']} {col['type']}" for col in columns]
                ddl = f"CREATE TABLE {t_name} (\n" + ",\n".join(col_defs) + "\n);"
                
                results.append({"name": t_name, "type": "TABLE", "ddl": ddl, "source": None})
        except Exception as e:
            # 권한 문제 등으로 테이블 목록 조회 실패 시 로그 남기거나 pass
            print(f"Warning: Failed to fetch tables for schema {target_schema}: {e}")

        # 2. Views
        try:
            for v_name in inspector.get_view_names(schema=target_schema):
                # DB2는 View Definition을 바로 가져오기 까다로울 수 있어 SQL로 보완 가능
                # 여기서는 inspector 사용 시도
                v_def = inspector.get_view_definition(v_name, schema=target_schema)
                results.append({"name": v_name, "type": "VIEW", "ddl": None, "source": v_def})
        except Exception:
            pass
            
        return results

    def get_procedures(self, schema: str = None) -> list[dict]:
        """
        SYSCAT.ROUTINES를 조회하여 프로시저/함수 소스 추출
        """
        target_schema = schema.upper() if schema else None
        
        # DB2 시스템 카탈로그 쿼리
        # TEXT 컬럼에 소스 코드가 들어있음 (긴 경우 CLOB일 수 있으나 기본적으로 TEXT 조회)
        sql = """
        SELECT ROUTINENAME, ROUTINETYPE, TEXT
        FROM SYSCAT.ROUTINES
        WHERE OWNERSCHEM = :schema
          AND ROUTINETYPE IN ('P', 'F') -- P: Procedure, F: Function
        """
        
        # 스키마 미지정 시 현재 유저 기준 (CURRENT SCHEMA)
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
                    
                    # 타입 코드 변환
                    obj_type = 'PROCEDURE' if r_type_code == 'P' else 'FUNCTION'
                    
                    results.append({
                        "name": r_name,
                        "type": obj_type,
                        "ddl": None,
                        "source": r_source
                    })
        except Exception as e:
            print(f"Warning: Failed to fetch DB2 routines: {e}")
            
        return results