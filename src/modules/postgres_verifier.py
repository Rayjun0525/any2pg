import logging
from typing import Optional, Tuple

import psycopg
import sqlglot
from urllib.parse import urlsplit, urlunsplit
from sqlglot.errors import ParseError

logger = logging.getLogger(__name__)


class VerifierAgent:
    def __init__(self, config: dict):
        """Create a verifier using the target DB configuration from config.yaml."""
        self.target_dsn = config["database"]["target"]["uri"]
        self.statement_timeout_ms = config["database"]["target"].get(
            "statement_timeout_ms"
        )

    @staticmethod
    def _redact_dsn(dsn: str) -> str:
        try:
            parts = urlsplit(dsn)
            if parts.username:
                safe_netloc = parts.hostname or ""
                if parts.port:
                    safe_netloc = f"{safe_netloc}:{parts.port}"
                parts = parts._replace(netloc=safe_netloc)
            return urlunsplit(parts)
        except Exception:
            return "<redacted>"

    def verify_sql(self, sql_script: str) -> Tuple[bool, Optional[str]]:
        """Simulate execution of converted SQL on the target database."""
        if not sql_script or not sql_script.strip():
            return False, "Empty SQL script"

        try:
            statements = self._split_statements(sql_script)
        except ValueError as parse_err:
            logger.warning("Unable to split SQL script: %s", parse_err)
            return False, str(parse_err)

        conn_args = {}
        if self.statement_timeout_ms:
            conn_args["options"] = f"-c statement_timeout={self.statement_timeout_ms}"

        logger.info(
            "Verifier connecting to target (uri=%s, timeout_ms=%s)",
            self._redact_dsn(self.target_dsn),
            self.statement_timeout_ms,
        )

        try:
            with psycopg.connect(self.target_dsn, **conn_args) as conn:
                conn.autocommit = False
                with conn.cursor() as cur:
                    failing_idx = None
                    failing_stmt = None
                    try:
                        for idx, statement in enumerate(statements, start=1):
                            failing_idx = idx
                            failing_stmt = statement
                            logger.debug("Executing statement %d: %s", idx, statement)
                            cur.execute(statement)
                        logger.info("Verification passed (Transaction will be rolled back).")
                        return True, None
                    except psycopg.Error as db_err:
                        diag = getattr(db_err, "diag", None)
                        primary = getattr(diag, "message_primary", None) if diag else None
                        context = getattr(diag, "context", None) if diag else None

                        fallback_message = str(db_err) or "Unknown database error"
                        error_msg = (
                            f"Statement #{failing_idx} failed: {primary or fallback_message}"
                        )
                        if context:
                            error_msg += f" | Context: {context}"
                        if failing_stmt:
                            error_msg += f" | SQL: {failing_stmt}"
                        logger.warning("Verification failed: %s", error_msg)
                        return False, error_msg
                    finally:
                        conn.rollback()
        except Exception as e:
            logger.error(f"Verification system error: {e}")
            return False, str(e)

    def _split_statements(self, sql_script: str) -> list[str]:
        try:
            parsed = sqlglot.parse(sql_script, read="postgres")
        except ParseError as parse_error:
            raise ValueError(f"SQL parse error: {parse_error}") from parse_error

        statements = [stmt.sql(dialect="postgres") for stmt in parsed if stmt]
        if not statements:
            raise ValueError("No executable statements found in SQL script")
        return statements
