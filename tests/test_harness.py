import json
import stat
import sys
from pathlib import Path

from longrun_agent.harness import Harness, HarnessConfig


def _write_agent_script(path: Path, mode: str) -> Path:
    script = path / "fake_agent.py"
    source = """import json
import sys
from pathlib import Path

project_dir = Path(sys.argv[1])
phase = sys.argv[2]

if phase == \"initializer\":
    if \"__MODE__\" == \"fail_initializer\":
        print(\"codex failed to start\", file=sys.stderr)
        raise SystemExit(17)
    features = [
        {\"category\": \"functional\", \"description\": \"Feature A\", \"steps\": [\"step 1\"], \"passes\": False},
        {\"category\": \"functional\", \"description\": \"Feature B\", \"steps\": [\"step 1\"], \"passes\": False},
    ]
    (project_dir / \"feature_list.json\").write_text(json.dumps(features, indent=2))
    (project_dir / \"init.sh\").write_text(\"#!/usr/bin/env bash\\necho init\\n\")
    (project_dir / \"claude-progress.txt\").write_text(\"initialized\\n\")
    raise SystemExit(0)

features_path = project_dir / \"feature_list.json\"
features = json.loads(features_path.read_text())

if \"__MODE__\" == \"good\":
    for feature in features:
        if not feature[\"passes\"]:
            feature[\"passes\"] = True
            break
elif \"__MODE__\" == \"mutate\":
    features[0][\"description\"] = \"tampered\"

features_path.write_text(json.dumps(features, indent=2))
"""
    script.write_text(source.replace("__MODE__", mode))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_run_session_initializer_then_coding(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build a basic task app")
    script = _write_agent_script(tmp_path, mode="good")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            verification_commands=[],
            bearings_commands=[],
            feature_target=2,
            auto_continue_delay_seconds=0,
        )
    )

    first = harness.run_session()
    second = harness.run_session()

    assert first.success is True
    assert first.phase == "initializer"
    assert second.success is True
    assert second.phase == "coding"

    features = json.loads((tmp_path / "feature_list.json").read_text())
    assert features[0]["passes"] is True


def test_run_session_non_zero_exit_reports_code_and_log_paths(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build a basic task app")
    script = _write_agent_script(tmp_path, mode="fail_initializer")

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
    assert result.return_code == 17
    assert "code=17" in result.message
    assert "agent.stdout.log" in result.message
    assert "agent.stderr.log" in result.message


def test_run_session_reverts_illegal_feature_list_mutation(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build a basic task app")

    features = [
        {
            "category": "functional",
            "description": "Feature A",
            "steps": ["step 1"],
            "passes": False,
        }
    ]
    (tmp_path / "feature_list.json").write_text(json.dumps(features, indent=2))

    script = _write_agent_script(tmp_path, mode="mutate")

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
    restored = json.loads((tmp_path / "feature_list.json").read_text())
    assert restored[0]["description"] == "Feature A"
    assert "forbidden feature_list mutation" in result.message.lower()


def test_run_session_short_circuits_when_all_features_pass(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build a basic task app")
    features = [
        {
            "category": "functional",
            "description": "Feature A",
            "steps": ["step 1"],
            "passes": True,
        }
    ]
    (tmp_path / "feature_list.json").write_text(json.dumps(features, indent=2))

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=["bash", "-lc", "exit 99"],
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is True
    assert result.message == "All features already passing"


def test_harness_uses_custom_state_dir_when_configured(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    (project_dir / "app_spec.txt").write_text("Build a basic task app")
    script = _write_agent_script(project_dir, mode="good")

    harness = Harness(
        HarnessConfig(
            project_dir=project_dir,
            state_dir=state_dir,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            verification_commands=[],
            bearings_commands=[],
            feature_target=2,
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is True
    assert (state_dir / "sessions" / "session-0001").exists()
    assert not (project_dir / ".longrun").exists()


def test_harness_can_store_generated_artifacts_under_subdirectory(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    artifact_dir = project_dir / ".longrun-artifacts"
    script = project_dir / "fake_agent_artifacts.py"
    script.write_text(
        """import json
import sys
from pathlib import Path

project_dir = Path(sys.argv[1])
phase = sys.argv[2]
artifact_dir = Path(sys.argv[3])
artifact_dir.mkdir(parents=True, exist_ok=True)

if phase == "initializer":
    features = [
        {"category": "functional", "description": "Feature A", "steps": ["step 1"], "passes": False},
        {"category": "functional", "description": "Feature B", "steps": ["step 1"], "passes": False},
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    (artifact_dir / "init.sh").write_text("#!/usr/bin/env bash\\necho init\\n")
    (artifact_dir / "claude-progress.txt").write_text("initialized\\n")
    raise SystemExit(0)

features_path = artifact_dir / "feature_list.json"
features = json.loads(features_path.read_text())
for feature in features:
    if not feature["passes"]:
        feature["passes"] = True
        break
features_path.write_text(json.dumps(features, indent=2))
"""
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    harness = Harness(
        HarnessConfig(
            project_dir=project_dir,
            artifacts_dir=Path(".longrun-artifacts"),
            agent_command=[
                sys.executable,
                str(script),
                "{project_dir}",
                "{phase}",
                str(artifact_dir),
            ],
            verification_commands=[],
            bearings_commands=[],
            feature_target=2,
            auto_continue_delay_seconds=0,
        )
    )

    first = harness.run_session()
    second = harness.run_session()

    assert first.success is True
    assert second.success is True
    assert (artifact_dir / "feature_list.json").exists()
    assert (artifact_dir / "app_spec.txt").exists()
    assert not (project_dir / "feature_list.json").exists()
