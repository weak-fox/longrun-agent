import json
from pathlib import Path

from longrun_agent.cli import (
    build_parser,
    run_improvement_cycle,
    run_improvement_research,
)
from longrun_agent.config import write_default_config
from longrun_agent.improvement_cycle import (
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


def test_parser_accepts_improvement_research_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "improvement-research",
            "--source-id",
            "community_playbook",
            "--title",
            "Community playbook",
            "--url",
            "https://example.com/playbook",
            "--source-type",
            "community",
            "--claim",
            "Small batches reduce risk.",
            "--tags",
            "batch_size,risk",
        ]
    )

    assert args.command == "improvement-research"
    assert args.source_id == "community_playbook"
    assert args.source_type == "community"
    assert args.claim == ["Small batches reduce risk."]
    assert args.tags == "batch_size,risk"


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
    assert payload["selected_research_claims"]
    claim_ids = {item["claim_id"] for item in payload["selected_research_claims"]}
    known_source_ids = {source["source_id"] for source in payload["research_sources"]}
    for hypothesis in payload["hypotheses"]:
        assert hypothesis["evidence_claim_ids"]
        assert set(hypothesis["evidence_claim_ids"]).issubset(claim_ids)
        assert hypothesis["source_ids"]
        assert set(hypothesis["source_ids"]).issubset(known_source_ids)
    for plan in payload["experiment_plans"]:
        assert plan["evidence_claim_ids"]
        assert set(plan["evidence_claim_ids"]).issubset(claim_ids)
        assert plan["source_ids"]
        assert set(plan["source_ids"]).issubset(known_source_ids)

    md_text = md_path.read_text()
    assert "Research Evidence" in md_text
    assert "sources:" in md_text
    for source in payload["research_sources"]:
        assert source["source_id"] in md_text
        assert source["url"] in md_text


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


def test_run_improvement_cycle_holds_when_research_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)

    state_dir = tmp_path / ".longrun"
    artifacts_dir = state_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "improvement-evidence.json").write_text(
        json.dumps({"sources": [], "claims": []}, indent=2) + "\n"
    )
    _write_session(state_dir, session_id=1, phase="coding", success=True, passing=1, total=5, progress_made=True)

    code = run_improvement_cycle(
        config_path=config_path,
        window=20,
        max_failure_rate=0.5,
        max_no_progress_rate=1.0,
        min_sessions=1,
        enforce_budget=True,
        as_json=False,
    )

    assert code == 1
    payload = json.loads((artifacts_dir / "improvement-cycle.json").read_text())
    assert payload["budget_gate"]["status"] == "hold"
    assert any(
        "insufficient_research_evidence" in reason
        for reason in payload["budget_gate"]["reasons"]
    )


def test_run_improvement_research_adds_local_evidence_entry(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)

    code = run_improvement_research(
        config_path=config_path,
        list_only=False,
        source_id="community_playbook",
        title="Community playbook",
        url="https://example.com/playbook",
        source_type="community",
        claims=["Small batches reduce risk."],
        tags="batch_size,risk",
        notes="team-curated",
    )

    assert code == 0
    evidence_path = tmp_path / ".longrun" / "artifacts" / "improvement-evidence.json"
    payload = json.loads(evidence_path.read_text())
    assert any(item["source_id"] == "community_playbook" for item in payload["sources"])
    assert any(
        item["source_id"] == "community_playbook"
        and "Small batches reduce risk." in item["statement"]
        for item in payload["claims"]
    )
