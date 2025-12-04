# Any2PG

Hybrid SQL migration toolkit that converts heterogeneous SQL (Oracle/MySQL/etc.) to PostgreSQL, validates the output, and captures metadata for context-aware fixes. Read this README end-to-end to install, configure, and operate the tool without additional references.

## 1) What Any2PG Does
- Multi-stage pipeline: SQLGlot transpile ➜ LLM review/patch ➜ PostgreSQL verification with forced `ROLLBACK`.
- Resume-friendly processing: SQLite keeps file status so reruns skip finished items.
- Metadata-driven RAG: per-schema objects cached in SQLite for precise context retrieval.
- Config-first control: logging, adapters, verification options, and LLM settings are all driven by YAML.

## 2) Architecture at a Glance
- **CLI (src/main.py):** `--init`, `--run`, `--reset-logs`, plus `--config`, `--db-file`, `--log-level`, and `--log-file` overrides.
- **Extractor (src/modules/metadata_extractor.py):** walks configured schemas via adapter instances, stores results in SQLite (`schema_objects`, `migration_logs`).
- **RAG Context Builder (src/modules/context_builder.py):** parses SQL with SQLGlot, fetches related objects by name + schema from SQLite.
- **Workflow (src/agents/workflow.py):** LangGraph-driven state machine for transpile ➜ review ➜ verify ➜ corrective rewrite.
- **Verifier (src/modules/postgres_verifier.py):** executes statements on PostgreSQL with `autocommit=False` and unconditional rollback; shims live in `src/context_builder_shim.py` and `src/postgres_verifier_shim.py` for legacy imports.

## 3) Supported Source Adapters
| Source DB       | Config `database.source.type` | Adapter Path                      | Extracts Tables/Views | Extracts Routines |
|-----------------|-------------------------------|-----------------------------------|-----------------------|-------------------|
| Oracle          | `oracle`                      | `src/modules/adapters/oracle.py`  | ✅ via SQLAlchemy inspector | ✅ via `USER_SOURCE` aggregation |
| MySQL/MariaDB   | `mysql` / `mariadb`           | `src/modules/adapters/mysql.py`   | ✅ via SQLAlchemy inspector | ✅ via `information_schema.ROUTINES` |
| Microsoft SQL Server | `mssql`                  | `src/modules/adapters/mssql.py`   | ✅ (`dbo` default)     | ✅ via `sys.objects`/`sys.sql_modules` |
| IBM DB2         | `db2`                         | `src/modules/adapters/db2.py`     | ✅ (uppercased schema) | ✅ via `SYSCAT.ROUTINES` |
| SAP HANA        | `hana`                        | `src/modules/adapters/hana.py`    | ✅ via inspector       | ✅ via `SYS.PROCEDURES` |
| Snowflake       | `snowflake`                   | `src/modules/adapters/snowflake.py` | ✅ via inspector     | ✅ via `information_schema.routines` |
| MongoDB         | `mongodb`                     | `src/modules/adapters/mongodb.py` | ✅ collections as tables | 🚫 (not supported) |

> Tip: Unknown adapters raise `ValueError`; add new implementations under `src/modules/adapters/` by subclassing `BaseDBAdapter`.

## 4) End-to-End Workflow
1. **Init (`--init`)**: connects to the source DB, extracts configured schemas, caches metadata in SQLite.
2. **Run (`--run`)**: reads SQL files from `project.source_dir`, performs transpile/review/verify, writes to `project.target_dir`, updates `migration_logs` with `PENDING/DONE/FAILED/...` status.
3. **Reset logs (`--reset-logs`)**: clears `migration_logs` to reprocess files.
4. **Resilience**: reruns skip rows marked `DONE`; retries stop once `project.max_retries` is reached.

## 5) Quickstart
```bash
# 0) Install deps
python -m pip install -r requirements.txt

# 1) Inspect options
python src/main.py --help

# 2) Copy sample config and adjust connection URIs/schemas
cp sample/config.sample.yaml ./config.yaml

# 3) Initialize metadata DB (override defaults if desired)
python src/main.py --init --config config.yaml --db-file "./project_A.db" --log-level DEBUG --log-file "./logs/any2pg.log"

# 4) Run conversion (can resume after interruption)
python src/main.py --run --config config.yaml --db-file "./project_A.db"

# 5) Reset statuses when you want to rerun all files
python src/main.py --reset-logs --config config.yaml --db-file "./project_A.db"
```

## 6) Configuration Reference (config.yaml)
```yaml
project:
  source_dir: "./input"            # Where original SQL files live
  target_dir: "./output"           # Where converted SQL will be written
  db_file: "./migration.db"        # Default SQLite path (override via --db-file)
  max_retries: 5                    # Stop correction loop after this many failures

logging:
  level: "INFO"                     # DEBUG, INFO, WARNING, ERROR
  module_levels:                    # Optional per-module overrides for deep tracing
    agents.workflow: "DEBUG"
    modules.context_builder: "DEBUG"
  file: "./any2pg.log"             # Empty string logs to console only
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  max_bytes: 1048576                # Rotate after ~1MB
  backup_count: 3                   # How many rotated files to keep

database:
  source:
    type: "oracle"                 # See adapter table above
    uri: "oracle+oracledb://user:pass@host:1521/?service_name=xe"
    schemas: ["HR", "SCOTT"]       # Schema list; omit to use DB defaults
  target:
    type: "postgres"
    uri: "postgresql://user:pass@localhost:5432/postgres"
    statement_timeout_ms: 5000      # Optional PG statement_timeout during verification

llm:
  provider: "ollama"               # Interface handled by LangChain
  model: "gemma:7b"
  base_url: "http://localhost:11434"
  temperature: 0.1

rules:                                # Free-form guidance strings for the reviewer
  - "Convert Oracle NVL to COALESCE."
  - "Replace SYSDATE with CURRENT_TIMESTAMP."
```

## 7) SQLite Schema
- **schema_objects**: `obj_id` (PK), `schema_name`, `obj_name`, `obj_type`, `ddl_script`, `source_code`, `extracted_at`. Uniqueness on `(schema_name, obj_name, obj_type)`.
- **migration_logs**: `file_path` (PK), `status`, `retry_count`, `last_error_msg`, `target_path`, `updated_at`.

## 8) Sample Assets
- `sample/config.sample.yaml`: ready-to-copy baseline with Oracle➜Postgres defaults.
- `sample/queries/*.sql`: three test queries (simple select, join+decode, function call) to validate the workflow. Copy them into `./input` for a dry run.

## 9) Logging & Troubleshooting
- Tuning: adjust `logging.level` for verbosity or override via `--log-level` (or `ANY2PG_LOG_LEVEL`). File output path can be set per run with `--log-file` (`ANY2PG_LOG_FILE`) or in YAML; parent directories are created automatically when needed.
- Targeted tracing: use `logging.module_levels` to crank up only the noisy components (e.g., `agents.workflow` for stage-by-stage traces, `modules.context_builder` for context queries).
- Verification safety: verifier uses `autocommit=False` and always rolls back; tune `statement_timeout_ms` if long-running statements occur.
- Adapter issues: most adapters rely on SQLAlchemy inspectors; missing dialect drivers will raise import/connection errors—install the correct driver for your source DB.
- Resume logic: if a file remains in `FAILED`/`VERIFY_FAIL`, inspect `migration_logs.last_error_msg` and increase `project.max_retries` if needed.

## 10) Developer Notes
- Primary code lives under `src/modules/` (metadata_extractor, context_builder, postgres_verifier, adapters). LangGraph workflow and prompts sit under `src/agents/`.
- Backward compatibility shims: `src/context_builder_shim.py` and `src/postgres_verifier_shim.py` re-export their implementations—new code should import from `src/modules/` paths directly.
- All comments/docstrings are in English for consistency; user-facing CLI messages remain bilingual where helpful.

## 11) Live Database Verification (PostgreSQL / Oracle)
- PostgreSQL smoke tests: set `POSTGRES_TEST_DSN` (e.g., `postgresql://user:pass@localhost:5432/any2pg_test`) and run `python -m pytest -q` to execute the `tests/integration/test_postgres_live.py` suite. The verifier runs inside a transaction and rolls back every statement.
- Oracle smoke tests: an Oracle instance is required but not bundled; set `ORACLE_TEST_DSN` and add analogous fixtures before enabling end-to-end Oracle checks.
