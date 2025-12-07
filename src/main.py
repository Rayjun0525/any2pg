import os
import sys
import argparse
import logging
import yaml
from logging.handlers import RotatingFileHandler
from typing import Optional

from modules.sqlite_store import DBManager
from modules.metadata_extractor import MetadataExtractor
from modules.context_builder import RAGContextBuilder
from modules.postgres_verifier import VerifierAgent
from agents.workflow import MigrationWorkflow
from ui.tui import TUIApplication
from quality_check import render_quality_report, run_quality_checks

logger = logging.getLogger("Any2PG")

# --- Helper Functions ---

def load_config(path="config.yaml"):
    if not os.path.exists(path):
        print(f"Config file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def configure_logging(config):
    log_conf = config.get("logging", {})
    log_file = log_conf.get("file", "any2pg.log")
    level_name = log_conf.get("level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(RotatingFileHandler(
            log_file, 
            maxBytes=log_conf.get("max_bytes", 1024*1024), 
            backupCount=log_conf.get("backup_count", 3)
        ))
    
    fmt = log_conf.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)

def get_db(config) -> DBManager:
    db_path = config["project"]["db_file"]
    project_name = config["project"]["name"]
    db = DBManager(db_path, project_name=project_name)
    db.init_db()
    return db

# --- Core Actions ---

def action_metadata(config: dict):
    db = get_db(config)
    extractor = MetadataExtractor(config, db)
    extractor.run()
    print("Metadata extraction completed.")

def action_port(config: dict, only_selected=False, changed_only=False):
    db = get_db(config)
    project_name = config["project"]["name"]
    source_dialect = config["database"]["source"]["type"]
    rag = RAGContextBuilder(db, source_dialect=source_dialect, project_name=project_name)
    verifier = VerifierAgent(config)
    workflow = MigrationWorkflow(config, rag, verifier)
    
    assets = db.list_source_assets(only_selected=only_selected, only_changed=changed_only)
    print(f"Found {len(assets)} assets to process.")
    
    for asset in assets:
        logger.info(f"Processing {asset['file_name']}...")
        
        initial_state = {
            "file_path": asset["file_path"],
            "source_sql": asset["sql_text"],
            "target_sql": None,
            "status": "PENDING",
            "error_msg": None,
            "retry_count": 0,
            "rag_context": None,
            "schema_refs": [],
            "skipped_statements": [],
            "executed_statements": 0
        }
        
        final_state = workflow.app.invoke(initial_state)
        
        status = final_state.get("status", "FAILED")
        target_sql = final_state.get("target_sql")
        error_msg = final_state.get("error_msg")
        
        db.save_rendered_output(
            file_path=asset["file_path"],
            sql_text=target_sql,
            source_hash=asset["content_hash"],
            status=status,
            verified=(status == "DONE"),
            last_error=error_msg,
            agent_state="DONE" if status == "DONE" else "FAILED"
        )
        
        db.add_execution_log(
            "migration", 
            detail=f"{asset['file_name']} -> {status}", 
            level="INFO" if status=="DONE" else "ERROR"
        )

    print("Porting completed.")

def action_verify(config: dict):
    db = get_db(config)
    verifier = VerifierAgent(config)
    outputs = db.list_rendered_outputs(limit=1000)
    
    for out in outputs:
        if out["status"] != "DONE":
            continue
            
        full = db.fetch_rendered_sql([out["file_name"]])[0]
        sql = full["sql_text"]
        
        logger.info(f"Verifying {out['file_name']}...")
        result = verifier.verify_sql(sql)
        
        db.save_rendered_output(
            file_path=full["file_path"],
            sql_text=sql,
            status="DONE" if result.success else "VERIFY_FAIL",
            verified=result.success,
            last_error=result.error if not result.success else full["last_error"],
            need_permission=False
        )
    print("Verification run completed.")

def action_status(config: dict):
    db = get_db(config)
    progress = db.summarize_migration()
    print("\n--- Migration Status ---")
    for p in progress:
        print(f"{p['status']}: {p['count']}")
    
    print("\n--- Recent Logs ---")
    logs = db.fetch_execution_logs(limit=10)
    for log in logs:
        print(f"[{log['created_at']}] {log['level']}: {log['event']} {log['detail'] or ''}")

def action_export(config: dict, export_dir=None, only_selected=False, changed_only=False):
    db = get_db(config)
    export_dir = export_dir or config["project"].get("target_dir", "output")
    os.makedirs(export_dir, exist_ok=True)
    
    assets = db.list_source_assets(only_selected=only_selected, only_changed=changed_only)
    count = 0
    for asset in assets:
        res = db.fetch_rendered_sql([asset["file_name"]])
        if res:
            row = res[0]
            if row["sql_text"]:
                path = os.path.join(export_dir, row["file_name"])
                with open(path, "w", encoding="utf-8") as f:
                    f.write(row["sql_text"])
                count += 1
    print(f"Exported {count} files to {export_dir}")

def action_apply(config: dict, only_selected=False, changed_only=False):
    db = get_db(config)
    verifier = VerifierAgent(config)
    assets = db.list_source_assets(only_selected=only_selected, only_changed=changed_only)
    print(f"Applying {len(assets)} assets to Target DB...")
    
    for asset in assets:
        res = db.fetch_rendered_sql([asset["file_name"]])
        if not res:
            continue
        row = res[0]
        if not row["sql_text"]:
            continue
        
        logger.info(f"Applying {row['file_name']}...")
        result = verifier.apply_sql(row["sql_text"])
        
        status = "APPLIED" if result.success else "APPLY_FAIL"
        db.save_rendered_output(
            file_path=row["file_path"],
            sql_text=row["sql_text"],
            status=status,
            verified=result.success,
            last_error=result.error
        )
    print("Apply completed.")

def action_quality(config: dict):
    logger.info("Running quality checks...")
    report = run_quality_checks(config)
    print(render_quality_report(report))

# --- TUI Wrapper ---
def action_tui(config: dict):
    actions = {
        "metadata": action_metadata,
        "port": action_port,
        "export": action_export,
        "apply": action_apply,
        "quality": action_quality
    }
    app = TUIApplication(config, actions)
    app.run()

# --- Main Dispatch ---

def main():
    parser = argparse.ArgumentParser(description="Any2PG Migration Tool")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--mode", choices=["metadata", "port", "verify", "status", "export", "apply", "quality", "tui"], default="tui")
    
    args = parser.parse_args()
    config = load_config(args.config)
    configure_logging(config)

    mode = args.mode
    
    if mode == "tui":
        action_tui(config)
    elif mode == "metadata":
        action_metadata(config)
    elif mode == "port":
        action_port(config)
    elif mode == "verify":
        action_verify(config)
    elif mode == "status":
        action_status(config)
    elif mode == "export":
        action_export(config)
    elif mode == "apply":
        action_apply(config)
    elif mode == "quality":
        action_quality(config)
    else:
        print("Please specify a valid mode")
        parser.print_help()

if __name__ == "__main__":
    main()
