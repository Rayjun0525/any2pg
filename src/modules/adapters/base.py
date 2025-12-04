from abc import ABC, abstractmethod
from typing import Any, Dict, List

from sqlalchemy.engine import Engine


class BaseDBAdapter(ABC):
    def __init__(self, engine: Engine):
        self.engine = engine

    @abstractmethod
    def get_tables_and_views(self, schema: str | None = None) -> List[Dict[str, Any]]:
        """Extract table and view metadata."""

    @abstractmethod
    def get_procedures(self, schema: str | None = None) -> List[Dict[str, Any]]:
        """Extract stored procedure and function metadata."""
