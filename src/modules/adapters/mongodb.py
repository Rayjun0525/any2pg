from pymongo import MongoClient

from .base import BaseDBAdapter


class MongoDBAdapter(BaseDBAdapter):
    def __init__(self, uri: str):
        self.client = MongoClient(uri)

    def get_tables_and_views(self, schema: str | None = None) -> list[dict]:
        results = []
        db = self.client.get_default_database()
        collection_names = db.list_collection_names()
        for name in collection_names:
            results.append({"name": name, "type": "TABLE", "ddl": None, "source": None})
        return results

    def get_procedures(self, schema: str | None = None) -> list[dict]:
        return []
