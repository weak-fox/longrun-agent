import json
import stat
import sys
from pathlib import Path

from longrun_agent.harness import Harness, HarnessConfig


def _write_agent_script(path: Path, mode: str) -> Path:
    script = path / f"fake_agent_remediation_{mode}.py"
    if mode == "initializer-missing-artifacts":
        source = """import json, sys
from pathlib import Path
project_dir = Path(sys.argv[1])
phase = sys.argv[2]
if phase == "initializer":
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
        {"category": "functional", "description": "B", "steps": ["s1"], "passes": False},
    ]
    (project_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    raise SystemExit(0)
raise SystemExit(0)
"""
    else:
        source = """import json, sys
from pathlib import Path
project_dir = Path(sys.argv[1])
phase = sys.argv[2]
features_path = project_dir / "feature_list.json"
features = json.loads(features_path.read_text())
if "__MODE__" == "coding-mutate":
    features[0]["description"] = "tampered"
elif "__MODE__" == "coding-pass-one":
    for item in features:
        if not item["passes"]:
            item["passes"] = True
            break
elif "__MODE__" == "verification-repair":
    if phase == "coding":
        for item in features:
            if not item["passes"]:
                item["passes"] = True
                break
    elif phase == "repair":
        (project_dir / "repaired.ok").write_text("ok")
features_path.write_text(json.dumps(features, indent=2))
"""
        source = source.replace("__MODE__", mode)
    script.write_text(source)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_harness_writes_remediation_report_for_initializer_artifact_gate(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build app")
    script = _write_agent_script(tmp_path, mode="initializer-missing-artifacts")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            feature_target=2,
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is False
    assert "required_artifacts_initializer" in result.message
    assert "agent.stdout.log" in result.message
    assert "agent.stderr.log" in result.message
    report = tmp_path / ".longrun" / "remediation" / "session-0001.json"
    assert report.exists()
    payload = json.loads(report.read_text())
    assert payload["gate"]["gate_id"] == "required_artifacts_initializer"


def test_harness_rolls_back_and_reports_on_feature_mutation_gate(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    (tmp_path / "feature_list.json").write_text(json.dumps(features, indent=2))
    script = _write_agent_script(tmp_path, mode="coding-mutate")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is False
    assert "feature_immutable_fields" in result.message
    restored = json.loads((tmp_path / "feature_list.json").read_text())
    assert restored[0]["description"] == "A"
    report = tmp_path / ".longrun" / "remediation" / "session-0001.json"
    assert report.exists()
    payload = json.loads(report.read_text())
    assert payload["gate"]["gate_id"] == "feature_immutable_fields"


def test_harness_enforces_progress_update_required_gate(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    (tmp_path / "feature_list.json").write_text(json.dumps(features, indent=2))
    script = _write_agent_script(tmp_path, mode="coding-pass-one")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            progress_update_required=True,
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is False
    assert "progress_update_required" in result.message


def test_harness_attempts_repair_after_verification_failure_when_enabled(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    (tmp_path / "feature_list.json").write_text(json.dumps(features, indent=2))
    script = _write_agent_script(tmp_path, mode="verification-repair")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            repair_on_verification_failure=True,
            verification_commands=["test -f repaired.ok"],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is True
    assert (tmp_path / "repaired.ok").exists()
