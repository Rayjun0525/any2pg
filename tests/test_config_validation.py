import pytest

from src.main import redact_dsn, validate_config


def _base_config(tmp_path):
    return {
        "project": {
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


def test_validate_config_normalizes_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "nested"))
    config = _base_config(tmp_path)
    config["project"]["target_dir"] = "$DATA_ROOT/output"
    config["logging"]["file"] = "$DATA_ROOT/logs/app.log"

    validated = validate_config(config)

    assert validated["project"]["target_dir"].endswith("nested/output")
    assert validated["logging"]["file"].endswith("nested/logs/app.log")


@pytest.mark.parametrize(
    "field, message",
    [
        ("source_dir", "project.source_dir"),
        ("target_dir", "project.target_dir"),
        ("db_file", "project.db_file"),
        ("max_retries", "project.max_retries"),
    ],
)
def test_validate_config_requires_project_fields(tmp_path, field, message):
    config = _base_config(tmp_path)
    config["project"].pop(field)

    with pytest.raises(ValueError, match=message):
        validate_config(config)


def test_validate_config_rejects_non_positive_retries(tmp_path):
    config = _base_config(tmp_path)
    config["project"]["max_retries"] = 0

    with pytest.raises(ValueError, match="max_retries must be >= 1"):
        validate_config(config)


def test_redact_dsn_strips_credentials():
    dsn = "postgresql://user:secret@db.example.com:5432/postgres"

    assert redact_dsn(dsn) == "postgresql://db.example.com:5432/postgres"
