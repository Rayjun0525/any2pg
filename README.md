# Any2PG

[한국어](README_KR.md) | **English**

A hybrid SQL migration toolkit that converts heterogeneous SQL (Oracle/MySQL/MSSQL/etc.) to PostgreSQL with AI-powered review and verification.

## 🎯 Key Features

- **Multi-stage Pipeline**: SQLGlot auto-conversion ➜ LLM review/fix ➜ PostgreSQL verification (auto-rollback)
- **Resume-friendly Processing**: SQLite-based state management for interruptible workflows
- **Metadata-driven RAG**: Schema-aware context for precise SQL conversions
- **K9s-style TUI**: Intuitive terminal UI for visual workflow management
- **Config-first Control**: YAML-based configuration for all operations

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  Any2PG Migration Pipeline                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1️⃣ Metadata Collection                                     │
│  Source DB ─► SQLAlchemy Inspector ─► SQLite (schema_objects) │
│  (Tables, Views, Indexes, Procedures, Functions, Triggers) │
│                                                             │
│  2️⃣ Conversion Mode Selection                               │
│  ┌─────────────┐         ┌───────────────────┐             │
│  │ FAST Mode   │         │ AGENT Mode (LLM)  │             │
│  │ SQLGlot only│         │ Review ↔ Convert  │             │
│  └─────────────┘         └───────────────────┘             │
│         │                        │                         │
│         └────────┬───────────────┘                         │
│                  ↓                                         │
│  3️⃣ Verification (PostgreSQL)                               │
│  BEGIN ─► Execute ─► ROLLBACK (Safe verification)          │
│                  ↓                                         │
│  4️⃣ Result Storage                                          │
│  SQLite (rendered_outputs) + File export (optional)        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 📦 Installation

```bash
# 1. Clone repository
git clone https://github.com/your-repo/any2pg.git
cd any2pg

# 2. Install dependencies
pip install -r requirements.txt

# 3. Prepare configuration
cp sample/config.sample.yaml config.yaml
# Edit config.yaml with your DB connection details
```

### Oracle Driver Selection

For Oracle DB sources, choose one:

**Option 1: oracledb (Recommended)** - Pure Python driver
```bash
pip install oracledb  # Already in requirements.txt
```

**Option 2: cx_Oracle** - Native driver (requires Oracle Instant Client + C++ Build Tools)
```bash
# Uncomment cx_Oracle in requirements.txt, then:
pip install cx_Oracle
```

## 🚀 Quick Start

### TUI Mode (Recommended)

```bash
python src/main.py --config config.yaml
```

Launches K9s-style interactive UI:
- **Left pane**: Asset (SQL files) list
- **Right pane**: Detail view (Info / SQL / Logs tabs)
- **Navigation**: ↑↓ to move, ←→ to switch tabs, Space to toggle selection

### CLI Mode

```bash
# 1. Collect metadata
python src/main.py --mode metadata

# 2. Run conversion
python src/main.py --mode port

# 3. Check status
python src/main.py --mode status

# 4. Export results
python src/main.py --mode export

# 5. Apply to target DB
python src/main.py --mode apply
```

## ⚙️ Configuration (config.yaml)

```yaml
general:
  project_name: "my_project"
  log_path: "logs/any2pg.log"
  log_level: "INFO"
  mode: "cli"  # cli or tui
  metadata_path: "data/project.db"
  max_retries: 3

database:
  source:
    type: "oracle"  # oracle, mysql, mssql, db2, etc.
    connection_string: "oracle+oracledb://user:pass@host:1521/?service_name=xe"
    schemas:
      - "HR"
      - "SCOTT"
  
  target:
    connection_string: "postgresql://user:pass@localhost:5432/target_db"
    target_schema: "public"

llm:
  provider: "ollama"
  model: "llama3"
  base_url: "http://localhost:11434"
  mode: "fast"  # fast (sqlglot only) or agent (LLM-powered)
```

## 🎨 K9s-Style TUI Usage

### Main Screen
```
┌──────────────────────────────────────────────────────────────┐
│ Any2PG v1.0 | Project: my_project                           │
├──────────────────┬───────────────────────────────────────────┤
│ Asset List       │ Detail View                               │
├──────────────────┼───────────────────────────────────────────┤
│ [X] table1.sql   │ Tab: Info | SQL | Logs                   │
│ [ ] table2.sql   │                                           │
│ [X] proc1.sql    │ File: table1.sql                         │
│                  │ Selected: True                            │
│                  │ Status: DONE                              │
│                  │ Extracted: 2025-12-07 15:30:00           │
└──────────────────┴───────────────────────────────────────────┘
 Tab: Info | Space: toggle select | q: quit
```

### Key Bindings
- `↑/↓` or `j/k`: Navigate asset list
- `←/→` or `h/l`: Switch tabs
- `Space`: Toggle asset selection
- `q` or `ESC`: Quit

## 📋 Supported Source Databases

| Database | Type Value | Adapter | Tables/Views | Procedures/Functions |
|----------|------------|---------|--------------|---------------------|
| Oracle | `oracle` | `oracle.py` | ✅ | ✅ |
| MySQL/MariaDB | `mysql` | `mysql.py` | ✅ | ✅ |
| MS SQL Server | `mssql` | `mssql.py` | ✅ | ✅ |
| IBM DB2 | `db2` | `db2.py` | ✅ | ✅ |
| SAP HANA | `hana` | `hana.py` | ✅ | ✅ |
| Snowflake | `snowflake` | `snowflake.py` | ✅ | ✅ |
| MongoDB | `mongodb` | `mongodb.py` | ✅ | 🚫 |

## 🔄 Workflow

### 1. Metadata Collection
```bash
python src/main.py --mode metadata
```
- Connects to source DB and extracts schema information
- Stores tables, views, indexes, procedures, functions, triggers in SQLite
- **Read-only**: Never modifies source DB

### 2. Conversion Execution

**FAST Mode** (sqlglot only)
```yaml
llm:
  mode: "fast"
```
- Rule-based automatic conversion
- No LLM cost
- Fast processing

**AGENT Mode** (LLM-powered)
```yaml
llm:
  mode: "agent"
```
- AI-powered conversion and review
- RAG-enabled context-aware transformation
- High quality, slower processing

### 3. Verification
- Safely validates on PostgreSQL with `BEGIN` → Execute → `ROLLBACK`
- Dangerous statements (DROP, DELETE, etc.) controlled by config
- Statements requiring transaction control marked with `need_permission` flag

### 4. Application
```bash
python src/main.py --mode apply
```
- Applies verified SQL to target DB
- Can filter by selected assets

## 🗄️ SQLite Schema

Key tables:

### schema_objects
Stores source DB metadata
- `project_name`, `schema_name`, `obj_name`, `obj_type`
- `ddl_script`, `source_code`

### source_assets
SQL files to be converted
- `file_name`, `file_path`, `sql_text`
- `selected_for_port`, `analysis_data`

### rendered_outputs
Conversion results
- `sql_text`, `status`, `verified`
- `review_comments`, `need_permission`, `agent_state`

### execution_logs
Execution logs
- `level`, `event`, `detail`, `created_at`

## 🧪 Testing

```bash
# Unit tests
python -m pytest tests/

# PostgreSQL integration tests (requires real DB)
export POSTGRES_TEST_DSN="postgresql://user:pass@localhost:5432/test_db"
python -m pytest tests/integration/
```

## 🛠️ Developer Guide

### Project Structure
```
any2pg/
├── src/
│   ├── main.py                    # CLI entry point
│   ├── modules/
│   │   ├── sqlite_store.py        # SQLite repository
│   │   ├── metadata_extractor.py  # Metadata collector
│   │   ├── context_builder.py     # RAG context builder
│   │   ├── postgres_verifier.py   # PostgreSQL verification engine
│   │   └── adapters/               # DB adapters
│   ├── agents/
│   │   ├── workflow.py             # LangGraph workflow
│   │   └── prompts.py              # LLM prompts
│   └── ui/
│       └── tui.py                  # K9s-style TUI
├── config.yaml                     # Configuration
└── requirements.txt                # Dependencies
```

### Adding New Adapters

1. Create new file in `src/modules/adapters/`
2. Inherit from `BaseDBAdapter`
3. Implement `get_tables_and_views()`, `get_procedures()`
4. Register in `__init__.py`

## 🔍 Troubleshooting

### Common Issues

**1. Oracle Connection Failure**
```bash
# Using oracledb
pip install oracledb

# Check connection_string format
oracle+oracledb://user:pass@host:1521/?service_name=xe
```

**2. LLM Connection Failure**
```bash
# Check Ollama server
curl http://localhost:11434/api/tags

# Download model
ollama pull llama3
```

**3. Repeated Conversion Failures**
```yaml
general:
  max_retries: 5  # Increase retry count
```

### Log Inspection

```bash
# Enable detailed logging
python src/main.py --mode port --log-level DEBUG

# View log file
tail -f logs/any2pg.log
```

## 📝 License

This project is open source.

## 🤝 Contributing

Bug reports, feature requests, and Pull Requests are welcome!

## 📞 Support

For issues, please create a GitHub Issue.

---

**Made with ❤️ for Database Migration**
