import json
import shlex
import stat
import sys
from pathlib import Path

from longrun_agent.harness import Harness, HarnessConfig


def _write_agent_script(path: Path, mode: str) -> Path:
    script = path / f"fake_agent_remediation_{mode}.py"
    if mode == "initializer-missing-artifacts":
        source = """import json, os, sys
from pathlib import Path
project_dir = Path(sys.argv[1])
phase = sys.argv[2]
artifact_dir = Path(os.environ.get("LONGRUN_ARTIFACTS_DIR", project_dir / ".longrun" / "artifacts"))
artifact_dir.mkdir(parents=True, exist_ok=True)
if phase == "initializer":
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
        {"category": "functional", "description": "B", "steps": ["s1"], "passes": False},
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    raise SystemExit(0)
raise SystemExit(0)
"""
    else:
        source = """import json, os, sys
import time
from pathlib import Path
project_dir = Path(sys.argv[1])
phase = sys.argv[2]
artifact_dir = Path(os.environ.get("LONGRUN_ARTIFACTS_DIR", project_dir / ".longrun" / "artifacts"))
artifact_dir.mkdir(parents=True, exist_ok=True)
features_path = artifact_dir / "feature_list.json"
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
elif "__MODE__" == "verification-repair-timeout":
    if phase == "coding":
        for item in features:
            if not item["passes"]:
                item["passes"] = True
                break
    elif phase == "repair":
        time.sleep(3)
elif "__MODE__" == "verification-repair-nonzero":
    if phase == "coding":
        for item in features:
            if not item["passes"]:
                item["passes"] = True
                break
    elif phase == "repair":
        print("repair failed hard", file=sys.stderr)
        raise SystemExit(7)
features_path.write_text(json.dumps(features, indent=2))
"""
        source = source.replace("__MODE__", mode)
    script.write_text(source)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_harness_writes_remediation_report_for_initializer_artifact_gate(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build app")
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
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
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
    restored = json.loads((artifact_dir / "feature_list.json").read_text())
    assert restored[0]["description"] == "A"
    report = tmp_path / ".longrun" / "remediation" / "session-0001.json"
    assert report.exists()
    payload = json.loads(report.read_text())
    assert payload["gate"]["gate_id"] == "feature_immutable_fields"


def test_harness_rolls_back_feature_list_from_before_session_snapshot(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    feature_file = artifact_dir / "feature_list.json"
    feature_file.write_text(json.dumps(features, indent=2))
    script = _write_agent_script(tmp_path, mode="coding-mutate")

    pre_coding_script = tmp_path / "pre_coding_mutate_passes.py"
    pre_coding_script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"feature_file = Path({str(feature_file)!r})\n"
        "features = json.loads(feature_file.read_text())\n"
        "features[0]['passes'] = True\n"
        "feature_file.write_text(json.dumps(features, indent=2))\n"
    )

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            pre_coding_commands=[
                f"{shlex.quote(sys.executable)} {shlex.quote(str(pre_coding_script))}"
            ],
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is False
    assert "feature_immutable_fields" in result.message
    restored = json.loads(feature_file.read_text())
    assert restored[0]["description"] == "A"
    assert restored[0]["passes"] is True


def test_harness_enforces_progress_update_required_gate(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
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
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
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


def test_harness_reruns_verification_commands_after_successful_repair(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    script = _write_agent_script(tmp_path, mode="verification-repair")
    attempts_log = tmp_path / "verification-attempts.log"
    verification_command = (
        f"echo attempt >> {shlex.quote(str(attempts_log))} && test -f repaired.ok"
    )

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            repair_on_verification_failure=True,
            verification_commands=[verification_command],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is True
    assert (tmp_path / "repaired.ok").exists()
    assert attempts_log.exists()
    attempts = [line for line in attempts_log.read_text().splitlines() if line.strip()]
    assert len(attempts) == 2


def test_harness_reports_verification_failure_with_log_path_evidence(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    script = _write_agent_script(tmp_path, mode="coding-pass-one")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            repair_on_verification_failure=False,
            verification_commands=["test -f verification.ok", "echo should-not-run"],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is False
    assert "verification_commands_pass" in result.message
    assert "verification command failed: test -f verification.ok" in result.message
    assert "verification.log" in result.message

    verification_log = tmp_path / ".longrun" / "sessions" / "session-0001" / "verification.log"
    assert verification_log.exists()
    verification_text = verification_log.read_text()
    assert "$ test -f verification.ok" in verification_text
    assert "[exit 1]" in verification_text
    assert "should-not-run" not in verification_text

    report = tmp_path / ".longrun" / "remediation" / "session-0001.json"
    assert report.exists()
    payload = json.loads(report.read_text())
    assert payload["gate"]["gate_id"] == "verification_commands_pass"
    assert "verification.log" in payload["gate"]["message"]


def test_harness_reports_pre_coding_failure_as_gate_and_blocks_agent(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    script = _write_agent_script(tmp_path, mode="coding-pass-one")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            pre_coding_commands=["echo pre-check", "exit 9", "echo should-not-run"],
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is False
    assert "pre_coding_commands_pass" in result.message
    assert "pre-coding command failed: exit 9" in result.message
    assert "pre-coding.log" in result.message

    pre_coding_log = tmp_path / ".longrun" / "sessions" / "session-0001" / "pre-coding.log"
    assert pre_coding_log.exists()
    pre_coding_text = pre_coding_log.read_text()
    assert "$ echo pre-check" in pre_coding_text
    assert "$ exit 9" in pre_coding_text
    assert "[exit 9]" in pre_coding_text
    assert "should-not-run" not in pre_coding_text

    report = tmp_path / ".longrun" / "remediation" / "session-0001.json"
    assert report.exists()
    payload = json.loads(report.read_text())
    assert payload["gate"]["gate_id"] == "pre_coding_commands_pass"
    assert "pre-coding.log" in payload["gate"]["message"]

    after_features = json.loads((artifact_dir / "feature_list.json").read_text())
    assert after_features[0]["passes"] is False


def test_harness_reports_repair_timeout_failure_detail(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    script = _write_agent_script(tmp_path, mode="verification-repair-timeout")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            repair_on_verification_failure=True,
            verification_commands=["test -f repaired.ok"],
            agent_timeout_seconds=1,
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is False
    assert "verification_commands_pass" in result.message
    assert "repair session timed out after 1s" in result.message

    report = tmp_path / ".longrun" / "remediation" / "session-0001.json"
    assert report.exists()
    payload = json.loads(report.read_text())
    assert payload["gate"]["gate_id"] == "verification_commands_pass"
    assert "repair session timed out after 1s" in payload["gate"]["message"]
    assert ".longrun/sessions/session-0001/agent.stdout.log" in payload["gate"]["message"]
    assert ".longrun/sessions/session-0001/agent.stderr.log" in payload["gate"]["message"]


def test_harness_reports_repair_nonzero_failure_detail(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build app")
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    script = _write_agent_script(tmp_path, mode="verification-repair-nonzero")

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

    assert result.success is False
    assert "verification_commands_pass" in result.message
    assert "repair session exited with non-zero status (code=7)" in result.message

    report = tmp_path / ".longrun" / "remediation" / "session-0001.json"
    assert report.exists()
    payload = json.loads(report.read_text())
    assert payload["gate"]["gate_id"] == "verification_commands_pass"
    assert "repair session exited with non-zero status (code=7)" in payload["gate"]["message"]
    assert ".longrun/sessions/session-0001/agent.stdout.log" in payload["gate"]["message"]
    assert ".longrun/sessions/session-0001/agent.stderr.log" in payload["gate"]["message"]
    assert "repair failed hard" in payload["gate"]["message"]
