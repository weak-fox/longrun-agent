"""Hard-gate checks and remediation primitives."""

from .engine import GateResult
from .remediation import RemediationEngine, RemediationOutcome

__all__ = ["GateResult", "RemediationEngine", "RemediationOutcome"]

