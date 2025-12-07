# src/modules/adapters/__init__.py

from sqlalchemy import create_engine

from .db2 import DB2Adapter
from .hana import HANAAdapter
from .mssql import MSSQLAdapter
from .mysql import MySQLAdapter
from .oracle import OracleAdapter
from .snowflake import SnowflakeAdapter

# MongoDB is optional
try:
    from .mongodb import MongoDBAdapter
except ImportError:
    MongoDBAdapter = None


def get_adapter(db_config: dict):
    """Return an adapter instance based on the configured source database."""
    db_type = db_config["type"].lower()
    uri = db_config["uri"]

    if db_type == "mongodb":
        if MongoDBAdapter is None:
            raise ValueError("MongoDB support requires 'pymongo' package. Install with: pip install pymongo")
        return MongoDBAdapter(uri)

    engine = create_engine(uri)

    if db_type == "oracle":
        return OracleAdapter(engine)
    if db_type in ["mysql", "mariadb"]:
        return MySQLAdapter(engine)
    if db_type == "db2":
        return DB2Adapter(engine)
    if db_type == "mssql":
        return MSSQLAdapter(engine)
    if db_type == "hana":
        return HANAAdapter(engine)
    if db_type == "snowflake":
        return SnowflakeAdapter(engine)

    raise ValueError(f"Unsupported Source DB Type: {db_type}")
