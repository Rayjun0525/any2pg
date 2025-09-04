"""Directory-based SQL conversion pipeline."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable


logger = logging.getLogger("any2pg")

from .agents import (
    ExecutionAgent,
    KnowledgeAgent,
    MetaInfoAgent,
    QueryWritingAgent,
    SemanticAnalysisAgent,
    ValidationAgent,
)

def convert_sql(
    sql: str,
    *,
    semantic: SemanticAnalysisAgent,
    writer: QueryWritingAgent,
    validator: ValidationAgent,
    knowledge: KnowledgeAgent,
    executor: ExecutionAgent,
    meta: MetaInfoAgent,
    max_rounds: int = 3,
) -> str:
    """Convert a single SQL string into PostgreSQL SQL using the agent pipeline.

    A simple feedback loop retries validation/execution errors up to ``max_rounds`` times.
    """

    patterns = meta.load_patterns()
    current = sql
    original = sql
    last_error = "unknown error"
    for round_no in range(1, max_rounds + 1):
        analysis = semantic.analyze(current)
        postgres_sql = writer.write_postgres(current, analysis, patterns)
        validation = validator.validate(postgres_sql)
        if not validation.get("approved", False):
            last_error = validation.get("reason", "validation rejected")
            logger.warning("Round %d validation failed: %s", round_no, last_error)
            current = validation.get("sql", postgres_sql)
            continue
        candidate = validation["sql"]
        exec_result = executor.execute(candidate)
        if executor.enabled and exec_result.lower().startswith("execution error"):
            last_error = exec_result
            logger.warning("Round %d execution failed: %s", round_no, exec_result)
            current = candidate  # feedback loop with error info
            continue
        meta.save_pattern(original, candidate)
        knowledge.internalize(analysis, candidate)
        logger.info(exec_result)
        return candidate
    logger.error("Conversion failed after %d rounds: %s", max_rounds, last_error)
    raise RuntimeError(f"conversion failed: {last_error}")


def iter_sql_files(input_dir: Path) -> Iterable[Path]:
    """Yield all ``.sql`` files under ``input_dir``."""
    yield from input_dir.rglob("*.sql")


def convert_directory(
    input_dir: str,
    output_dir: str,
    llm_config: dict,
    config: dict,
    *,
    progress: bool = False,
    log_dir: str | None = None,
) -> None:
    """Convert all SQL files in ``input_dir`` and write PostgreSQL versions to ``output_dir``."""
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        logger.addHandler(
            logging.FileHandler(log_path / "conversion.log", encoding="utf-8")
        )

    exec_cfg = config.get("execute", {})
    meta_cfg = config.get("meta", {})
    max_rounds = config.get("max_rounds", 3)

    semantic = SemanticAnalysisAgent("semantic", "You analyze SQL semantics.", llm_config)
    writer = QueryWritingAgent("writer", "You translate SQL to PostgreSQL.", llm_config)
    validator = ValidationAgent("validator", "You validate PostgreSQL syntax.", llm_config)
    knowledge = KnowledgeAgent("knowledge", "You store conversion knowledge.", llm_config)
    executor = ExecutionAgent(
        "executor",
        "You verify execution results.",
        llm_config,
        dsn=exec_cfg.get("dsn"),
        enabled=exec_cfg.get("enabled", False),
    )
    meta = MetaInfoAgent(
        "meta",
        "You manage reusable SQL patterns.",
        llm_config,
        path=meta_cfg.get("path"),
        enabled=meta_cfg.get("enabled", False),
    )

    files = list(iter_sql_files(in_path))
    total = len(files)
    for idx, sql_file in enumerate(files, start=1):
        relative = sql_file.relative_to(in_path)
        target = out_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if progress:
            logger.info(
                "[%d/%d] Converting %s -> %s", idx, total, sql_file, target
            )
        else:
            logger.info("Converting %s -> %s", sql_file, target)

        sql_text = sql_file.read_text(encoding="utf-8")
        try:
            converted = convert_sql(
                sql_text,
                semantic=semantic,
                writer=writer,
                validator=validator,
                knowledge=knowledge,
                executor=executor,
                meta=meta,
                max_rounds=max_rounds,
            )
        except Exception as exc:
            logger.error("Failed to convert %s: %s", sql_file, exc)
            continue
        target.write_text(converted, encoding="utf-8")
        if progress:
            logger.info("[%d/%d] Written %s", idx, total, target)
        else:
            logger.info("Written %s", target)
