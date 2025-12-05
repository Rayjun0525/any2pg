import copy
from pathlib import Path

from src.main import validate_config
from src.quality_check import QualityReport, run_quality_checks


def _base_config(tmp_path: Path) -> dict:
    return {
        "project": {
            "name": "quality_project",
            "source_dir": str(tmp_path / "input"),
            "target_dir": str(tmp_path / "output"),
            "db_file": str(tmp_path / "db.sqlite"),
            "max_retries": 2,
        },
        "database": {
            "source": {"type": "oracle", "uri": "oracle://u:p@host:1521/xepdb1"},
            "target": {"type": "postgres", "uri": "postgresql://u:p@localhost/db"},
        },
        "llm": {"provider": "ollama", "model": "gemma:7b", "base_url": "http://localhost:11434"},
        "logging": {"level": "INFO", "file": ""},
    }


def test_quality_checks_are_perfect(tmp_path):
    config = validate_config(copy.deepcopy(_base_config(tmp_path)))

    report = run_quality_checks(config)

    assert isinstance(report, QualityReport)
    assert report.perfect is True
    assert report.average_score == 10
    assert all(metric.score == metric.max_score for metric in report.metrics)
