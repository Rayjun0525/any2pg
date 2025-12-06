import os
import sys
import argparse
import logging
from logging.handlers import RotatingFileHandler
import yaml
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit
from tqdm import tqdm

from modules.sqlite_store import DBManager
from modules.metadata_extractor import MetadataExtractor
from modules.context_builder import ContextResult, RAGContextBuilder
from modules.postgres_verifier import VerifierAgent
from quality_check import render_quality_report, run_quality_checks
from agents.workflow import MigrationWorkflow
from ui.tui import TUIApplication

logger = logging.getLogger("Any2PG")
logger.addHandler(logging.NullHandler())


class SQLiteLogHandler(logging.Handler):
    """Write log records into the project's SQLite execution log table."""

    def __init__(self, db: DBManager):
        super().__init__()
        self.db = db

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            event = record.getMessage()
            self.db.add_execution_log(event, detail=message, level=record.levelname)
        except Exception:
            # Avoid recursive logging loops
            sys.stderr.write("[SQLiteLogHandler] failed to persist log\n")


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
    for key in ("name", "db_file", "max_retries"):
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

    project.setdefault("mirror_outputs", False)
    project.setdefault("auto_ingest_source_dir", False)
    project.setdefault("silent_mode", False)

    source_dir = project.get("source_dir")
    target_dir = project.get("target_dir")

    project["source_dir"] = (
        expand_path(source_dir) if source_dir not in (None, "") else None
    )
    project["target_dir"] = (
        expand_path(target_dir) if target_dir not in (None, "") else None
    )
    project["db_file"] = expand_path(project["db_file"])

    if project["mirror_outputs"] and not project["target_dir"]:
        raise ValueError("project.target_dir is required when mirror_outputs is true")

    if project.get("auto_ingest_source_dir") and not project.get("source_dir"):
        raise ValueError("project.source_dir is not set but auto_ingest_source_dir is true")

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


def configure_logging(config: dict, extra_handlers: Optional[List[logging.Handler]] = None, silent: bool = False):
    logging_conf = config.get("logging", {})

    level_name = logging_conf.get("level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = logging_conf.get(
        "format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    handlers: List[logging.Handler] = []
    if not silent:
        handlers.append(logging.StreamHandler(sys.stdout))
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

    if extra_handlers:
        handlers.extend(extra_handlers)

    logging.basicConfig(level=level, format=fmt, handlers=handlers or [logging.NullHandler()], force=True)
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
    env_silent = os.getenv("ANY2PG_SILENT")

    if args.log_level:
        logging_conf["level"] = args.log_level
    elif env_level:
        logging_conf["level"] = env_level

    # Preserve intentional empty string (disable file) by checking for None explicitly
    if args.log_file is not None:
        logging_conf["file"] = args.log_file
    elif env_file is not None:
        logging_conf["file"] = env_file

    project_conf = config.setdefault("project", {})
    silent_flag = getattr(args, "silent", False)
    if silent_flag:
        project_conf["silent_mode"] = True
    elif env_silent is not None:
        project_conf["silent_mode"] = env_silent.lower() in {"1", "true", "yes"}


def _sync_source_dir(db: DBManager, source_dir: str, auto_select: bool = True) -> None:
    """Ingest .sql files from source_dir into SQLite as source_assets."""

    if not source_dir:
        logger.info("Source directory not configured; skipping filesystem ingest")
        return

    if not os.path.isdir(source_dir):
        logger.warning("Source directory not found for sync: %s", source_dir)
        return

    sql_files = sorted([f for f in os.listdir(source_dir) if f.lower().endswith('.sql')])
    if not sql_files:
        logger.warning("No SQL files discovered in source_dir=%s", source_dir)
        return

    for fname in sql_files:
        file_path = os.path.join(source_dir, fname)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                sql_text = f.read()
        except OSError as exc:
            logger.error("Failed to read %s: %s", file_path, exc)
            continue
        db.sync_source_asset(file_path, sql_text, selected_for_port=auto_select)


def _record_event(config: dict, db: DBManager, event: str, detail: Optional[str] = None, level: str = "INFO") -> None:
    if not config.get("project", {}).get("silent_mode"):
        return
    try:
        db.add_execution_log(event, detail=detail, level=level)
    except Exception:
        logger.debug("Failed to persist execution log for event=%s", event)

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
    if config["project"].get("auto_ingest_source_dir", False):
        _sync_source_dir(db, config["project"]["source_dir"])
    _record_event(config, db, "metadata:started", detail=f"source={redact_dsn(source_conf.get('uri', ''))}")
    extractor = MetadataExtractor(config, db)
    extractor.run()
    _record_event(config, db, "metadata:completed")

def run_reset_logs(config):
    """Clear migration log table for a clean retry."""
    db_path = config['project']['db_file']
    project_name = config['project']['name']
    logger.info("Resetting logs for DB: %s", db_path)
    db = DBManager(db_path, project_name=project_name)
    with db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM migration_logs WHERE project_name = ?", (project_name,))
    logger.info("Logs reset complete.")

def run_migration(
    config,
    only_selected: bool = False,
    changed_only: bool = False,
    asset_names: Optional[Iterable[str]] = None,
):
    """Execute the migration workflow for pending SQL files."""
    summary_messages = []
    source_dir = config['project'].get('source_dir')
    target_dir = config['project'].get('target_dir')
    db_path = config['project']['db_file']
    project_name = config['project']['name']
    mirror_outputs = config["project"].get("mirror_outputs", False)
    auto_ingest = config["project"].get("auto_ingest_source_dir", False)

    db = DBManager(db_path, project_name=project_name)
    db.init_db()
    if auto_ingest:
        _sync_source_dir(db, source_dir)
    source_conf = config['database']['source']
    target_conf = config['database']['target']
    source_dialect = source_conf.get('type', 'oracle')
    rag = RAGContextBuilder(db, source_dialect=source_dialect, project_name=project_name)
    verifier = VerifierAgent(config)
    workflow_engine = MigrationWorkflow(config, rag, verifier)

    logger.info(
        "Run starting (project=%s, db_file=%s, source=%s, target=%s, mirror_outputs=%s, auto_ingest=%s)",
        project_name,
        db_path,
        source_dialect,
        target_conf.get('type', 'postgres'),
        mirror_outputs,
        auto_ingest,
    )
    _record_event(
        config,
        db,
        "porting:started",
        detail=(
            f"mode={config.get('llm', {}).get('mode', 'full')} "
            f"mirror={mirror_outputs} selected={only_selected} "
            f"changed={changed_only} filter={','.join(sorted(asset_names)) if asset_names else 'all'}"
        ),
    )
    
    assets = db.list_source_assets(only_selected=only_selected, only_changed=changed_only)
    if not assets:
        message = (
            "No SQL assets registered in SQLite. Run --init or enable auto_ingest_source_dir "
            "to ingest source files before porting."
        )
        logger.warning(message)
        print(message)
        return

    allowed_names = {a['file_name'] for a in assets}
    if asset_names:
        filter_names = {n for n in asset_names}
        allowed_names &= filter_names
        assets = [a for a in assets if a['file_name'] in allowed_names]
        if not allowed_names:
            logger.warning("No assets matched the provided filters.")
            return

    pending_assets = []
    for asset in assets:
        status = asset["last_status"] or asset["log_status"]
        if status == "DONE" and asset["source_hash"] == asset["content_hash"]:
            continue
        pending_assets.append(asset)

    logger.info(
        "Processing %d / %d assets using DB: %s",
        len(pending_assets),
        len(assets),
        db_path,
    )
    logger.debug("Pending assets: %s", ", ".join(a["file_name"] for a in pending_assets))

    if not pending_assets:
        message = "All assets are already marked DONE with no detected changes."
        logger.info(message)
        print(message)
        return
    
    done, failed = 0, 0
    for asset in tqdm(pending_assets, desc="Processing Files"):
        file_path = asset["file_path"]
        file_name = asset["file_name"]
        output_path = os.path.join(target_dir, file_name) if target_dir else file_name
        source_sql = asset["sql_text"]

        ctx_result: Optional[ContextResult] = None
        try:
            ctx_result = rag.build_context(source_sql)
        except Exception:
            logger.warning("[%s] Failed to pre-compute context; continuing", file_name)
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
            logger.info("[%s] Starting workflow", file_name)
            final_state = workflow_engine.app.invoke(initial_state)

            if final_state['status'] == 'DONE' and final_state['target_sql']:
                db.save_rendered_output(
                    output_path,
                    final_state['target_sql'],
                    source_hash=asset["content_hash"],
                    status=final_state['status'],
                    verified=final_state.get('status') == 'DONE',
                    last_error=final_state.get('error_msg'),
                )
                done += 1
                if mirror_outputs:
                    os.makedirs(target_dir, exist_ok=True)
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write(final_state['target_sql'])
                    logger.info("[%s] Migration complete -> %s", file_name, output_path)
                else:
                    logger.info("[%s] Migration complete (stored in SQLite)", file_name)
            else:
                failed += 1
                logger.warning(
                    "[%s] status=%s error=%s",
                    file_name,
                    final_state['status'],
                    final_state['error_msg'],
                )

            detected_schemas = sorted(set(final_state.get("schema_refs") or []))
            schemas_text = ",".join(detected_schemas) if detected_schemas else None
            skipped = final_state.get("skipped_statements") or []
            skipped_blob = "\n".join(skipped) if skipped else None

            with db.get_cursor(commit=True) as cur:
                cur.execute(
                    """
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
                """,
                    (
                        project_name,
                        final_state['file_path'],
                        schemas_text,
                        final_state['status'],
                        final_state['retry_count'],
                        final_state['error_msg'],
                        output_path if final_state['status'] == 'DONE' else None,
                        skipped_blob,
                        final_state.get('executed_statements', 0),
                    ),
                )
        except Exception:
            logger.exception("[%s] Critical error processing file", file_name)
            failed += 1

    summary_line = (
        "Run summary: "
        f"{done} succeeded, {failed} failed/incomplete out of {len(pending_assets)} pending assets"
    )
    logger.info(summary_line)
    summary_messages.append(summary_line)
    summary_messages.append("Results are recorded in SQLite for status and preview screens.")
    print("\n".join(summary_messages))
    _record_event(
        config,
        db,
        "porting:completed",
        detail=f"done={done}, failed={failed}, pending={len(pending_assets)}",
        level="ERROR" if failed else "INFO",
    )


def run_quality_audit(config: dict) -> None:
    """Run internal quality checks and print a scored report."""

    logger.info("Running quality checks against a sandboxed SQLite store")
    report = run_quality_checks(config)
    print(render_quality_report(report))
    if not report.perfect:
        logger.warning("Quality checks finished with non-perfect scores")
    db = DBManager(config['project']['db_file'], project_name=config['project']['name'])
    db.init_db()
    _record_event(
        config,
        db,
        "quality:completed",
        detail=f"score={report.score:.2f} perfect={report.perfect}",
        level="INFO" if report.perfect else "WARNING",
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


def _list_assets(
    config: dict,
    show_sql: bool = False,
    only_selected: bool = False,
    only_changed: bool = False,
    asset_names: Optional[Iterable[str]] = None,
):
    db_path = config['project']['db_file']
    source_dir = config['project']['source_dir']
    project_name = config['project']['name']
    db = DBManager(db_path, project_name=project_name)
    db.init_db()
    if config["project"].get("auto_ingest_source_dir", True):
        _sync_source_dir(db, source_dir)

    assets = db.list_source_assets(only_selected=only_selected, only_changed=only_changed)
    if asset_names:
        names = {n for n in asset_names}
        assets = [a for a in assets if a['file_name'] in names]

    print(f"\n=== Registered assets in {db_path} (project={project_name}) ===")
    if only_selected:
        print("(only selected)")
    if only_changed:
        print("(only changed since last render)")
    if not assets:
        print("No assets found.")
        return

    for row in assets:
        status = row['last_status'] or row['log_status'] or 'PENDING'
        changed = 'CHANGED' if (row['source_hash'] != row['content_hash']) else 'OK'
        print(f"- {row['file_name']} | status={status} | selected={bool(row['selected_for_port'])} | {changed}")
        if row.get('parsed_schemas'):
            print(f"  Schemas: {row['parsed_schemas']}")
        if show_sql:
            print("  --- SQL ---")
            print(row['sql_text'])
            print("  -----------")


def _update_selection(config: dict, select: Iterable[str], deselect: Iterable[str]) -> None:
    db_path = config['project']['db_file']
    project_name = config['project']['name']
    db = DBManager(db_path, project_name=project_name)
    db.init_db()
    if config["project"].get("auto_ingest_source_dir", True):
        _sync_source_dir(db, config['project']['source_dir'])
    selected = db.set_selection(select, True) if select else 0
    deselected = db.set_selection(deselect, False) if deselect else 0
    if select:
        logger.info("Marked %d asset(s) as selected: %s", selected, ", ".join(select))
    if deselect:
        logger.info("Marked %d asset(s) as deselected: %s", deselected, ", ".join(deselect))


def _export_outputs(
    config: dict,
    only_selected: bool = False,
    changed_only: bool = False,
    export_dir: Optional[str] = None,
    asset_names: Optional[Iterable[str]] = None,
):
    project = config['project']
    db = DBManager(project['db_file'], project_name=project['name'])
    db.init_db()
    if config["project"].get("auto_ingest_source_dir", True):
        _sync_source_dir(db, project['source_dir'])

    assets = db.list_source_assets(only_selected=only_selected, only_changed=changed_only)
    allowed_names = {a['file_name'] for a in assets}
    if asset_names:
        filter_names = {n for n in asset_names}
        allowed_names &= filter_names
        if not allowed_names:
            logger.warning(
                "No rendered outputs matched the requested asset names. (requested: %s)",
                ", ".join(sorted(filter_names)),
            )
            return
    elif not allowed_names:
        logger.warning("No assets matched the current selection filters for export.")
        return

    export_dir = export_dir or project['target_dir']
    os.makedirs(export_dir, exist_ok=True)

    outputs = db.fetch_rendered_sql(allowed_names)
    if not outputs:
        logger.warning("No rendered outputs available for export.")
        return

    for row in outputs:
        if changed_only and row['file_name'] not in allowed_names:
            continue
        if not row['sql_text']:
            logger.warning("Skipping %s (no SQL stored)", row['file_name'])
            continue
        dest = os.path.join(export_dir, row['file_name'])
        with open(dest, 'w', encoding='utf-8') as f:
            f.write(row['sql_text'])
        logger.info("Exported %s -> %s", row['file_name'], dest)
    _record_event(
        config,
        db,
        "export:completed",
        detail=f"count={len(outputs)} -> {export_dir}",
    )


def _apply_outputs(
    config: dict,
    only_selected: bool = False,
    changed_only: bool = False,
    asset_names: Optional[Iterable[str]] = None,
):
    project = config['project']
    db = DBManager(project['db_file'], project_name=project['name'])
    db.init_db()
    if config["project"].get("auto_ingest_source_dir", True):
        _sync_source_dir(db, project['source_dir'])
    verifier = VerifierAgent(config)

    assets = db.list_source_assets(only_selected=only_selected, only_changed=changed_only)
    allowed_names = {a['file_name'] for a in assets}
    if asset_names:
        filter_names = {n for n in asset_names}
        allowed_names &= filter_names
        if not allowed_names:
            logger.warning(
                "No rendered outputs matched the requested asset names. (requested: %s)",
                ", ".join(sorted(filter_names)),
            )
            return
    elif not allowed_names:
        logger.warning("No assets matched the current selection filters for apply.")
        return

    outputs = db.fetch_rendered_sql(allowed_names)
    if not outputs:
        logger.warning("No rendered outputs found to apply.")
        return

    success, failed = 0, 0

    for row in outputs:
        if changed_only and row['file_name'] not in allowed_names:
            continue
        if not row['sql_text']:
            logger.warning("Skipping %s (no SQL stored)", row['file_name'])
            continue
        result = verifier.apply_sql(row['sql_text'])
        db.save_rendered_output(
            row['file_path'],
            row['sql_text'],
            source_hash=row['source_hash'],
            status='APPLIED' if result.success else 'FAILED',
            verified=result.success,
            last_error=result.error,
        )
        if result.success:
            logger.info("Applied %s to PostgreSQL (skipped=%d)", row['file_name'], len(result.skipped_statements))
            success += 1
        else:
            logger.error("Failed applying %s: %s", row['file_name'], result.error)
            failed += 1
    _record_event(
        config,
        db,
        "apply:completed",
        detail=f"success={success}, failed={failed}",
        level="ERROR" if failed else "INFO",
    )


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
        choices=['metadata', 'port', 'report', 'assets', 'export', 'apply', 'quality', 'tui'],
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
    parser.add_argument(
        '--only-selected',
        action='store_true',
        help="Limit listing/export/apply to assets marked selected_for_port",
    )
    parser.add_argument(
        '--show-sql',
        action='store_true',
        help="Print SQL bodies when listing assets",
    )
    parser.add_argument(
        '--select',
        nargs='*',
        default=None,
        help="Asset file names to mark as selected for porting",
    )
    parser.add_argument(
        '--deselect',
        nargs='*',
        default=None,
        help="Asset file names to unselect",
    )
    parser.add_argument(
        '--export-dir',
        type=str,
        default=None,
        help="Override directory for exporting rendered SQL (export mode)",
    )
    parser.add_argument(
        '--changed-only',
        action='store_true',
        help="Only operate on assets whose source hash changed since last render",
    )
    parser.add_argument(
        '--asset-names',
        nargs='*',
        default=None,
        help="Restrict export/apply/list to specific asset file names",
    )
    parser.add_argument(
        '--quality',
        action='store_true',
        help="Run internal quality checks (alias for --mode quality)",
    )
    parser.add_argument(
        '--llm-mode',
        choices=['fast', 'full'],
        default=None,
        help="Override LLM processing mode for this run",
    )
    parser.add_argument(
        '--silent',
        action='store_true',
        help="Suppress stdout and write execution logs into SQLite for later review",
    )

    args = parser.parse_args()
    config = load_config(args.config)
    apply_logging_overrides(config, args)
    config = validate_config(config)
    if args.llm_mode:
        config.setdefault("llm", {})["mode"] = args.llm_mode

    db_for_logging = DBManager(
        config["project"]["db_file"], project_name=config["project"]["name"]
    )
    db_for_logging.init_db()
    extra_handlers: List[logging.Handler] = []
    if config["project"].get("silent_mode"):
        extra_handlers.append(SQLiteLogHandler(db_for_logging))

    configure_logging(config, extra_handlers=extra_handlers, silent=config["project"].get("silent_mode", False))

    # Allow CLI override of SQLite path without editing YAML
    if args.db_file:
        config['project']['db_file'] = args.db_file

    mode = args.mode or "tui"
    if args.init:
        mode = "metadata"
    elif args.run:
        mode = "port"
    elif args.quality:
        mode = "quality"

    if args.reset_logs:
        run_reset_logs(config)
        return

    if args.select or args.deselect:
        _update_selection(config, args.select or [], args.deselect or [])

    if mode == "metadata":
        run_init(config)
    elif mode == "report":
        run_report(config, schema_filter=args.schema_filter, status_filter=args.status_filter)
    elif mode == "assets":
        _list_assets(
            config,
            show_sql=args.show_sql,
            only_selected=args.only_selected,
            only_changed=args.changed_only,
            asset_names=args.asset_names,
        )
    elif mode == "export":
        _export_outputs(
            config,
            only_selected=args.only_selected,
            changed_only=args.changed_only,
            export_dir=args.export_dir,
            asset_names=args.asset_names,
        )
    elif mode == "apply":
        _apply_outputs(
            config,
            only_selected=args.only_selected,
            changed_only=args.changed_only,
            asset_names=args.asset_names,
        )
    elif mode == "quality":
        run_quality_audit(config)
    elif mode == "tui":
        app = TUIApplication(
            config,
            actions={
                "metadata": run_init,
                "port": run_migration,
                "export": _export_outputs,
                "apply": _apply_outputs,
                "quality": run_quality_audit,
            },
        )
        app.run()
    else:
        run_migration(
            config,
            only_selected=args.only_selected,
            changed_only=args.changed_only,
            asset_names=args.asset_names,
        )


if __name__ == "__main__":
    main()
