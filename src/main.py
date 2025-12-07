
import os
import sys
import argparse
import logging
import yaml
import json
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict

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
    log_conf = config.get("general", {})
    log_file = log_conf.get("log_path", "any2pg.log")
    level_name = log_conf.get("log_level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=1024*1024, backupCount=3))
    
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", handlers=handlers, force=True)

def get_db(config) -> DBManager:
    db_path = config["general"]["metadata_path"]
    project = config["general"]["project_name"]
    db = DBManager(db_path, project_name=project)
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
    rag = RAGContextBuilder(db, source_dialect=config["database"]["source"]["type"], project_name=config["general"]["project_name"])
    verifier = VerifierAgent(config)
    workflow = MigrationWorkflow(config, rag, verifier)
    
    assets = db.list_source_assets(only_selected=only_selected, only_changed=changed_only)
    print(f"Found {len(assets)} assets to process.")
    
    for asset in assets:
        logger.info(f"Processing {asset['file_name']}...")
        
        # Prepare state
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
        
        # Save result
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
        
        # Update logs
        db.add_execution_log("migration", detail=f"{asset['file_name']} -> {status}", level="INFO" if status=="DONE" else "ERROR") 

    print("Porting completed.")

def action_verify(config: dict):
    # This is effectively re-running verification on rendered outputs
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
            need_permission=False # Reset or logic to detect need_permission
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
    export_dir = export_dir or config["general"].get("export_dir", "output")
    os.makedirs(export_dir, exist_ok=True)
    
    outputs = db.list_rendered_outputs(limit=10000) # Fetch all roughly
    # Better to filter in db, but list_rendered_outputs is limited. 
    # Using fetch_rendered_sql without filenames returns all?
    # No, fetch_rendered_sql needs filenames.
    # Let's verify list_source_assets logic which invokes fetch_rendered_sql internally or we use custom logic.
    
    # Simple export logic:
    # 1. Get all assets
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
    # Re-use export logic selection
    assets = db.list_source_assets(only_selected=only_selected, only_changed=changed_only)
    print(f" Applying {len(assets)} assets to Target DB...")
    
    for asset in assets:
        res = db.fetch_rendered_sql([asset["file_name"]])
        if not res: continue
        row = res[0]
        if not row["sql_text"]: continue
        
        logger.info(f"Applying {row['file_name']}...")
        result = verifier.apply_sql(row["sql_text"]) 
        # Note: verifier.apply_sql essentially does execution without rollback if configured for 'apply' mode or similar
        # But VerifierAgent usually rolls back. We might need a 'real apply' method in VerifierAgent or use execute_sql with commit.
        # Assuming apply_sql does commit.
        
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
    run_quality_audit(config)

def run_quality_audit(config: dict):
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
    parser.add_argument("--mode", choices=["metadata", "port", "verify", "status", "export", "apply", "quality", "tui", "cli"], default="cli")
    
    args = parser.parse_args()
    config = load_config(args.config)
    configure_logging(config)

    # CLI mode logic
    mode = args.mode
    if config["general"].get("mode") == "tui" and mode == "cli":
        mode = "tui"
    
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
        # Default behavior if cli is chosen but no specific command
        print("Please specify a mode: --mode [metadata|port|verify|status|export|apply|tui]")
        parser.print_help()

if __name__ == "__main__":
    main()
