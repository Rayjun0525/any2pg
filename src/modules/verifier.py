import logging
from typing import Optional, Tuple

import psycopg

logger = logging.getLogger(__name__)


class VerifierAgent:
    def __init__(self, config: dict):
        """Create a verifier using the target DB configuration from config.yaml."""
        self.target_dsn = config["database"]["target"]["uri"]
        self.statement_timeout_ms = config["database"]["target"].get(
            "statement_timeout_ms"
        )

    def verify_sql(self, sql_script: str) -> Tuple[bool, Optional[str]]:
        """Simulate execution of converted SQL on the target database."""
        if not sql_script or not sql_script.strip():
            return False, "Empty SQL script"

        conn_args = {}
        if self.statement_timeout_ms:
            conn_args["options"] = f"-c statement_timeout={self.statement_timeout_ms}"

        try:
            with psycopg.connect(self.target_dsn, **conn_args) as conn:
                conn.autocommit = False
                with conn.cursor() as cur:
                    try:
                        cur.execute(sql_script)
                        logger.info("Verification passed (Transaction will be rolled back).")
                        return True, None
                    except psycopg.Error as db_err:
                        error_msg = f"DB Error: {db_err.diag.message_primary}"
                        if db_err.diag.context:
                            error_msg += f" | Context: {db_err.diag.context}"
                        logger.warning(f"Verification failed: {error_msg}")
                        return False, error_msg
                    finally:
                        conn.rollback()
        except Exception as e:
            logger.error(f"Verification system error: {e}")
            return False, str(e)
