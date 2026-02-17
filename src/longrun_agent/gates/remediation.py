"""Remediation execution for failed hard gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from .engine import GateResult


@dataclass(slots=True)
class RemediationOutcome:
    actions_applied: list[str]
    report_path: str | None


class RemediationEngine:
    """Apply deterministic remediation actions and persist failure report."""

    def __init__(self, state_dir: Path, feature_file: Path):
        self.state_dir = state_dir
        self.feature_file = feature_file

    def apply(
        self,
        session_id: int,
        phase: str,
        gate: GateResult,
        before_features: list[dict[str, Any]] | None,
        before_snapshot_path: Path | None = None,
    ) -> RemediationOutcome:
        actions_applied: list[str] = []

        for action in gate.remediation:
            if action == "rollback_feature_list":
                restored = False
                if before_snapshot_path is not None and before_snapshot_path.exists():
                    self.feature_file.write_text(before_snapshot_path.read_text())
                    restored = True
                elif before_features is not None:
                    self.feature_file.write_text(json.dumps(before_features, indent=2) + "\n")
                    restored = True

                if restored:
                    actions_applied.append(action)

        report_path = self._write_report(
            session_id=session_id,
            phase=phase,
            gate=gate,
            actions_applied=actions_applied,
        )
        actions_applied.append("write_report")

        return RemediationOutcome(
            actions_applied=actions_applied,
            report_path=str(report_path),
        )

    def _write_report(
        self,
        session_id: int,
        phase: str,
        gate: GateResult,
        actions_applied: list[str],
    ) -> Path:
        remediation_dir = self.state_dir / "remediation"
        remediation_dir.mkdir(parents=True, exist_ok=True)
        report_path = remediation_dir / f"session-{session_id:04d}.json"
        payload = {
            "session_id": session_id,
            "phase": phase,
            "timestamp": datetime.now(UTC).isoformat(),
            "gate": asdict(gate),
            "actions_applied": actions_applied,
        }
        report_path.write_text(json.dumps(payload, indent=2) + "\n")
        return report_path
