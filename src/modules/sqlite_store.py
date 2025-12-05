import hashlib
import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterable, List, Optional

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

        source_assets_ddl = """
        CREATE TABLE IF NOT EXISTS source_assets (
            asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            sql_text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            parsed_schemas TEXT,
            selected_for_port INTEGER DEFAULT 1,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_name, file_path)
        );
        """

        rendered_outputs_ddl = """
        CREATE TABLE IF NOT EXISTS rendered_outputs (
            output_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            asset_id INTEGER,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            sql_text TEXT,
            content_hash TEXT,
            source_hash TEXT,
            status TEXT,
            verified INTEGER DEFAULT 0,
            last_error TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_name, file_path)
        );
        """

        execution_logs_ddl = """
        CREATE TABLE IF NOT EXISTS execution_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            level TEXT NOT NULL,
            event TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                cursor.execute(source_assets_ddl)
                cursor.execute(rendered_outputs_ddl)
                cursor.execute(execution_logs_ddl)

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

                self._ensure_column(
                    cursor,
                    "source_assets",
                    "selected_for_port",
                    "ALTER TABLE source_assets ADD COLUMN selected_for_port INTEGER DEFAULT 1",
                )
                self._ensure_column(
                    cursor,
                    "source_assets",
                    "parsed_schemas",
                    "ALTER TABLE source_assets ADD COLUMN parsed_schemas TEXT",
                )
                self._ensure_column(
                    cursor,
                    "source_assets",
                    "notes",
                    "ALTER TABLE source_assets ADD COLUMN notes TEXT",
                )

                self._ensure_column(
                    cursor,
                    "rendered_outputs",
                    "source_hash",
                    "ALTER TABLE rendered_outputs ADD COLUMN source_hash TEXT",
                )
                self._ensure_column(
                    cursor,
                    "rendered_outputs",
                    "verified",
                    "ALTER TABLE rendered_outputs ADD COLUMN verified INTEGER DEFAULT 0",
                )
                self._ensure_column(
                    cursor,
                    "rendered_outputs",
                    "last_error",
                    "ALTER TABLE rendered_outputs ADD COLUMN last_error TEXT",
                )

                self._ensure_column(
                    cursor,
                    "execution_logs",
                    "project_name",
                    "ALTER TABLE execution_logs ADD COLUMN project_name TEXT DEFAULT 'default'",
                )
                self._ensure_column(
                    cursor,
                    "execution_logs",
                    "level",
                    "ALTER TABLE execution_logs ADD COLUMN level TEXT DEFAULT 'INFO'",
                )
                self._ensure_column(
                    cursor,
                    "execution_logs",
                    "event",
                    "ALTER TABLE execution_logs ADD COLUMN event TEXT",
                )

            logger.info(f"Database initialized successfully at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    # --- Source asset helpers -------------------------------------------------

    def _hash_sql(self, sql_text: str) -> str:
        return hashlib.sha256(sql_text.encode("utf-8")).hexdigest()

    def sync_source_asset(
        self,
        file_path: str,
        sql_text: str,
        parsed_schemas: Optional[str] = None,
        selected_for_port: bool = True,
        override_selection: bool = False,
    ) -> None:
        content_hash = self._hash_sql(sql_text)
        file_name = os.path.basename(file_path)
        selected_flag = 1 if selected_for_port else 0
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO source_assets (
                    project_name, file_name, file_path, sql_text, content_hash, parsed_schemas, selected_for_port, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_name, file_path) DO UPDATE SET
                    sql_text = excluded.sql_text,
                    content_hash = excluded.content_hash,
                    parsed_schemas = COALESCE(excluded.parsed_schemas, source_assets.parsed_schemas),
                    selected_for_port = CASE WHEN ? THEN excluded.selected_for_port ELSE source_assets.selected_for_port END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    self.project_name,
                    file_name,
                    file_path,
                    sql_text,
                    content_hash,
                    parsed_schemas,
                    selected_flag,
                    1 if override_selection else 0,
                ),
            )

    def list_source_assets(
        self,
        only_selected: bool = False,
        only_changed: bool = False,
    ) -> List[sqlite3.Row]:
        base_sql = [
            """
            SELECT sa.*, ro.source_hash, ro.status AS last_status, ml.status AS log_status
            FROM source_assets sa
            LEFT JOIN rendered_outputs ro
              ON sa.project_name = ro.project_name AND sa.file_path = ro.file_path
            LEFT JOIN migration_logs ml
              ON sa.project_name = ml.project_name AND sa.file_path = ml.file_path
            WHERE sa.project_name = ?
            """
        ]
        params: List = [self.project_name]
        if only_selected:
            base_sql.append("AND sa.selected_for_port = 1")
        if only_changed:
            base_sql.append(
                "AND (ro.source_hash IS NULL OR ro.source_hash != sa.content_hash)"
            )
        sql = "\n".join(base_sql) + "\nORDER BY sa.file_name"
        with self.get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def set_selection(self, file_names: Iterable[str], selected: bool = True) -> int:
        names = list(file_names)
        if not names:
            return 0
        placeholders = ",".join(["?"] * len(names))
        sql = f"""
            UPDATE source_assets
            SET selected_for_port = ?, updated_at = CURRENT_TIMESTAMP
            WHERE project_name = ? AND file_name IN ({placeholders})
        """
        with self.get_cursor(commit=True) as cur:
            cur.execute(sql, [1 if selected else 0, self.project_name, *names])
            return cur.rowcount

    # --- Output helpers -------------------------------------------------------

    def save_rendered_output(
        self,
        file_path: str,
        sql_text: str,
        source_hash: Optional[str] = None,
        status: Optional[str] = None,
        verified: bool = False,
        last_error: Optional[str] = None,
    ) -> None:
        content_hash = self._hash_sql(sql_text) if sql_text else None
        file_name = os.path.basename(file_path)
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO rendered_outputs (
                    project_name, file_name, file_path, sql_text, content_hash, source_hash, status, verified, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_name, file_path) DO UPDATE SET
                    sql_text = excluded.sql_text,
                    content_hash = excluded.content_hash,
                    source_hash = excluded.source_hash,
                    status = excluded.status,
                    verified = excluded.verified,
                    last_error = excluded.last_error,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    self.project_name,
                    file_name,
                    file_path,
                    sql_text,
                    content_hash,
                    source_hash,
                    status,
                    1 if verified else 0,
                    last_error,
                ),
            )

    def fetch_rendered_sql(self, file_names: Optional[Iterable[str]] = None) -> List[sqlite3.Row]:
        params: List = [self.project_name]
        base_sql = "SELECT * FROM rendered_outputs WHERE project_name = ?"
        if file_names:
            names = list(file_names)
            placeholders = ",".join(["?"] * len(names))
            base_sql += f" AND file_name IN ({placeholders})"
            params.extend(names)
        base_sql += " ORDER BY file_name"
        with self.get_cursor() as cur:
            cur.execute(base_sql, params)
            return cur.fetchall()

    def list_rendered_outputs(self, limit: int = 100) -> List[sqlite3.Row]:
        sql = (
            "SELECT file_name, status, verified, updated_at "
            "FROM rendered_outputs "
            "WHERE project_name = ? "
            "ORDER BY updated_at DESC LIMIT ?"
        )
        with self.get_cursor() as cur:
            cur.execute(sql, (self.project_name, limit))
            return cur.fetchall()

    # --- Metadata navigation helpers ----------------------------------------

    def list_schemas(self) -> List[sqlite3.Row]:
        sql = (
            "SELECT DISTINCT schema_name FROM schema_objects WHERE project_name = ? "
            "ORDER BY schema_name"
        )
        with self.get_cursor() as cur:
            cur.execute(sql, (self.project_name,))
            return cur.fetchall()

    def list_schema_objects(self, schema: Optional[str] = None) -> List[sqlite3.Row]:
        base_sql = [
            "SELECT schema_name, obj_name, obj_type, extracted_at FROM schema_objects",
            "WHERE project_name = ?",
        ]
        params: List = [self.project_name]
        if schema:
            base_sql.append("AND schema_name = ?")
            params.append(schema)
        base_sql.append("ORDER BY schema_name, obj_type, obj_name")
        with self.get_cursor() as cur:
            cur.execute("\n".join(base_sql), params)
            return cur.fetchall()

    def get_object_detail(
        self, schema: str, obj_name: str, obj_type: Optional[str] = None
    ) -> Optional[sqlite3.Row]:
        base_sql = [
            "SELECT schema_name, obj_name, obj_type, ddl_script, source_code, extracted_at",
            "FROM schema_objects",
            "WHERE project_name = ? AND schema_name = ? AND obj_name = ?",
        ]
        params: List = [self.project_name, schema, obj_name]
        if obj_type:
            base_sql.append("AND obj_type = ?")
            params.append(obj_type)
        base_sql.append("ORDER BY obj_type LIMIT 1")
        with self.get_cursor() as cur:
            cur.execute("\n".join(base_sql), params)
            return cur.fetchone()

    # --- Execution log helpers ----------------------------------------------

    def add_execution_log(self, event: str, detail: Optional[str] = None, level: str = "INFO") -> None:
        with self.get_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO execution_logs (project_name, level, event, detail)
                VALUES (?, ?, ?, ?)
                """,
                (self.project_name, level.upper(), event, detail),
            )

    def fetch_execution_logs(
        self, limit: int = 200, level: Optional[str] = None
    ) -> List[sqlite3.Row]:
        base_sql = [
            "SELECT project_name, level, event, detail, created_at",
            "FROM execution_logs",
            "WHERE project_name = ?",
        ]
        params: List = [self.project_name]
        if level:
            base_sql.append("AND level = ?")
            params.append(level.upper())
        base_sql.append("ORDER BY log_id DESC LIMIT ?")
        params.append(limit)
        with self.get_cursor() as cur:
            cur.execute("\n".join(base_sql), params)
            return cur.fetchall()

    def summarize_migration(self) -> List[sqlite3.Row]:
        with self.get_cursor() as cur:
            cur.execute(
                """
                SELECT status, COUNT(*) as count
                FROM migration_logs
                WHERE project_name = ?
                GROUP BY status
                ORDER BY status
                """,
                (self.project_name,),
            )
            return cur.fetchall()
