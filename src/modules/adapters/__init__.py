# src/modules/adapters/__init__.py

from sqlalchemy import create_engine
from .oracle import OracleAdapter
from .mysql import MySQLAdapter
from .db2 import DB2Adapter
from .mssql import MSSQLAdapter
from .hana import HANAAdapter
from .snowflake import SnowflakeAdapter
from .mongodb import MongoDBAdapter

def get_adapter(db_config: dict):
    """
    db_config: config.yaml의 'database.source' 딕셔너리 전체
    """
    db_type = db_config['type'].lower()
    uri = db_config['uri']

    # 1. NoSQL (MongoDB) - SQLAlchemy를 쓰지 않음
    if db_type == "mongodb":
        return MongoDBAdapter(uri)

    # 2. RDBMS (SQLAlchemy Engine 공통 사용)
    engine = create_engine(uri)
    
    if db_type == "oracle":
        return OracleAdapter(engine)
    elif db_type in ["mysql", "mariadb"]:
        return MySQLAdapter(engine)
    elif db_type == "db2":
        return DB2Adapter(engine)
    elif db_type == "mssql":
        return MSSQLAdapter(engine)
    elif db_type == "hana":
        return HANAAdapter(engine)
    elif db_type == "snowflake":
        return SnowflakeAdapter(engine)
    else:
        raise ValueError(f"Unsupported Source DB Type: {db_type}")