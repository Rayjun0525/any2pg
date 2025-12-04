import logging

from src.main import apply_logging_overrides, configure_logging


def test_configure_logging_creates_parent_directory_and_honors_level(tmp_path):
    log_path = tmp_path / "nested" / "logs" / "any2pg.log"
    config = {
        "logging": {
            "level": "WARNING",
            "file": str(log_path),
            "format": "%(levelname)s:%(message)s",
        }
    }

    configure_logging(config)

    logger = logging.getLogger("Any2PG")
    logger.warning("hello")

    assert log_path.parent.exists()
    assert log_path.exists()
    assert log_path.read_text(encoding="utf-8").strip().endswith("hello")

    assert logging.getLogger().getEffectiveLevel() == logging.WARNING


def test_apply_logging_overrides_prefers_cli_then_env(monkeypatch):
    base_config = {"logging": {"level": "INFO", "file": "from_config.log"}}
    args = type("Args", (), {"log_level": None, "log_file": None})

    monkeypatch.setenv("ANY2PG_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ANY2PG_LOG_FILE", "from_env.log")

    apply_logging_overrides(base_config, args)
    assert base_config["logging"]["level"] == "DEBUG"
    assert base_config["logging"]["file"] == "from_env.log"

    args.log_level = "ERROR"
    args.log_file = "from_cli.log"
    apply_logging_overrides(base_config, args)

    assert base_config["logging"]["level"] == "ERROR"
    assert base_config["logging"]["file"] == "from_cli.log"

