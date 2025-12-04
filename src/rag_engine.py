"""Backward compatibility wrapper for RAG context builder.

This shim keeps historical imports like ``import rag_engine`` working after the
implementation moved to ``src/modules/rag_engine.py``. New code should import
``RAGContextBuilder`` from ``modules.rag_engine`` directly.
"""

from modules.rag_engine import RAGContextBuilder

__all__ = ["RAGContextBuilder"]
