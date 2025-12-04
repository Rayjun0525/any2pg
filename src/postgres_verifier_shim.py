"""Verifier compatibility wrapper.

This thin shim keeps ``import verifier`` working after moving the implementation
to ``src/modules/postgres_verifier.py``. The functionality was not removed; new
code should import ``VerifierAgent`` from ``modules.postgres_verifier`` directly.
"""

from modules.postgres_verifier import VerifierAgent

__all__ = ["VerifierAgent"]
