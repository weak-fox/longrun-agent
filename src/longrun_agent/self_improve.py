"""Self-improvement analysis for longrun-agent sessions."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SessionSnapshot:
    session_id: int
    phase: str
    success: bool
    passing: int
    total: int
    progress_made: bool | None


@dataclass(slots=True)
class SelfImproveReport:
    window: int
    session_count: int
    failure_count: int
    coding_sessions: int
    no_progress_sessions: int
    gate_failures: dict[str, int]


@dataclass(slots=True)
class ImprovementRecommendation:
    recommendation_id: str
    summary: str
    evidence: str
    auto_apply: bool = False
    config_field: str | None = None
    config_value: bool | int | str | None = None


def _session_id_from_name(name: str) -> int:
    try:
        return int(name.split("-")[1])
    except (IndexError, ValueError):
        return -1


def _load_session_snapshot(session_file: Path, fallback_session_id: int) -> SessionSnapshot | None:
    try:
        raw = json.loads(session_file.read_text())
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None

    return SessionSnapshot(
        session_id=int(raw.get("session_id", fallback_session_id)),
        phase=str(raw.get("phase", "")),
        success=bool(raw.get("success", False)),
        passing=int(raw.get("passing", 0)),
        total=int(raw.get("total", 0)),
        progress_made=raw.get("progress_made")
        if isinstance(raw.get("progress_made"), bool) or raw.get("progress_made") is None
        else None,
    )


def _load_gate_failures(remediation_dir: Path, relevant_session_ids: set[int]) -> dict[str, int]:
    if not remediation_dir.exists():
        return {}

    counts: Counter[str] = Counter()
    for report_path in sorted(remediation_dir.glob("session-*.json")):
        session_id = _session_id_from_name(report_path.stem)
        if session_id not in relevant_session_ids:
            continue
        try:
            raw = json.loads(report_path.read_text())
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        gate = raw.get("gate")
        if not isinstance(gate, dict):
            continue
        gate_id = gate.get("gate_id")
        if isinstance(gate_id, str) and gate_id.strip():
            counts[gate_id.strip()] += 1
    return dict(sorted(counts.items()))


def analyze_recent_sessions(state_dir: Path, window: int) -> SelfImproveReport:
    session_root = state_dir / "sessions"
    session_dirs = sorted(
        (path for path in session_root.glob("session-*") if path.is_dir()),
        key=lambda path: _session_id_from_name(path.name),
    )
    if window > 0:
        session_dirs = session_dirs[-window:]

    snapshots: list[SessionSnapshot] = []
    for session_dir in session_dirs:
        session_id = _session_id_from_name(session_dir.name)
        session_file = session_dir / "session.json"
        if not session_file.exists():
            continue
        snapshot = _load_session_snapshot(session_file, fallback_session_id=session_id)
        if snapshot is not None:
            snapshots.append(snapshot)

    failure_count = sum(1 for item in snapshots if not item.success)
    coding_sessions = sum(1 for item in snapshots if item.phase == "coding")
    no_progress_sessions = sum(
        1
        for item in snapshots
        if item.phase == "coding" and item.progress_made is False
    )
    relevant_session_ids = {item.session_id for item in snapshots}
    gate_failures = _load_gate_failures(state_dir / "remediation", relevant_session_ids)

    return SelfImproveReport(
        window=window,
        session_count=len(snapshots),
        failure_count=failure_count,
        coding_sessions=coding_sessions,
        no_progress_sessions=no_progress_sessions,
        gate_failures=gate_failures,
    )


def build_recommendations(
    report: SelfImproveReport,
    *,
    repair_on_verification_failure: bool,
) -> list[ImprovementRecommendation]:
    recommendations: list[ImprovementRecommendation] = []

    verification_failures = report.gate_failures.get("verification_commands_pass", 0)
    if verification_failures > 0 and not repair_on_verification_failure:
        recommendations.append(
            ImprovementRecommendation(
                recommendation_id="enable_repair_on_verification_failure",
                summary="Enable automatic repair after verification gate failures",
                evidence=(
                    f"Detected {verification_failures} verification_commands_pass failures "
                    f"in last {report.session_count} sessions"
                ),
                auto_apply=True,
                config_field="repair_on_verification_failure",
                config_value=True,
            )
        )

    if report.coding_sessions >= 3 and report.no_progress_sessions >= 2:
        recommendations.append(
            ImprovementRecommendation(
                recommendation_id="reduce_no_progress_rework",
                summary=(
                    "Too many coding sessions with no progress; tighten scope and "
                    "require clearer per-session acceptance criteria"
                ),
                evidence=(
                    f"{report.no_progress_sessions}/{report.coding_sessions} coding sessions had "
                    "no progress"
                ),
            )
        )

    pre_coding_failures = report.gate_failures.get("pre_coding_commands_pass", 0)
    if pre_coding_failures >= 2:
        recommendations.append(
            ImprovementRecommendation(
                recommendation_id="stabilize_pre_coding_commands",
                summary="Pre-coding checks fail repeatedly; simplify and harden pre-coding commands",
                evidence=f"Detected {pre_coding_failures} pre_coding_commands_pass failures",
            )
        )

    if not recommendations:
        recommendations.append(
            ImprovementRecommendation(
                recommendation_id="keep_baseline",
                summary="No urgent tuning needed; continue with current settings",
                evidence=f"No high-risk failure pattern in last {report.session_count} sessions",
            )
        )

    return recommendations


def render_plan_markdown(
    report: SelfImproveReport,
    recommendations: list[ImprovementRecommendation],
    *,
    applied_actions: list[str],
) -> str:
    gate_lines = (
        "\n".join(f"- `{gate_id}`: {count}" for gate_id, count in report.gate_failures.items())
        if report.gate_failures
        else "- none"
    )

    recommendation_lines: list[str] = []
    for index, recommendation in enumerate(recommendations, start=1):
        auto_apply = "yes" if recommendation.auto_apply else "no"
        recommendation_lines.extend(
            [
                f"{index}. `{recommendation.recommendation_id}`",
                f"   - summary: {recommendation.summary}",
                f"   - evidence: {recommendation.evidence}",
                f"   - auto-apply: {auto_apply}",
            ]
        )
        if recommendation.config_field is not None:
            recommendation_lines.append(
                "   - config change: "
                f"`gates.{recommendation.config_field} = {recommendation.config_value}`"
            )

    applied_lines = "\n".join(f"- {item}" for item in applied_actions) if applied_actions else "- none"

    return (
        "# Self Improvement Plan\n\n"
        f"Generated at: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"- Window: {report.window}\n"
        f"- Sessions analyzed: {report.session_count}\n"
        f"- Failures: {report.failure_count}\n"
        f"- Coding sessions: {report.coding_sessions}\n"
        f"- Coding sessions with no progress: {report.no_progress_sessions}\n\n"
        "## Gate Failure Signals\n"
        f"{gate_lines}\n\n"
        "## Recommendations\n"
        + "\n".join(recommendation_lines)
        + "\n\n## Applied Actions\n"
        + f"{applied_lines}\n"
    )
