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
1. **Metadata mode (`--mode metadata` or `--init`)**: connects to the source DB, extracts configured schemas, caches metadata in SQLite. Extraction uses only inspector/read queries—source DBs are never mutated.
2. **Port mode (`--mode port` or `--run`, default)**: reads SQL files from `project.source_dir`, performs transpile/review/verify inside PostgreSQL transactions (BEGIN/ROLLBACK), writes to `project.target_dir`, updates `migration_logs` with `PENDING/DONE/FAILED/...` status. Dangerous statements (DROP/INSERT/etc.) and procedure calls are skipped unless explicitly allowed in `verification.*`.
3. **Report mode (`--mode report`)**: prints the conversion report from SQLite filtered by `project.name` and optional schema/status filters so you can review skipped statements and retry counts.
4. **Reset logs (`--reset-logs`)**: clears `migration_logs` for the current `project.name` to reprocess files.
5. **Resilience**: reruns skip rows marked `DONE`; retries stop once `project.max_retries` is reached.

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

# 6) Inspect results without rerunning conversions
python src/main.py --mode report --config config.yaml --schema-filter HR
```

## 6) Configuration Reference (config.yaml)
```yaml
project:
  name: "example_project"        # Project label used to scope all SQLite rows and reports
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

verification:
  mode: "port"                    # metadata | port | report (defaults to porting)
  allow_dangerous_statements: false  # If true, run DDL/DML during verification (still inside BEGIN/ROLLBACK)
  allow_procedure_execution: false   # If true, execute CALL/DO/EXECUTE during verification

rules:                                # Free-form guidance strings for the reviewer
  - "Convert Oracle NVL to COALESCE."
  - "Replace SYSDATE with CURRENT_TIMESTAMP."
```

## 7) SQLite Schema
- **schema_objects**: `obj_id` (PK), `project_name`, `schema_name`, `obj_name`, `obj_type`, `ddl_script`, `source_code`, `extracted_at`. Uniqueness on `(project_name, schema_name, obj_name, obj_type)`.
- **migration_logs / report source**: `project_name`, `file_path`, `detected_schemas`, `status`, `retry_count`, `last_error_msg`, `target_path`, `skipped_statements`, `executed_statements`, `updated_at`. Unique on `(project_name, file_path)` so multiple projects can reuse one SQLite file.
  - `detected_schemas` is derived from parsed SQL references so reports can be filtered by schema lineage without cross-project collisions.

## 8) Sample Assets
- `sample/config.sample.yaml`: ready-to-copy baseline with Oracle➜Postgres defaults.
- `sample/queries/*.sql`: three test queries (simple select, join+decode, function call) to validate the workflow. Copy them into `./input` for a dry run.

## 9) Logging & Troubleshooting
- Tuning: adjust `logging.level` for verbosity or override via `--log-level` (or `ANY2PG_LOG_LEVEL`). File output path can be set per run with `--log-file` (`ANY2PG_LOG_FILE`) or in YAML; parent directories are created automatically when needed.
- Targeted tracing: use `logging.module_levels` to crank up only the noisy components (e.g., `agents.workflow` for stage-by-stage traces, `modules.context_builder` for context queries).
- Verification safety: verifier wraps execution in explicit `BEGIN`/`ROLLBACK`; dangerous DDL/DML and procedure calls are skipped unless `verification.allow_dangerous_statements` / `verification.allow_procedure_execution` are enabled. Statement timeout is configurable. Data parity is **not** auto-checked—compare source/target data manually after review.
- Adapter issues: most adapters rely on SQLAlchemy inspectors; missing dialect drivers will raise import/connection errors—install the correct driver for your source DB.
- Resume logic: if a file remains in `FAILED`/`VERIFY_FAIL`, inspect `migration_logs.last_error_msg` and increase `project.max_retries` if needed.
- Config validation: startup enforces required keys (`project.*`, `database.{source,target}.uri`, `llm.*`) and rejects `max_retries < 1` so misconfigured runs fail fast with actionable errors.
- Deterministic context: the RAG context builder orders schema objects consistently (`schema_name`, `obj_type`, `obj_name`) so reviewer prompts are reproducible across runs.
- Reporting: `--mode report --schema-filter HR` prints SQLite-backed results for the active `project.name` (no cross-project leakage) including skipped statements and retry counts.

## 10) Quality Gates & Testing
- Defensive config validation (paths expanded, retries coerced to integer, required keys enforced) guards against accidental misconfiguration.
- SQLite operations are fully transactional—write operations roll back on errors to avoid partially persisted metadata.
- RAG context builder ignores unparsable SQL safely and only emits objects that provide DDL/source text.
- Run the full suite locally with `python -m pytest`; set `POSTGRES_TEST_DSN` to enable live PostgreSQL smoke tests.

## 11) Developer Notes
- Primary code lives under `src/modules/` (metadata_extractor, context_builder, postgres_verifier, adapters). LangGraph workflow and prompts sit under `src/agents/`.
- Backward compatibility shims: `src/context_builder_shim.py` and `src/postgres_verifier_shim.py` re-export their implementations—new code should import from `src/modules/` paths directly.
- All comments/docstrings are in English for consistency; user-facing CLI messages remain bilingual where helpful.

## 12) Live Database Verification (PostgreSQL / Oracle)
- PostgreSQL smoke tests: set `POSTGRES_TEST_DSN` (e.g., `postgresql://user:pass@localhost:5432/any2pg_test`) and run `python -m pytest -q` to execute the `tests/integration/test_postgres_live.py` suite. The verifier runs inside a transaction and rolls back every statement.
- Oracle smoke tests: an Oracle instance is required but not bundled; set `ORACLE_TEST_DSN` and add analogous fixtures before enabling end-to-end Oracle checks.
