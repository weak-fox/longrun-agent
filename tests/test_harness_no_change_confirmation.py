import json
import stat
import sys
from pathlib import Path

from longrun_agent.harness import Harness, HarnessConfig


def _write_agent_script(path: Path, mode: str) -> Path:
    script = path / f"fake_agent_no_change_{mode}.py"
    source = """import json
import sys
from pathlib import Path

project_dir = Path(sys.argv[1])
phase = sys.argv[2]

if phase == "initializer":
    features = [
        {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
        {"category": "functional", "description": "B", "steps": ["s1"], "passes": False},
    ]
    (project_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    (project_dir / "init.sh").write_text("#!/usr/bin/env bash\\necho init\\n")
    (project_dir / "claude-progress.txt").write_text("initialized\\n")
    raise SystemExit(0)

if phase == "coding":
    # Intentionally no file changes.
    raise SystemExit(0)

if phase == "repair":
    if "__MODE__" == "continue":
        print('{"decision":"continue","reason":"needs more implementation"}')
    else:
        print('{"decision":"mark_complete","reason":"already satisfied by existing behavior"}')
    raise SystemExit(0)

raise SystemExit(0)
"""
    script.write_text(source.replace("__MODE__", mode))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_coding_no_change_can_confirm_continue_and_bypass_hard_gates(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build app")
    script = _write_agent_script(tmp_path, mode="continue")

    initializer_harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
            feature_target=2,
        )
    )

    first = initializer_harness.run_session()

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            commit_required=True,
            progress_update_required=True,
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
            feature_target=2,
        )
    )

    second = harness.run_session()

    assert first.success is True
    assert second.success is True
    assert "no-change confirmation: continue" in second.message
    assert second.progress_made is False

    features = json.loads((tmp_path / "feature_list.json").read_text())
    assert features[0]["passes"] is False
    assert features[1]["passes"] is False


def test_coding_no_change_can_confirm_and_mark_current_feature_complete(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build app")
    script = _write_agent_script(tmp_path, mode="mark-complete")

    initializer_harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
            feature_target=2,
        )
    )

    first = initializer_harness.run_session()

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            commit_required=True,
            progress_update_required=True,
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
            feature_target=2,
        )
    )

    second = harness.run_session()

    assert first.success is True
    assert second.success is True
    assert "no-change confirmation: mark_complete" in second.message
    assert second.progress_made is True
    assert second.passing == 1
    assert second.total == 2

    features = json.loads((tmp_path / "feature_list.json").read_text())
    assert features[0]["passes"] is True
    assert features[1]["passes"] is False
