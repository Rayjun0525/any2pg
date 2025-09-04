"""Command line interface for any2pg."""
from __future__ import annotations

import argparse

import yaml

from any2pg import convert_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Translate SQL files to PostgreSQL.")
    parser.add_argument("input", help="Input directory containing SQL files")
    parser.add_argument("output", help="Output directory for PostgreSQL SQL files")
    parser.add_argument(
        "--model",
        default="gpt-oss:20b",
        help="Ollama model to use (default: gpt-oss:20b)",
    )
    parser.add_argument(
        "--ollama-base-url",
        default="http://localhost:11434",
        help="Base URL for the Ollama server",
    )
    parser.add_argument(
        "--config",
        help="YAML configuration file for execution and meta options",
    )
    parser.add_argument(
        "-p",
        "--progress",
        action="store_true",
        help="Show progress while converting files",
    )
    parser.add_argument(
        "--log-dir",
        help="Directory to write conversion logs",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    llm_config = {
        "model": args.model,
        "api_type": "ollama",
        "base_url": args.ollama_base_url,
    }
    config = {}
    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    convert_directory(
        args.input,
        args.output,
        llm_config,
        config,
        progress=args.progress,
        log_dir=args.log_dir,
    )


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
