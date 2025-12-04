import pymongo
from typing import List, Dict, Any
from .base import BaseDBAdapter

class MongoDBAdapter: 
    # BaseDBAdapter를 상속받지 않음 (SQLAlchemy Engine이 없으므로 Duck Typing)
    
    def __init__(self, uri: str):
        self.client = pymongo.MongoClient(uri)
        # URI에서 DB명 파싱하거나, 기본 DB 사용
        self.db = self.client.get_database() 

    def get_tables_and_views(self, schema: str = None) -> List[Dict[str, Any]]:
        """
        MongoDB는 'Table' 대신 'Collection'을 조회.
        Schema는 없으므로 schema 인자는 무시하거나 DB명으로 대체 가능.
        """
        results = []
        collections = self.db.list_collection_names()
        
        for col_name in collections:
            # system 컬렉션 제외
            if col_name.startswith("system."):
                continue
                
            # 스키마 추론: 첫 번째 문서를 샘플링
            sample_doc = self.db[col_name].find_one()
            
            # JSON 구조를 가상의 CREATE TABLE 문으로 변환 (LLM 이해용)
            pseudo_ddl = f"-- [MongoDB Collection: {col_name}]\n"
            pseudo_ddl += f"-- Sample Document Structure (Inferred):\n"
            pseudo_ddl += f"CREATE TABLE {col_name} (\n"
            
            if sample_doc:
                cols = []
                for k, v in sample_doc.items():
                    # 파이썬 타입을 SQL 타입 이름으로 매핑 (간이)
                    py_type = type(v).__name__
                    sql_type = "TEXT"
                    if py_type == "int": sql_type = "INTEGER"
                    elif py_type == "float": sql_type = "FLOAT"
                    elif py_type == "bool": sql_type = "BOOLEAN"
                    elif py_type == "dict": sql_type = "JSONB" # 중첩 객체
                    elif py_type == "list": sql_type = "ARRAY"
                    
                    cols.append(f"  {k} {sql_type}")
                pseudo_ddl += ",\n".join(cols)
            else:
                pseudo_ddl += "  -- Empty Collection"
                
            pseudo_ddl += "\n);"
            
            results.append({
                "name": col_name,
                "type": "COLLECTION", # TABLE 대신 COLLECTION 표기
                "ddl": pseudo_ddl,
                "source": None
            })
            
        return results

    def get_procedures(self, schema: str = None) -> List[Dict[str, Any]]:
        """
        MongoDB의 Stored Javascript (system.js) 조회
        """
        results = []
        try:
            # system.js 컬렉션 조회 (Deprecated 되었으나 레거시에는 존재 가능)
            system_js = self.db.get_collection("system.js")
            if system_js:
                for func in system_js.find():
                    f_name = func.get("_id")
                    f_code = str(func.get("value"))
                    
                    results.append({
                        "name": f_name,
                        "type": "FUNCTION", # JS Function
                        "ddl": None,
                        "source": f_code
                    })
        except Exception:
            pass
            
        return results