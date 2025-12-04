"""Verifier compatibility wrapper.

This thin shim keeps ``import verifier`` working after moving the implementation
to ``src/modules/verifier.py``. The functionality was not removed; new code
should import ``VerifierAgent`` from ``modules.verifier`` directly.
"""

from modules.verifier import VerifierAgent

__all__ = ["VerifierAgent"]
