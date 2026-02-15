"""Deterministic post-session gate checks."""

from __future__ import annotations

from pathlib import Path

from longrun_agent.feature_list import (
    detect_forbidden_mutations,
    load_feature_list,
    progress_counts,
    validate_feature_schema,
)

from .engine import GateResult, passing_gate


def check_required_artifacts_initializer(project_dir: Path, feature_target: int) -> GateResult:
    """Require initializer artifacts before proceeding."""
    missing: list[str] = []
    feature_file = project_dir / "feature_list.json"
    init_script = project_dir / "init.sh"
    progress_file = project_dir / "claude-progress.txt"

    if not feature_file.exists():
        missing.append("feature_list.json")
    if not init_script.exists():
        missing.append("init.sh")
    if not progress_file.exists():
        missing.append("claude-progress.txt")

    if missing:
        return GateResult(
            gate_id="required_artifacts_initializer",
            passed=False,
            message=f"initializer missing required artifacts: {', '.join(missing)}",
            remediation=["write_report", "run_repair_session", "stop"],
            evidence={"missing": missing},
        )

    try:
        features = load_feature_list(feature_file)
    except Exception as exc:
        return GateResult(
            gate_id="required_artifacts_initializer",
            passed=False,
            message=f"initializer produced invalid feature_list.json: {exc}",
            remediation=["write_report", "run_repair_session", "stop"],
        )

    if len(features) < feature_target:
        return GateResult(
            gate_id="required_artifacts_initializer",
            passed=False,
            message=(
                f"initializer created only {len(features)} features; "
                f"target is {feature_target}"
            ),
            remediation=["write_report", "run_repair_session", "stop"],
            evidence={"count": len(features), "target": feature_target},
        )

    return passing_gate("required_artifacts_initializer")


def check_feature_list_coding_invariants(
    feature_file: Path,
    before_features: list[dict],
    max_features_per_session: int,
) -> GateResult:
    """Enforce coding-phase feature_list invariants."""
    if not feature_file.exists():
        return GateResult(
            gate_id="feature_list_exists",
            passed=False,
            message="feature_list.json is missing after coding session",
            remediation=["write_report", "stop"],
        )

    try:
        after_features = load_feature_list(feature_file)
    except Exception as exc:
        return GateResult(
            gate_id="feature_schema_valid",
            passed=False,
            message=f"feature_list.json is invalid: {exc}",
            remediation=["rollback_feature_list", "write_report", "stop"],
        )

    schema_issues = validate_feature_schema(after_features)
    if schema_issues:
        return GateResult(
            gate_id="feature_schema_valid",
            passed=False,
            message=f"feature_list schema issues after coding session: {schema_issues[0]}",
            remediation=["rollback_feature_list", "write_report", "stop"],
        )

    mutation_issues = detect_forbidden_mutations(before_features, after_features)
    if mutation_issues:
        return GateResult(
            gate_id="feature_immutable_fields",
            passed=False,
            message=f"forbidden feature_list mutation detected: {mutation_issues[0]}",
            remediation=["rollback_feature_list", "write_report", "stop"],
            evidence={"issue": mutation_issues[0]},
        )

    before_passing, _ = progress_counts(before_features)
    after_passing, _ = progress_counts(after_features)
    delta = after_passing - before_passing
    if max_features_per_session > 0 and delta > max_features_per_session:
        return GateResult(
            gate_id="max_features_per_session",
            passed=False,
            message=f"max features per session exceeded: {delta} > {max_features_per_session}",
            remediation=["rollback_feature_list", "write_report", "stop"],
            evidence={"delta": delta, "max_allowed": max_features_per_session},
        )

    return passing_gate("feature_list_coding_invariants")

