"""Directory-based SQL conversion pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

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
    for _ in range(max_rounds):
        analysis = semantic.analyze(current)
        postgres_sql = writer.write_postgres(current, analysis, patterns)
        validation = validator.validate(postgres_sql)
        if not validation.get("approved", False):
            current = validation.get("sql", postgres_sql)
            continue
        candidate = validation["sql"]
        exec_result = executor.execute(candidate)
        if executor.enabled and exec_result.lower().startswith("execution error"):
            current = candidate  # feedback loop with error info
            continue
        meta.save_pattern(original, candidate)
        knowledge.internalize(analysis, candidate)
        print(exec_result)
        return candidate
    raise RuntimeError("conversion failed")


def iter_sql_files(input_dir: Path) -> Iterable[Path]:
    """Yield all ``.sql`` files under ``input_dir``."""
    yield from input_dir.rglob("*.sql")


def convert_directory(input_dir: str, output_dir: str, llm_config: dict, config: dict) -> None:
    """Convert all SQL files in ``input_dir`` and write PostgreSQL versions to ``output_dir``."""
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

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

    for sql_file in iter_sql_files(in_path):
        relative = sql_file.relative_to(in_path)
        target = out_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"Converting {sql_file} -> {target}")

        sql_text = sql_file.read_text(encoding="utf-8")
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
        target.write_text(converted, encoding="utf-8")
        print(f"Written {target}")
