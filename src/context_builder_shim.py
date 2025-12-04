"""Backward compatibility wrapper for the RAG context builder.

This shim keeps historical imports like ``import rag_engine`` working after the
implementation moved to ``src/modules/context_builder.py``. New code should
import ``RAGContextBuilder`` from ``modules.context_builder`` directly.
"""

from modules.context_builder import RAGContextBuilder

__all__ = ["RAGContextBuilder"]
