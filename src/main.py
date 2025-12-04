import os
import sys
import yaml
import argparse
import logging
from logging.handlers import RotatingFileHandler
from typing import Dict
from urllib.parse import urlsplit, urlunsplit
from tqdm import tqdm

from modules.db_manager import DBManager
from modules.extractor import MetadataExtractor
from modules.rag_engine import RAGContextBuilder
from modules.verifier import VerifierAgent
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

def load_config(path="config.yaml"):
    if not os.path.exists(path):
        print(f"Config file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


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

def run_init(config):
    """Initialize SQLite storage and extract source metadata."""
    db_path = config['project']['db_file']
    source_conf = config["database"]["source"]
    logger.info(
        "Initializing system (db_file=%s, source=%s, uri=%s)",
        db_path,
        source_conf.get("type"),
        redact_dsn(source_conf.get("uri", "")),
    )
    
    db = DBManager(db_path)
    db.init_db()
    
    extractor = MetadataExtractor(config, db)
    extractor.run()

def run_reset_logs(config):
    """Clear migration log table for a clean retry."""
    db_path = config['project']['db_file']
    logger.info("Resetting logs for DB: %s", db_path)
    db = DBManager(db_path)
    with db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM migration_logs")
    logger.info("Logs reset complete.")

def run_migration(config):
    """Execute the migration workflow for pending SQL files."""
    source_dir = config['project']['source_dir']
    target_dir = config['project']['target_dir']
    db_path = config['project']['db_file']
    
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    db = DBManager(db_path)
    source_conf = config['database']['source']
    target_conf = config['database']['target']
    source_dialect = source_conf.get('type', 'oracle')
    rag = RAGContextBuilder(db, source_dialect=source_dialect)
    verifier = VerifierAgent(config)
    workflow_engine = MigrationWorkflow(config, rag, verifier)

    logger.info(
        "Run starting (source_dir=%s, target_dir=%s, db_file=%s, source=%s, target=%s)",
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
            cur.execute("SELECT status FROM migration_logs WHERE file_path = ?", (full_path,))
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

        initial_state = {
            "file_path": file_path,
            "source_sql": source_sql,
            "target_sql": None,
            "status": "PENDING",
            "error_msg": None,
            "retry_count": 0,
            "rag_context": None
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

            with db.get_cursor(commit=True) as cur:
                cur.execute("""
                    INSERT INTO migration_logs (file_path, status, retry_count, last_error_msg, target_path)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                        status = excluded.status,
                        retry_count = excluded.retry_count,
                        last_error_msg = excluded.last_error_msg,
                        target_path = excluded.target_path,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    final_state['file_path'],
                    final_state['status'],
                    final_state['retry_count'],
                    final_state['error_msg'],
                    output_path if final_state['status'] == 'DONE' else None
                ))
        except Exception:
            logger.exception("[%s] Critical error processing file", fname)
            failed += 1

    logger.info(
        f"Run summary: {done} succeeded, {failed} failed/incomplete out of {len(pending_files)} pending files"
    )

def main():
    parser = argparse.ArgumentParser(
        description="Any2PG: Hybrid SQL Migration Tool",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python src/main.py --init --config config.yaml\n"
            "  python src/main.py --run --db-file project_A.db\n"
            "  python src/main.py --reset-logs --db-file project_A.db\n"
            "All behavior is controlled via the YAML config; CLI flags only select the operation and override the DB file."
        ),
    )
    parser.add_argument(
        '--init',
        action='store_true',
        help="Initialize SQLite storage and extract source DB metadata",
    )
    parser.add_argument(
        '--run',
        action='store_true',
        help="Run the migration workflow for SQL files in project.source_dir",
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

    args = parser.parse_args()
    config = load_config(args.config)
    configure_logging(config)

    # Allow CLI override of SQLite path without editing YAML
    if args.db_file:
        config['project']['db_file'] = args.db_file

    if args.init:
        run_init(config)
    elif args.reset_logs:
        run_reset_logs(config)
    elif args.run:
        run_migration(config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
