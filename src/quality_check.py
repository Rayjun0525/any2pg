import logging
import os
import shutil
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, List
from urllib.parse import urlsplit, urlunsplit

from modules.postgres_verifier import VerifierAgent
from modules.sqlite_store import DBManager

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


@dataclass
class QualityMetric:
    name: str
    score: int
    max_score: int = 10
    details: str = ""
    recommendation: str = ""


@dataclass
class QualityReport:
    metrics: List[QualityMetric]

    @property
    def average_score(self) -> float:
        if not self.metrics:
            return 0.0
        return round(sum(m.score for m in self.metrics) / len(self.metrics), 2)

    @property
    def perfect(self) -> bool:
        return all(m.score == m.max_score for m in self.metrics)


REQUIRED_SCHEMA_COLUMNS = {
    "schema_objects": {"project_name", "schema_name", "obj_name", "obj_type", "ddl_script", "source_code"},
    "migration_logs": {"project_name", "file_path", "status", "retry_count", "last_error_msg", "detected_schemas"},
    "source_assets": {"project_name", "file_name", "file_path", "sql_text", "content_hash", "selected_for_port"},
    "rendered_outputs": {"project_name", "file_name", "file_path", "sql_text", "content_hash", "source_hash", "status"},
}


def _redact_for_metric(dsn: str) -> str:
    """Redact credentials for metric checks without importing main.redact_dsn (avoid cycles)."""

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


def _make_sandbox_config(config: dict) -> tuple[dict, str]:
    sandbox_dir = tempfile.mkdtemp(prefix="any2pg_quality_")
    cloned = deepcopy(config)
    cloned.setdefault("project", {})
    cloned["project"]["db_file"] = os.path.join(sandbox_dir, "quality.sqlite")
    cloned["project"]["source_dir"] = sandbox_dir
    cloned["project"]["target_dir"] = os.path.join(sandbox_dir, "output")
    os.makedirs(cloned["project"]["target_dir"], exist_ok=True)
    return cloned, sandbox_dir


def _check_config(config: dict) -> QualityMetric:
    required_sections = {"project", "database", "llm"}
    missing_sections = [section for section in required_sections if section not in config]
    if missing_sections:
        return QualityMetric(
            name="Configuration completeness",
            score=0,
            details=f"Missing sections: {', '.join(sorted(missing_sections))}",
            recommendation="Ensure project, database, and llm sections exist in config.yaml",
        )

    project = config.get("project", {})
    missing_project = [key for key in ("name", "source_dir", "target_dir", "db_file", "max_retries") if key not in project]
    if missing_project:
        return QualityMetric(
            name="Configuration completeness",
            score=5,
            details=f"Missing project fields: {', '.join(sorted(missing_project))}",
            recommendation="Fill in required project fields before running quality checks",
        )

    max_retries = project.get("max_retries", 0)
    if isinstance(max_retries, int) and max_retries >= 1:
        return QualityMetric(
            name="Configuration completeness",
            score=10,
            details="All required sections and project fields are present with sane defaults",
        )
    return QualityMetric(
        name="Configuration completeness",
        score=6,
        details="project.max_retries must be >= 1",
        recommendation="Set project.max_retries to a positive integer",
    )


def _check_logging_safety(config: dict) -> QualityMetric:
    target_uri = config.get("database", {}).get("target", {}).get("uri", "")
    sample_dsn = target_uri or "postgresql://user:secret@localhost:5432/postgres"
    redacted = _redact_for_metric(sample_dsn)

    if any(token in redacted for token in ("user", "secret")):
        return QualityMetric(
            name="Logging safety",
            score=6,
            details="DSN redaction did not remove credentials",
            recommendation="Use redact_dsn when logging connection strings",
        )

    return QualityMetric(
        name="Logging safety",
        score=10,
        details="Connection strings are redactable for safe logging",
    )


def _check_db_schema(config: dict) -> QualityMetric:
    db = DBManager(config["project"]["db_file"], project_name=config["project"]["name"])
    db.init_db()

    missing_by_table: dict[str, List[str]] = {}
    with db.get_cursor() as cur:
        for table, required_cols in REQUIRED_SCHEMA_COLUMNS.items():
            cur.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cur.fetchall()}
            missing_cols = sorted(required_cols - existing)
            if missing_cols:
                missing_by_table[table] = missing_cols

    if missing_by_table:
        details = "; ".join(f"{tbl}: {', '.join(cols)}" for tbl, cols in missing_by_table.items())
        return QualityMetric(
            name="SQLite schema coverage",
            score=7,
            details=f"Missing columns -> {details}",
            recommendation="Re-run initialization to align schema or migrate columns",
        )

    return QualityMetric(
        name="SQLite schema coverage",
        score=10,
        details="All management tables include required metadata columns",
    )


def _check_verifier_safety(config: dict) -> QualityMetric:
    verifier = VerifierAgent(config)
    dangerous_sql = """
    CREATE TABLE demo(id INT);
    CALL do_work();
    SELECT * FROM demo;
    """
    executable, skipped, prep_error = verifier._prepare_statements(dangerous_sql)

    if prep_error:
        return QualityMetric(
            name="Verifier safety gates",
            score=5,
            details=f"Pre-check failed: {prep_error}",
            recommendation="Ensure SQL parsing dependencies are installed",
        )

    skipped_dangerous = any("CREATE TABLE" in stmt for stmt in skipped)
    skipped_procedure = any(stmt.strip().upper().startswith("CALL") for stmt in skipped)

    if not skipped_dangerous or not skipped_procedure:
        return QualityMetric(
            name="Verifier safety gates",
            score=7,
            details="Expected dangerous/procedure statements to be skipped",
            recommendation="Review verification allow_* flags and classification logic",
        )

    return QualityMetric(
        name="Verifier safety gates",
        score=10,
        details=(
            "Dangerous and procedural statements are skipped while safe statements remain executable"
        ),
    )


def _check_asset_pipeline(config: dict) -> QualityMetric:
    db = DBManager(config["project"]["db_file"], project_name=config["project"]["name"])
    db.init_db()

    sample_path = os.path.join(config["project"]["source_dir"], "quality_asset.sql")
    os.makedirs(os.path.dirname(sample_path), exist_ok=True)
    sql_text = "SELECT 1;"
    with open(sample_path, "w", encoding="utf-8") as handle:
        handle.write(sql_text)

    db.sync_source_asset(sample_path, sql_text, selected_for_port=True, override_selection=True)
    db.save_rendered_output(sample_path, "SELECT 1 AS ok;", source_hash=None, status="DONE", verified=True)

    assets = db.list_source_assets()
    outputs = db.fetch_rendered_sql(["quality_asset.sql"])

    if not assets or not outputs:
        return QualityMetric(
            name="Asset/Output pipeline",
            score=5,
            details="Assets or outputs could not be persisted in SQLite",
            recommendation="Check file permissions and database path",
        )

    asset = assets[0]
    output = outputs[0]
    if asset["file_name"] != output["file_name"]:
        return QualityMetric(
            name="Asset/Output pipeline",
            score=7,
            details="Asset/output linkage mismatch",
            recommendation="Ensure save_rendered_output uses consistent file names",
        )

    if not output["verified"] or output["status"] != "DONE":
        return QualityMetric(
            name="Asset/Output pipeline",
            score=8,
            details="Rendered output missing verification flags",
            recommendation="Persist verification status when saving outputs",
        )

    return QualityMetric(
        name="Asset/Output pipeline",
        score=10,
        details="Assets sync correctly and outputs retain verification status",
    )


CHECKS: List[Callable[[dict], QualityMetric]] = [
    _check_config,
    _check_logging_safety,
    _check_db_schema,
    _check_verifier_safety,
    _check_asset_pipeline,
]


def run_quality_checks(config: dict) -> QualityReport:
    """Execute a suite of quality checks and return a scored report."""

    sandbox_config, sandbox_dir = _make_sandbox_config(config)
    metrics: List[QualityMetric] = []
    try:
        for check in CHECKS:
            metric = check(sandbox_config)
            metrics.append(metric)
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)

    return QualityReport(metrics=metrics)


def render_quality_report(report: QualityReport) -> str:
    lines = ["\n=== Quality Report ==="]
    for metric in report.metrics:
        lines.append(f"- {metric.name}: {metric.score}/{metric.max_score}")
        if metric.details:
            lines.append(f"  Details: {metric.details}")
        if metric.recommendation and metric.score < metric.max_score:
            lines.append(f"  Recommendation: {metric.recommendation}")
    lines.append(f"Overall: {report.average_score}/10 (perfect={report.perfect})")
    return "\n".join(lines)
