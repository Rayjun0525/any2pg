from abc import ABC, abstractmethod
from typing import List, Dict, Any
from sqlalchemy.engine import Engine

class BaseDBAdapter(ABC):
    def __init__(self, engine: Engine):
        self.engine = engine

    @abstractmethod
    def get_tables_and_views(self, schema: str = None) -> List[Dict[str, Any]]:
        """
        테이블과 뷰 스키마 정보 추출
        :return: [{'name': str, 'type': 'TABLE'|'VIEW', 'ddl': str, 'source': str}, ...]
        """
        pass

    @abstractmethod
    def get_procedures(self, schema: str = None) -> List[Dict[str, Any]]:
        """
        프로시저, 함수 소스 코드 추출
        :return: [{'name': str, 'type': 'PROCEDURE'|'FUNCTION', 'ddl': None, 'source': str}, ...]
        """
        pass