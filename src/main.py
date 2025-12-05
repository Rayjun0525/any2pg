import os
import sys
import yaml
import argparse
import logging
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit
from tqdm import tqdm

from modules.sqlite_store import DBManager
from modules.metadata_extractor import MetadataExtractor
from modules.context_builder import ContextResult, RAGContextBuilder
from modules.postgres_verifier import VerifierAgent
from agents.workflow import MigrationWorkflow

logger = logging.getLogger("Any2PG")
logger.addHandler(logging.NullHandler())


def redact_dsn(dsn: str) -> str:
    """Hide credentials inside a connection string for safe logging."""
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


def expand_path(path: str) -> str:
    """Expand environment variables and user-home shorthand inside a path."""

    return os.path.expanduser(os.path.expandvars(path))


def load_config(path="config.yaml"):
    if not os.path.exists(path):
        print(f"Config file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def validate_config(config: dict) -> dict:
    """Validate required config keys and normalize paths for downstream use."""

    required_sections = ("project", "database", "llm")
    for section in required_sections:
        if section not in config or not isinstance(config[section], dict):
            raise ValueError(f"Missing required config section: {section}")

    project = config["project"]
    for key in ("name", "source_dir", "target_dir", "db_file", "max_retries"):
        if key not in project:
            raise ValueError(f"project.{key} is required")

    try:
        project["max_retries"] = int(project["max_retries"])
    except (TypeError, ValueError) as exc:
        raise ValueError("project.max_retries must be an integer") from exc

    if project["max_retries"] < 1:
        raise ValueError("project.max_retries must be >= 1")

    project["name"] = str(project["name"]).strip()
    if not project["name"]:
        raise ValueError("project.name must be a non-empty string")

    project["source_dir"] = expand_path(project["source_dir"])
    project["target_dir"] = expand_path(project["target_dir"])
    project["db_file"] = expand_path(project["db_file"])

    database = config["database"]
    for side in ("source", "target"):
        if side not in database:
            raise ValueError(f"database.{side} configuration is required")
        if "uri" not in database[side]:
            raise ValueError(f"database.{side}.uri is required")

    logging_conf = config.setdefault("logging", {})
    logging_conf["file"] = expand_path(logging_conf.get("file", ""))

    verification_conf = config.setdefault("verification", {})
    verification_conf.setdefault("allow_dangerous_statements", False)
    verification_conf.setdefault("allow_procedure_execution", False)
    verification_conf.setdefault("mode", "port")

    return config


def configure_logging(config: dict):
    logging_conf = config.get("logging", {})

    level_name = logging_conf.get("level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = logging_conf.get(
        "format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    handlers = [logging.StreamHandler(sys.stdout)]
    log_file = logging_conf.get("file")
    if log_file:
        log_file = os.path.expanduser(os.path.expandvars(log_file))
        log_dir = os.path.dirname(os.path.abspath(log_file))
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=logging_conf.get("max_bytes", 1_000_000),
                backupCount=logging_conf.get("backup_count", 3),
                encoding="utf-8",
            )
        )

    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)
    module_levels: Dict[str, str] = logging_conf.get("module_levels", {})
    for module_name, module_level in module_levels.items():
        mod_logger = logging.getLogger(module_name)
        mod_logger.setLevel(getattr(logging, module_level.upper(), level))

    logger.info(
        "Logging configured (level=%s, output=%s)",
        level_name,
        log_file or "stdout",
    )


def apply_logging_overrides(config: dict, args: argparse.Namespace) -> None:
    """Merge logging-level/file overrides from CLI args or environment into config."""

    config.setdefault("logging", {})
    logging_conf = config["logging"]

    env_level = os.getenv("ANY2PG_LOG_LEVEL")
    env_file = os.getenv("ANY2PG_LOG_FILE")

    if args.log_level:
        logging_conf["level"] = args.log_level
    elif env_level:
        logging_conf["level"] = env_level

    # Preserve intentional empty string (disable file) by checking for None explicitly
    if args.log_file is not None:
        logging_conf["file"] = args.log_file
    elif env_file is not None:
        logging_conf["file"] = env_file

def run_init(config):
    """Initialize SQLite storage and extract source metadata."""
    db_path = config['project']['db_file']
    source_conf = config["database"]["source"]
    project_name = config["project"]["name"]
    logger.info(
        "Initializing system (db_file=%s, source=%s, uri=%s)",
        db_path,
        source_conf.get("type"),
        redact_dsn(source_conf.get("uri", "")),
    )

    db = DBManager(db_path, project_name=project_name)
    db.init_db()

    extractor = MetadataExtractor(config, db)
    extractor.run()

def run_reset_logs(config):
    """Clear migration log table for a clean retry."""
    db_path = config['project']['db_file']
    project_name = config['project']['name']
    logger.info("Resetting logs for DB: %s", db_path)
    db = DBManager(db_path, project_name=project_name)
    with db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM migration_logs WHERE project_name = ?", (project_name,))
    logger.info("Logs reset complete.")

def run_migration(config):
    """Execute the migration workflow for pending SQL files."""
    source_dir = config['project']['source_dir']
    target_dir = config['project']['target_dir']
    db_path = config['project']['db_file']
    project_name = config['project']['name']

    if not os.path.isdir(source_dir):
        raise FileNotFoundError(f"Configured source_dir does not exist: {source_dir}")

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    db = DBManager(db_path, project_name=project_name)
    source_conf = config['database']['source']
    target_conf = config['database']['target']
    source_dialect = source_conf.get('type', 'oracle')
    rag = RAGContextBuilder(db, source_dialect=source_dialect, project_name=project_name)
    verifier = VerifierAgent(config)
    workflow_engine = MigrationWorkflow(config, rag, verifier)

    logger.info(
        "Run starting (project=%s, source_dir=%s, target_dir=%s, db_file=%s, source=%s, target=%s)",
        project_name,
        source_dir,
        target_dir,
        db_path,
        source_dialect,
        target_conf.get('type', 'postgres'),
    )
    
    all_files = sorted([f for f in os.listdir(source_dir) if f.endswith('.sql')])
    if not all_files:
        logger.warning(f"No SQL files found in {source_dir}")
        return

    # Resume logic
    pending_files = []
    with db.get_cursor() as cur:
        for fname in all_files:
            full_path = os.path.join(source_dir, fname)
            cur.execute(
                "SELECT status FROM migration_logs WHERE project_name = ? AND file_path = ?",
                (project_name, full_path),
            )
            row = cur.fetchone()
            if row and row['status'] == 'DONE':
                continue
            pending_files.append(fname)

    logger.info(
        "Processing %d / %d files using DB: %s",
        len(pending_files),
        len(all_files),
        db_path,
    )
    logger.debug("Pending files: %s", ", ".join(pending_files))

    if not pending_files:
        logger.info("All files are already marked DONE; nothing to process.")
        return
    
    done, failed = 0, 0
    for fname in tqdm(pending_files, desc="Processing Files"):
        file_path = os.path.join(source_dir, fname)
        output_path = os.path.join(target_dir, fname)

        with open(file_path, 'r', encoding='utf-8') as f:
            source_sql = f.read()

        ctx_result: Optional[ContextResult] = None
        try:
            ctx_result = rag.build_context(source_sql)
        except Exception:
            logger.warning("[%s] Failed to pre-compute context; continuing", fname)
            ctx_result = None

        initial_state = {
            "file_path": file_path,
            "source_sql": source_sql,
            "target_sql": None,
            "status": "PENDING",
            "error_msg": None,
            "retry_count": 0,
            "rag_context": (ctx_result.context if ctx_result and ctx_result.context else None),
            "schema_refs": sorted(ctx_result.referenced_schemas) if ctx_result else [],
            "skipped_statements": [],
            "executed_statements": 0,
        }

        try:
            logger.info("[%s] Starting workflow", fname)
            final_state = workflow_engine.app.invoke(initial_state)

            if final_state['status'] == 'DONE' and final_state['target_sql']:
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(final_state['target_sql'])
                done += 1
                logger.info("[%s] Migration complete -> %s", fname, output_path)
            else:
                failed += 1
                logger.warning(
                    "[%s] status=%s error=%s",
                    fname,
                    final_state['status'],
                    final_state['error_msg'],
                )

            detected_schemas = sorted(set(final_state.get("schema_refs") or []))
            schemas_text = ",".join(detected_schemas) if detected_schemas else None
            skipped = final_state.get("skipped_statements") or []
            skipped_blob = "\n".join(skipped) if skipped else None

            with db.get_cursor(commit=True) as cur:
                cur.execute("""
                    INSERT INTO migration_logs (project_name, file_path, detected_schemas, status, retry_count, last_error_msg, target_path, skipped_statements, executed_statements)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_name, file_path) DO UPDATE SET
                        detected_schemas = excluded.detected_schemas,
                        status = excluded.status,
                        retry_count = excluded.retry_count,
                        last_error_msg = excluded.last_error_msg,
                        target_path = excluded.target_path,
                        skipped_statements = excluded.skipped_statements,
                        executed_statements = excluded.executed_statements,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    project_name,
                    final_state['file_path'],
                    schemas_text,
                    final_state['status'],
                    final_state['retry_count'],
                    final_state['error_msg'],
                    output_path if final_state['status'] == 'DONE' else None,
                    skipped_blob,
                    final_state.get('executed_statements', 0),
                ))
        except Exception:
            logger.exception("[%s] Critical error processing file", fname)
            failed += 1

    logger.info(
        f"Run summary: {done} succeeded, {failed} failed/incomplete out of {len(pending_files)} pending files"
    )



def run_report(config, schema_filter: Optional[str] = None, status_filter: Optional[str] = None):
    """Print a migration report filtered by project, schema, and status."""

    db_path = config['project']['db_file']
    project_name = config['project']['name']
    logger.info("Loading report from %s for project %s", db_path, project_name)

    db = DBManager(db_path, project_name=project_name)
    base_sql = (
        "SELECT file_path, detected_schemas, status, retry_count, executed_statements, "
        "skipped_statements, last_error_msg, target_path, updated_at "
        "FROM migration_logs WHERE project_name = ?"
    )
    params: List = [project_name]

    if schema_filter:
        base_sql += " AND detected_schemas LIKE ?"
        params.append(f"%{schema_filter.upper()}%")

    if status_filter:
        base_sql += " AND status = ?"
        params.append(status_filter)

    base_sql += " ORDER BY updated_at DESC"

    with db.get_cursor() as cur:
        cur.execute(base_sql, params)
        rows = cur.fetchall()

    if not rows:
        print("No records found for the given filters.")
        return

    print("\n=== Migration Report ===")
    print(f"Project: {project_name}")
    if schema_filter:
        print(f"Schema filter: {schema_filter}")
    if status_filter:
        print(f"Status filter: {status_filter}")
    print("-----------------------")

    for row in rows:
        print(f"File: {row['file_path']}")
        print(f"  Schemas: {row['detected_schemas'] or 'N/A'}")
        print(f"  Status: {row['status']} (retries={row['retry_count']})")
        print(f"  Executed: {row['executed_statements']} | Skipped: {bool(row['skipped_statements'])}")
        if row['target_path']:
            print(f"  Target: {row['target_path']}")
        if row['last_error_msg']:
            print(f"  Notes/Error: {row['last_error_msg']}")
        if row['skipped_statements']:
            print("  Skipped statements:\n    " + "\n    ".join(row['skipped_statements'].split("\n")))
        print(f"  Updated at: {row['updated_at']}")
        print("-----------------------")


def main():
    parser = argparse.ArgumentParser(
        description="Any2PG: Hybrid SQL Migration Tool",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python src/main.py --mode metadata --config config.yaml\n"
            "  python src/main.py --mode port --db-file project_A.db\n"
            "  python src/main.py --mode report --schema-filter HR --db-file project_A.db\n"
            "All behavior is controlled via the YAML config; CLI flags only select the operation and override the DB file."
        ),
    )
    parser.add_argument(
        '--mode',
        choices=['metadata', 'port', 'report'],
        default=None,
        help="Select the operation mode (metadata extraction, porting, or report display)",
    )
    parser.add_argument(
        '--init',
        action='store_true',
        help="Initialize SQLite storage and extract source DB metadata (alias for --mode metadata)",
    )
    parser.add_argument(
        '--run',
        action='store_true',
        help="Run the migration workflow for SQL files in project.source_dir (alias for --mode port)",
    )
    parser.add_argument(
        '--reset-logs',
        action='store_true',
        help="Truncate migration_logs to retry all input files",
    )
    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help="Path to YAML config controlling adapters, logging, RAG, verifier, and directories",
    )
    parser.add_argument(
        '--db-file',
        type=str,
        default=None,
        help="Override project.db_file from config (useful per run without editing YAML)",
    )
    parser.add_argument(
        '--log-level',
        type=str,
        default=None,
        help="Override logging.level from config (DEBUG/INFO/WARNING/ERROR)",
    )
    parser.add_argument(
        '--log-file',
        type=str,
        default=None,
        help="Override logging.file path from config (empty string disables file handler)",
    )
    parser.add_argument(
        '--schema-filter',
        type=str,
        default=None,
        help="Filter report rows by schema substring (report mode only)",
    )
    parser.add_argument(
        '--status-filter',
        type=str,
        default=None,
        help="Filter report rows by status (report mode only)",
    )

    args = parser.parse_args()
    config = load_config(args.config)
    apply_logging_overrides(config, args)
    config = validate_config(config)
    configure_logging(config)

    # Allow CLI override of SQLite path without editing YAML
    if args.db_file:
        config['project']['db_file'] = args.db_file

    mode = args.mode or config.get("verification", {}).get("mode", "port")
    if args.init:
        mode = "metadata"
    elif args.run:
        mode = "port"

    if args.reset_logs:
        run_reset_logs(config)
        return

    if mode == "metadata":
        run_init(config)
    elif mode == "report":
        run_report(config, schema_filter=args.schema_filter, status_filter=args.status_filter)
    else:
        run_migration(config)


if __name__ == "__main__":
    main()
