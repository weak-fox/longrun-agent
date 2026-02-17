import json
from pathlib import Path

from longrun_agent.cli import build_parser, run_self_improve
from longrun_agent.config import load_config, write_default_config
from longrun_agent.self_improve import analyze_recent_sessions


def _write_session(
    state_dir: Path,
    *,
    session_id: int,
    phase: str,
    success: bool,
    passing: int,
    total: int,
    progress_made: bool | None = None,
) -> None:
    session_dir = state_dir / "sessions" / f"session-{session_id:04d}"
    session_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "phase": phase,
        "success": success,
        "message": "test",
        "passing": passing,
        "total": total,
        "progress_made": progress_made,
    }
    (session_dir / "session.json").write_text(json.dumps(payload, indent=2) + "\n")


def _write_remediation(
    state_dir: Path,
    *,
    session_id: int,
    gate_id: str,
    message: str = "failed",
) -> None:
    remediation_dir = state_dir / "remediation"
    remediation_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "gate": {
            "gate_id": gate_id,
            "message": message,
        },
    }
    (remediation_dir / f"session-{session_id:04d}.json").write_text(json.dumps(payload, indent=2) + "\n")


def test_parser_accepts_self_improve_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(["self-improve", "--window", "15", "--no-apply"])

    assert args.command == "self-improve"
    assert args.window == 15
    assert args.apply is False


def test_analyze_recent_sessions_tracks_failures_and_no_progress(tmp_path: Path) -> None:
    state_dir = tmp_path / ".longrun"
    _write_session(state_dir, session_id=1, phase="coding", success=True, passing=1, total=10, progress_made=False)
    _write_session(state_dir, session_id=2, phase="coding", success=False, passing=1, total=10, progress_made=False)
    _write_remediation(state_dir, session_id=2, gate_id="verification_commands_pass")

    report = analyze_recent_sessions(state_dir=state_dir, window=10)

    assert report.session_count == 2
    assert report.failure_count == 1
    assert report.no_progress_sessions == 2
    assert report.gate_failures["verification_commands_pass"] == 1


def test_run_self_improve_applies_safe_tuning_when_pattern_matches(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)

    state_dir = tmp_path / ".longrun"
    _write_session(state_dir, session_id=1, phase="coding", success=False, passing=1, total=5, progress_made=False)
    _write_remediation(state_dir, session_id=1, gate_id="verification_commands_pass")

    code = run_self_improve(config_path=config_path, window=20, apply=True)

    assert code == 0
    loaded = load_config(config_path)
    assert loaded.repair_on_verification_failure is True

    plan_path = state_dir / "artifacts" / "self-improvement-plan.md"
    assert plan_path.exists()
    plan_text = plan_path.read_text()
    assert "verification_commands_pass" in plan_text
    assert "repair_on_verification_failure" in plan_text


def test_run_self_improve_does_not_apply_when_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)

    state_dir = tmp_path / ".longrun"
    _write_session(state_dir, session_id=1, phase="coding", success=False, passing=1, total=5, progress_made=False)
    _write_remediation(state_dir, session_id=1, gate_id="verification_commands_pass")

    code = run_self_improve(config_path=config_path, window=20, apply=False)

    assert code == 0
    loaded = load_config(config_path)
    assert loaded.repair_on_verification_failure is False
