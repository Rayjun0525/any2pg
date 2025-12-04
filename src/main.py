import os
import sys
import yaml
import argparse
import logging
from tqdm import tqdm

from modules.db_manager import DBManager
from modules.extractor import MetadataExtractor
from modules.rag_engine import RAGContextBuilder
from modules.verifier import VerifierAgent
from agents.workflow import MigrationWorkflow

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Any2PG")

def load_config(path="config.yaml"):
    if not os.path.exists(path):
        logger.error(f"Config file not found: {path}")
        sys.exit(1)
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def run_init(config):
    """시스템 초기화 및 메타데이터 추출"""
    db_path = config['project']['db_file']
    logger.info(f"Initializing System with DB: {db_path}")
    
    db = DBManager(db_path)
    db.init_db()
    
    extractor = MetadataExtractor(config, db)
    extractor.run()

def run_reset_logs(config):
    """작업 로그 초기화"""
    db_path = config['project']['db_file']
    logger.info(f"Resetting logs for DB: {db_path}")
    db = DBManager(db_path)
    with db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM migration_logs")
    logger.info("Logs reset complete.")

def run_migration(config):
    """마이그레이션 메인 루프"""
    source_dir = config['project']['source_dir']
    target_dir = config['project']['target_dir']
    db_path = config['project']['db_file']
    
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    db = DBManager(db_path)
    rag = RAGContextBuilder(db)
    verifier = VerifierAgent(config)
    workflow_engine = MigrationWorkflow(config, rag, verifier)
    
    all_files = [f for f in os.listdir(source_dir) if f.endswith('.sql')]
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

    logger.info(f"Processing {len(pending_files)} / {len(all_files)} files using DB: {db_path}")
    
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
            final_state = workflow_engine.app.invoke(initial_state)
            
            if final_state['status'] == 'DONE' and final_state['target_sql']:
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(final_state['target_sql'])
            
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
        except Exception as e:
            logger.error(f"Critical error processing {fname}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Any2PG: Hybrid SQL Migration Tool")
    parser.add_argument('--init', action='store_true', help="Initialize DB and Extract Metadata")
    parser.add_argument('--run', action='store_true', help="Run Migration Workflow")
    parser.add_argument('--reset-logs', action='store_true', help="Reset Migration Logs")
    # [변경] 프로젝트별 DB 파일 지정 옵션 추가
    parser.add_argument('--db-file', type=str, default=None, help="Path to SQLite DB file (Overrides config)")
    
    args = parser.parse_args()
    config = load_config()

    # CLI 인자가 있으면 config 덮어쓰기
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