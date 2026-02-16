import json
import stat
import sys
from pathlib import Path

from longrun_agent.harness import Harness, HarnessConfig


def _write_agent_script(path: Path, mode: str) -> Path:
    script = path / "fake_agent_article.py"
    source = """import json
import os
import sys
from pathlib import Path

project_dir = Path(sys.argv[1])
phase = sys.argv[2]
artifact_dir = Path(os.environ.get("LONGRUN_ARTIFACTS_DIR", project_dir / ".longrun" / "artifacts"))
artifact_dir.mkdir(parents=True, exist_ok=True)

if phase == "initializer":
    features = [
        {"category": "functional", "description": "Feature A", "steps": ["step 1"], "passes": False},
        {"category": "functional", "description": "Feature B", "steps": ["step 1"], "passes": False},
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    (artifact_dir / "init.sh").write_text("#!/usr/bin/env bash\\necho init\\n")
    raise SystemExit(0)

if "__MODE__" == "touch-marker":
    (project_dir / "agent_was_invoked.txt").write_text("yes")

features_path = artifact_dir / "feature_list.json"
features = json.loads(features_path.read_text())

if "__MODE__" == "mark-two":
    changed = 0
    for feature in features:
        if not feature["passes"]:
            feature["passes"] = True
            changed += 1
        if changed == 2:
            break
else:
    for feature in features:
        if not feature["passes"]:
            feature["passes"] = True
            break

features_path.write_text(json.dumps(features, indent=2))
"""
    script.write_text(source.replace("__MODE__", mode))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_pre_coding_checks_fail_fast_before_agent_invocation(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build a basic task app")

    features = [
        {
            "category": "functional",
            "description": "Feature A",
            "steps": ["step 1"],
            "passes": False,
        }
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))

    script = _write_agent_script(tmp_path, mode="touch-marker")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            pre_coding_commands=["exit 1"],
            bearings_commands=[],
            verification_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is False
    assert "pre-coding check failed" in result.message.lower()
    assert not (tmp_path / "agent_was_invoked.txt").exists()


def test_max_features_per_session_enforced_and_reverted(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".longrun" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "app_spec.txt").write_text("Build a basic task app")

    features = [
        {
            "category": "functional",
            "description": "Feature A",
            "steps": ["step 1"],
            "passes": False,
        },
        {
            "category": "functional",
            "description": "Feature B",
            "steps": ["step 1"],
            "passes": False,
        },
        {
            "category": "functional",
            "description": "Feature C",
            "steps": ["step 1"],
            "passes": False,
        },
    ]
    (artifact_dir / "feature_list.json").write_text(json.dumps(features, indent=2))

    script = _write_agent_script(tmp_path, mode="mark-two")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            max_features_per_session=1,
            pre_coding_commands=[],
            bearings_commands=[],
            verification_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is False
    assert "max features per session" in result.message.lower()

    restored = json.loads((artifact_dir / "feature_list.json").read_text())
    assert sum(1 for item in restored if item["passes"]) == 0
