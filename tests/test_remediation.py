import json
from pathlib import Path

from longrun_agent.gates.engine import GateResult
from longrun_agent.gates.remediation import RemediationEngine


def test_remediation_engine_rolls_back_feature_list_and_writes_report(tmp_path: Path) -> None:
    state_dir = tmp_path / ".longrun"
    state_dir.mkdir(parents=True, exist_ok=True)
    feature_file = tmp_path / "feature_list.json"

    before_features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    tampered = [
        {"category": "functional", "description": "tampered", "steps": ["s1"], "passes": True},
    ]
    feature_file.write_text(json.dumps(tampered, indent=2))

    engine = RemediationEngine(state_dir=state_dir, feature_file=feature_file)
    gate = GateResult(
        gate_id="feature_immutable_fields",
        passed=False,
        message="description changed",
        remediation=["rollback_feature_list", "write_report", "stop"],
        evidence={"field": "description"},
    )
    outcome = engine.apply(
        session_id=12,
        phase="coding",
        gate=gate,
        before_features=before_features,
    )

    restored = json.loads(feature_file.read_text())
    assert restored[0]["description"] == "A"
    assert "rollback_feature_list" in outcome.actions_applied
    assert outcome.report_path is not None
    report_payload = json.loads(Path(outcome.report_path).read_text())
    assert report_payload["gate"]["gate_id"] == "feature_immutable_fields"
    assert report_payload["session_id"] == 12

