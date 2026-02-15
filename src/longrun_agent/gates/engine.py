"""Gate result models and helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GateResult:
    """Normalized result for one gate evaluation."""

    gate_id: str
    passed: bool
    message: str
    remediation: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


def passing_gate(gate_id: str) -> GateResult:
    return GateResult(gate_id=gate_id, passed=True, message="ok")

