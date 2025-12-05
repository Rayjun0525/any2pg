import logging
from dataclasses import dataclass, field
from typing import List, Optional

import psycopg
import sqlglot
from urllib.parse import urlsplit, urlunsplit
from sqlglot import exp
from sqlglot.errors import ParseError

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    success: bool
    error: Optional[str]
    skipped_statements: List[str] = field(default_factory=list)
    executed_statements: int = 0
    notes: Optional[str] = None


class VerifierAgent:
    def __init__(self, config: dict):
        """Create a verifier using the target DB configuration from config.yaml."""
        self.target_dsn = config["database"]["target"]["uri"]
        target_conf = config["database"]["target"]
        verification_conf = config.get("verification", {})
        self.statement_timeout_ms = target_conf.get("statement_timeout_ms")
        self.allow_dangerous = verification_conf.get("allow_dangerous_statements", False)
        self.allow_procedures = verification_conf.get("allow_procedure_execution", False)

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

    def verify_sql(self, sql_script: str) -> VerificationResult:
        """Simulate execution of converted SQL on the target database."""
        if not sql_script or not sql_script.strip():
            return VerificationResult(False, "Empty SQL script")

        executable, skipped, prep_error = self._prepare_statements(sql_script)
        if prep_error:
            return VerificationResult(False, prep_error)
        if not executable and skipped:
            note = (
                "All statements skipped due to verification safety settings; "
                "functional data checks must be done manually."
            )
            return VerificationResult(True, None, skipped_statements=skipped, notes=note)

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
                    executed = 0
                    try:
                        cur.execute("BEGIN")
                        for idx, statement in enumerate(executable, start=1):
                            failing_idx = idx
                            failing_stmt = statement
                            logger.debug("Executing statement %d: %s", idx, statement)
                            cur.execute(statement)
                            executed += 1
                        logger.info(
                            "Verification passed (Transaction will be rolled back). Executed %d statements; skipped %d.",
                            executed,
                            len(skipped),
                        )
                        return VerificationResult(
                            True,
                            None,
                            skipped_statements=skipped,
                            executed_statements=executed,
                            notes="Data parity is not validated; please run your own comparisons.",
                        )
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
                        return VerificationResult(
                            False,
                            error_msg,
                            skipped_statements=skipped,
                            executed_statements=executed,
                        )
                    finally:
                        conn.rollback()
        except Exception as e:
            logger.error(f"Verification system error: {e}")
            return VerificationResult(False, str(e))

    def apply_sql(self, sql_script: str) -> VerificationResult:
        """Execute converted SQL on the target database with safety filters."""

        if not sql_script or not sql_script.strip():
            return VerificationResult(False, "Empty SQL script")

        executable, skipped, prep_error = self._prepare_statements(sql_script)
        if prep_error:
            return VerificationResult(False, prep_error)
        if not executable:
            note = "No executable statements after safety filtering"
            return VerificationResult(True, None, skipped_statements=skipped, notes=note)

        conn_args = {}
        if self.statement_timeout_ms:
            conn_args["options"] = f"-c statement_timeout={self.statement_timeout_ms}"

        logger.info(
            "Applying SQL to target (uri=%s, timeout_ms=%s, statements=%d, skipped=%d)",
            self._redact_dsn(self.target_dsn),
            self.statement_timeout_ms,
            len(executable),
            len(skipped),
        )

        try:
            with psycopg.connect(self.target_dsn, **conn_args) as conn:
                with conn.cursor() as cur:
                    executed = 0
                    failing_stmt = None
                    try:
                        for statement in executable:
                            failing_stmt = statement
                            cur.execute(statement)
                            executed += 1
                        conn.commit()
                        return VerificationResult(
                            True,
                            None,
                            skipped_statements=skipped,
                            executed_statements=executed,
                            notes="Statements applied to target database.",
                        )
                    except psycopg.Error as db_err:
                        conn.rollback()
                        diag = getattr(db_err, "diag", None)
                        primary = getattr(diag, "message_primary", None) if diag else None
                        context = getattr(diag, "context", None) if diag else None
                        error_msg = primary or str(db_err) or "Unknown database error"
                        if context:
                            error_msg += f" | Context: {context}"
                        if failing_stmt:
                            error_msg += f" | SQL: {failing_stmt}"
                        logger.warning("Target apply failed: %s", error_msg)
                        return VerificationResult(
                            False,
                            error_msg,
                            skipped_statements=skipped,
                            executed_statements=executed,
                        )
        except Exception as exc:
            logger.error("Execution error: %s", exc)
            return VerificationResult(False, str(exc))

    def _split_statements(self, sql_script: str) -> list[str]:
        try:
            parsed = sqlglot.parse(sql_script, read="postgres")
        except ParseError as parse_error:
            raise ValueError(f"SQL parse error: {parse_error}") from parse_error

        statements = [stmt.sql(dialect="postgres") for stmt in parsed if stmt]
        if not statements:
            raise ValueError("No executable statements found in SQL script")
        return statements

    def _classify_statement(self, stmt: sqlglot.Expression) -> str:
        """Return classification: safe, dangerous, or procedure."""

        dangerous_nodes = tuple(
            node
            for node in (
                getattr(exp, "Create", None),
                getattr(exp, "Alter", None),
                getattr(exp, "Drop", None),
                getattr(exp, "Insert", None),
                getattr(exp, "Update", None),
                getattr(exp, "Delete", None),
                getattr(exp, "Merge", None),
                getattr(exp, "Command", None),
            )
            if node
        )

        procedure_nodes = tuple(
            node for node in (getattr(exp, "Call", None), getattr(exp, "Procedure", None)) if node
        )
        if procedure_nodes and isinstance(stmt, procedure_nodes):
            return "procedure"

        if isinstance(stmt, dangerous_nodes):
            # Treat DO blocks and raw commands as procedures when they encapsulate code
            if isinstance(stmt, exp.Command):
                token = (stmt.this.sql().upper() if getattr(stmt, "this", None) else "")
                if token in {"CALL", "EXEC", "EXECUTE", "DO"}:
                    return "procedure"
            return "dangerous"

        leading = stmt.sql(dialect="postgres").strip().upper()
        if leading.startswith(("CALL ", "EXEC ", "EXECUTE ", "DO ")):
            return "procedure"

        if leading.startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE", "DROP", "ALTER", "CREATE")):
            return "dangerous"

        return "safe"

    def _prepare_statements(self, sql_script: str):
        try:
            statements = self._split_statements(sql_script)
            parsed = sqlglot.parse(sql_script, read="postgres")
        except ValueError as parse_err:
            logger.warning("Unable to split SQL script: %s", parse_err)
            return [], [], str(parse_err)

        executable: list[str] = []
        skipped: list[str] = []
        for raw, stmt in zip(statements, parsed):
            classification = self._classify_statement(stmt)
            if classification == "procedure" and not self.allow_procedures:
                skipped.append(raw)
                continue
            if classification == "dangerous" and not self.allow_dangerous:
                skipped.append(raw)
                continue
            executable.append(raw)

        return executable, skipped, None
