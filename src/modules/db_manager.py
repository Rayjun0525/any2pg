import sqlite3
import logging
import os
from contextlib import contextmanager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DBManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_connection(self):
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row 
            return conn
        except sqlite3.Error as e:
            logger.error(f"DB Connection failed: {e}")
            raise

    @contextmanager
    def get_cursor(self, commit=False):
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            if commit:
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"DB Operation failed: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def init_db(self):
        """시스템 초기화: 스키마 컬럼 추가 및 복합키 설정"""
        schema_objects_ddl = """
        CREATE TABLE IF NOT EXISTS schema_objects (
            obj_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_name TEXT NOT NULL,    -- [추가] 스키마 구분용
            obj_name TEXT NOT NULL,
            obj_type TEXT NOT NULL,
            ddl_script TEXT,
            source_code TEXT,
            extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            -- [변경] 스키마명까지 포함하여 유니크 제약 설정
            UNIQUE(schema_name, obj_name, obj_type)
        );
        """

        idx_schema_name = "CREATE INDEX IF NOT EXISTS idx_schema_name ON schema_objects(obj_name);"

        migration_logs_ddl = """
        CREATE TABLE IF NOT EXISTS migration_logs (
            file_path TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            retry_count INTEGER DEFAULT 0,
            last_error_msg TEXT,
            target_path TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """

        idx_log_status = "CREATE INDEX IF NOT EXISTS idx_log_status ON migration_logs(status);"

        try:
            with self.get_cursor(commit=True) as cursor:
                cursor.execute(schema_objects_ddl)
                cursor.execute(idx_schema_name)
                cursor.execute(migration_logs_ddl)
                cursor.execute(idx_log_status)
                
            logger.info(f"Database initialized successfully at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise