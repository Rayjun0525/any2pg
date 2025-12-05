import logging
import sqlite3
from contextlib import contextmanager

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DBManager:
    def __init__(self, db_path: str, project_name: str | None = None):
        self.db_path = db_path
        self.project_name = project_name or "default"

    def get_connection(self):
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as e:
            logger.error(f"DB Connection failed: {e}")
            raise

    @contextmanager
    def get_cursor(self, commit: bool = False):
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

    def _ensure_column(self, cursor, table: str, column: str, ddl: str):
        cursor.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cursor.fetchall()}
        if column not in columns:
            cursor.execute(ddl)

    def init_db(self):
        """Initialize tables and indexes for schema metadata and migration logs."""
        schema_objects_ddl = """
        CREATE TABLE IF NOT EXISTS schema_objects (
            obj_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            schema_name TEXT NOT NULL,
            obj_name TEXT NOT NULL,
            obj_type TEXT NOT NULL,
            ddl_script TEXT,
            source_code TEXT,
            extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_name, schema_name, obj_name, obj_type)
        );
        """

        idx_schema_name = "CREATE INDEX IF NOT EXISTS idx_schema_name ON schema_objects(obj_name);"
        idx_schema_project = """
            CREATE INDEX IF NOT EXISTS idx_schema_project
            ON schema_objects(project_name, schema_name)
        """
        idx_schema_project_unique = """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_schema_project_obj
            ON schema_objects(project_name, schema_name, obj_name, obj_type)
        """

        migration_logs_ddl = """
        CREATE TABLE IF NOT EXISTS migration_logs (
            project_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            detected_schemas TEXT,
            status TEXT NOT NULL,
            retry_count INTEGER DEFAULT 0,
            last_error_msg TEXT,
            target_path TEXT,
            skipped_statements TEXT,
            executed_statements INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_name, file_path)
        );
        """

        idx_log_status = "CREATE INDEX IF NOT EXISTS idx_log_status ON migration_logs(status);"
        idx_log_project = """
            CREATE INDEX IF NOT EXISTS idx_log_project
            ON migration_logs(project_name, status)
        """
        idx_log_project_file = """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_log_project_file
            ON migration_logs(project_name, file_path)
        """

        try:
            with self.get_cursor(commit=True) as cursor:
                cursor.execute(schema_objects_ddl)
                cursor.execute(idx_schema_name)
                cursor.execute(idx_schema_project)
                cursor.execute(idx_schema_project_unique)
                cursor.execute(migration_logs_ddl)
                cursor.execute(idx_log_status)
                cursor.execute(idx_log_project)
                cursor.execute(idx_log_project_file)

                # Add missing columns for existing installations
                self._ensure_column(
                    cursor,
                    "schema_objects",
                    "project_name",
                    "ALTER TABLE schema_objects ADD COLUMN project_name TEXT DEFAULT 'default'",
                )
                self._ensure_column(
                    cursor,
                    "migration_logs",
                    "project_name",
                    "ALTER TABLE migration_logs ADD COLUMN project_name TEXT DEFAULT 'default'",
                )
                self._ensure_column(
                    cursor,
                    "migration_logs",
                    "detected_schemas",
                    "ALTER TABLE migration_logs ADD COLUMN detected_schemas TEXT",
                )
                self._ensure_column(
                    cursor,
                    "migration_logs",
                    "skipped_statements",
                    "ALTER TABLE migration_logs ADD COLUMN skipped_statements TEXT",
                )
                self._ensure_column(
                    cursor,
                    "migration_logs",
                    "executed_statements",
                    "ALTER TABLE migration_logs ADD COLUMN executed_statements INTEGER DEFAULT 0",
                )

            logger.info(f"Database initialized successfully at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise
