import json
from pathlib import Path

from longrun_agent.gates.checks import (
    check_feature_list_coding_invariants,
    check_required_artifacts_initializer,
)


def test_required_artifacts_initializer_gate_fails_when_artifacts_missing(tmp_path: Path) -> None:
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
        {"category": "functional", "description": "B", "steps": ["s1"], "passes": False},
    ]
    (tmp_path / "feature_list.json").write_text(json.dumps(features, indent=2))
    # init.sh and claude-progress.txt intentionally missing

    result = check_required_artifacts_initializer(project_dir=tmp_path, feature_target=2)

    assert result.passed is False
    assert result.gate_id == "required_artifacts_initializer"
    assert "init.sh" in result.message
    assert "claude-progress.txt" in result.message
    assert "run_repair_session" in result.remediation


def test_feature_list_coding_invariants_gate_detects_immutable_mutation(tmp_path: Path) -> None:
    before_features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    mutated = [
        {"category": "functional", "description": "tampered", "steps": ["s1"], "passes": True},
    ]
    feature_file = tmp_path / "feature_list.json"
    feature_file.write_text(json.dumps(mutated, indent=2))

    result = check_feature_list_coding_invariants(
        feature_file=feature_file,
        before_features=before_features,
        max_features_per_session=1,
    )

    assert result.passed is False
    assert result.gate_id == "feature_immutable_fields"
    assert "rollback_feature_list" in result.remediation

