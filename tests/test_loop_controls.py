import json
import stat
import sys
from pathlib import Path

from longrun_agent.harness import Harness, HarnessConfig


def _write_agent_script(path: Path, mode: str) -> Path:
    script = path / "fake_agent_loop.py"
    source = """import json
import sys
from pathlib import Path

project_dir = Path(sys.argv[1])
phase = sys.argv[2]

if phase == "initializer":
    features = [
        {"category": "functional", "description": "Feature A", "steps": ["step 1"], "passes": False},
        {"category": "functional", "description": "Feature B", "steps": ["step 1"], "passes": False},
    ]
    (project_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    (project_dir / "init.sh").write_text("#!/usr/bin/env bash\\necho init\\n")
    raise SystemExit(0)

features_path = project_dir / "feature_list.json"
features = json.loads(features_path.read_text())

if "__MODE__" == "progress":
    for feature in features:
        if not feature["passes"]:
            feature["passes"] = True
            break
elif "__MODE__" == "stuck":
    pass

features_path.write_text(json.dumps(features, indent=2))
"""
    script.write_text(source.replace("__MODE__", mode))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_run_session_blocks_when_active_lock_exists(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build a basic task app")
    lock_dir = tmp_path / ".longrun"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "lock.json").write_text(
        json.dumps({"pid": 1, "started_at": "2026-01-01T00:00:00Z"})
    )

    script = _write_agent_script(tmp_path, mode="progress")
    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            feature_target=2,
            bearings_commands=[],
            verification_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is False
    assert "another harness instance" in result.message.lower()


def test_run_session_replaces_stale_lock(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build a basic task app")
    lock_dir = tmp_path / ".longrun"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "lock.json").write_text(
        json.dumps({"pid": 999999, "started_at": "2026-01-01T00:00:00Z"})
    )

    script = _write_agent_script(tmp_path, mode="progress")
    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            feature_target=2,
            bearings_commands=[],
            verification_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    result = harness.run_session()

    assert result.success is True
    assert result.phase == "initializer"


def test_run_loop_stops_after_consecutive_no_progress_sessions(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build a basic task app")
    script = _write_agent_script(tmp_path, mode="stuck")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            feature_target=2,
            max_no_progress_sessions=2,
            bearings_commands=[],
            verification_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    results = harness.run_loop(max_sessions=8)

    assert len(results) == 3
    assert results[0].phase == "initializer"
    assert results[1].phase == "coding"
    assert results[2].phase == "coding"
    assert harness.last_loop_stop_reason is not None
    assert "no progress" in harness.last_loop_stop_reason.lower()


def test_status_summary_returns_progress_and_session_info(tmp_path: Path) -> None:
    (tmp_path / "app_spec.txt").write_text("Build a basic task app")
    script = _write_agent_script(tmp_path, mode="progress")

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=[sys.executable, str(script), "{project_dir}", "{phase}"],
            feature_target=2,
            bearings_commands=[],
            verification_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    harness.run_session()
    harness.run_session()

    summary = harness.status_summary()

    assert summary["project_dir"] == str(tmp_path.resolve())
    assert summary["progress"]["passing"] == 1
    assert summary["progress"]["total"] == 2
    assert summary["session_count"] == 2
    assert summary["last_session"]["phase"] == "coding"


def test_status_summary_is_read_only(tmp_path: Path) -> None:
    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=["echo", "noop"],
            bearings_commands=[],
            verification_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    harness.status_summary()

    assert not (tmp_path / "app_spec.txt").exists()
    assert not (tmp_path / "claude-progress.txt").exists()
