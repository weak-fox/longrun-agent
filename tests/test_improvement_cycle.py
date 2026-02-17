import json
from pathlib import Path

from longrun_agent.cli import build_parser, run_improvement_cycle
from longrun_agent.config import write_default_config
from longrun_agent.improvement_cycle import (
    ARCHITECTURE_SOURCES,
    ImprovementTargets,
    evaluate_budget_gate,
)


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


def _write_remediation(state_dir: Path, *, session_id: int, gate_id: str) -> None:
    remediation_dir = state_dir / "remediation"
    remediation_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "gate": {
            "gate_id": gate_id,
            "message": "failed",
        },
    }
    (remediation_dir / f"session-{session_id:04d}.json").write_text(json.dumps(payload, indent=2) + "\n")


def test_parser_accepts_improvement_cycle_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "improvement-cycle",
            "--window",
            "30",
            "--max-failure-rate",
            "0.08",
            "--max-no-progress-rate",
            "0.2",
            "--min-sessions",
            "12",
            "--enforce-budget",
        ]
    )

    assert args.command == "improvement-cycle"
    assert args.window == 30
    assert args.max_failure_rate == 0.08
    assert args.max_no_progress_rate == 0.2
    assert args.min_sessions == 12
    assert args.enforce_budget is True


def test_evaluate_budget_gate_blocks_when_failure_rate_exceeds_target() -> None:
    decision = evaluate_budget_gate(
        session_count=20,
        failure_count=5,
        coding_sessions=20,
        no_progress_sessions=1,
        targets=ImprovementTargets(
            max_failure_rate=0.10,
            max_no_progress_rate=0.25,
            min_sessions=10,
        ),
    )

    assert decision.status == "hold"
    assert "failure_rate" in decision.reasons[0]


def test_run_improvement_cycle_writes_artifacts_with_source_links(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)

    state_dir = tmp_path / ".longrun"
    _write_session(state_dir, session_id=1, phase="coding", success=True, passing=1, total=5, progress_made=True)
    _write_session(state_dir, session_id=2, phase="coding", success=False, passing=1, total=5, progress_made=False)
    _write_remediation(state_dir, session_id=2, gate_id="verification_commands_pass")

    code = run_improvement_cycle(
        config_path=config_path,
        window=20,
        max_failure_rate=0.5,
        max_no_progress_rate=1.0,
        min_sessions=1,
        enforce_budget=False,
        as_json=False,
    )

    assert code == 0
    artifacts_dir = state_dir / "artifacts"
    json_path = artifacts_dir / "improvement-cycle.json"
    md_path = artifacts_dir / "improvement-cycle.md"
    assert json_path.exists()
    assert md_path.exists()

    payload = json.loads(json_path.read_text())
    assert payload["diagnosis"]["session_count"] == 2
    assert payload["budget_gate"]["status"] == "promote"

    md_text = md_path.read_text()
    assert "Architecture Sources" in md_text
    for source in ARCHITECTURE_SOURCES:
        assert source.url in md_text


def test_run_improvement_cycle_enforce_budget_returns_nonzero_on_hold(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)

    state_dir = tmp_path / ".longrun"
    _write_session(state_dir, session_id=1, phase="coding", success=False, passing=0, total=5, progress_made=False)
    _write_session(state_dir, session_id=2, phase="coding", success=False, passing=0, total=5, progress_made=False)

    code = run_improvement_cycle(
        config_path=config_path,
        window=20,
        max_failure_rate=0.2,
        max_no_progress_rate=0.5,
        min_sessions=1,
        enforce_budget=True,
        as_json=True,
    )

    assert code == 1
