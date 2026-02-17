import json
from pathlib import Path
from types import SimpleNamespace

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
from longrun_agent.runtime.contracts import AgentRunResult


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


def _write_metrics_heavy_evidence(artifacts_dir: Path, claim_count: int = 12) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "sources": [
            {
                "source_id": "lab_source",
                "name": "Lab Source",
                "url": "https://example.com/lab",
                "source_type": "community",
                "rationale": "Synthetic evidence for rotation test",
                "tags": ["metrics"],
                "retrieved_at": "2026-02-17T00:00:00+00:00",
            }
        ],
        "claims": [
            {
                "claim_id": f"lab_source-c{index}",
                "source_id": "lab_source",
                "statement": f"Synthetic metrics claim {index}",
                "tags": ["metrics"],
                "created_at": "2026-02-17T00:00:00+00:00",
            }
            for index in range(1, claim_count + 1)
        ],
    }
    (artifacts_dir / "improvement-evidence.json").write_text(json.dumps(payload, indent=2) + "\n")


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
    assert args.auto_bootstrap is True
    assert args.auto_research is True


def test_parser_accepts_run_cycle_alias() -> None:
    parser = build_parser()
    args = parser.parse_args(["run-cycle", "--window", "5"])
    assert args.command == "run-cycle"
    assert args.window == 5


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
    assert args.max_sources == 6
    assert args.max_claims == 12


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
        auto_bootstrap=False,
        bootstrap_sessions=None,
        auto_research=False,
        topic=None,
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
        auto_bootstrap=False,
        bootstrap_sessions=None,
        auto_research=False,
        topic=None,
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
        auto_bootstrap=False,
        bootstrap_sessions=None,
        auto_research=False,
        topic=None,
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
        topic=None,
        max_sources=6,
        max_claims=12,
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


def test_run_improvement_research_topic_auto_collects_bundle(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)

    class FakeBackend:
        def run(self, request):
            request.session_dir.mkdir(parents=True, exist_ok=True)
            stdout_path = request.session_dir / "agent.stdout.log"
            stderr_path = request.session_dir / "agent.stderr.log"
            stdout_path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "title": "Research Source",
                                "url": "https://example.com/research-source",
                                "source_type": "community",
                                "rationale": "useful",
                                "tags": ["metrics"],
                            }
                        ],
                        "claims": [
                            {
                                "source_url": "https://example.com/research-source",
                                "statement": "Use metrics windows for comparison.",
                                "tags": ["metrics"],
                            }
                        ],
                    }
                )
            )
            stderr_path.write_text("")
            return AgentRunResult(
                backend="fake",
                return_code=0,
                timeout=False,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )

    monkeypatch.setattr("longrun_agent.cli.create_backend", lambda **_kwargs: FakeBackend())

    code = run_improvement_research(
        config_path=config_path,
        list_only=False,
        topic="improve reliability",
        max_sources=4,
        max_claims=6,
        source_id=None,
        title=None,
        url=None,
        source_type="community",
        claims=None,
        tags="",
        notes="",
    )

    assert code == 0
    evidence_path = tmp_path / ".longrun" / "artifacts" / "improvement-evidence.json"
    payload = json.loads(evidence_path.read_text())
    assert any(item["url"] == "https://example.com/research-source" for item in payload["sources"])
    assert any(
        item["statement"] == "Use metrics windows for comparison."
        for item in payload["claims"]
    )


def test_run_improvement_cycle_records_memory_and_avoids_same_claim_set(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)

    state_dir = tmp_path / ".longrun"
    artifacts_dir = state_dir / "artifacts"
    _write_metrics_heavy_evidence(artifacts_dir, claim_count=12)
    _write_session(state_dir, session_id=1, phase="coding", success=True, passing=1, total=5, progress_made=True)

    first = run_improvement_cycle(
        config_path=config_path,
        window=20,
        max_failure_rate=0.5,
        max_no_progress_rate=1.0,
        min_sessions=1,
        auto_bootstrap=False,
        bootstrap_sessions=None,
        auto_research=False,
        topic=None,
        enforce_budget=False,
        as_json=False,
    )
    assert first == 0
    first_payload = json.loads((artifacts_dir / "improvement-cycle.json").read_text())
    first_claim_ids = [item["claim_id"] for item in first_payload["selected_research_claims"]]
    assert len(first_claim_ids) == 8

    second = run_improvement_cycle(
        config_path=config_path,
        window=20,
        max_failure_rate=0.5,
        max_no_progress_rate=1.0,
        min_sessions=1,
        auto_bootstrap=False,
        bootstrap_sessions=None,
        auto_research=False,
        topic=None,
        enforce_budget=False,
        as_json=False,
    )
    assert second == 0
    second_payload = json.loads((artifacts_dir / "improvement-cycle.json").read_text())
    second_claim_ids = [item["claim_id"] for item in second_payload["selected_research_claims"]]
    assert second_claim_ids != first_claim_ids
    assert set(second_claim_ids) - set(first_claim_ids)

    memory_payload = json.loads((artifacts_dir / "improvement-memory.json").read_text())
    assert len(memory_payload["cycles"]) >= 2
    assert memory_payload["claim_usage"]["lab_source-c1"]["count"] >= 1


def test_run_improvement_cycle_auto_bootstraps_sessions_when_window_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)

    def _fake_run_loop(self, max_sessions=None, continue_on_failure=False):
        del max_sessions, continue_on_failure
        _write_session(
            self.state_dir,
            session_id=1,
            phase="coding",
            success=True,
            passing=1,
            total=5,
            progress_made=True,
        )
        return [SimpleNamespace(success=True)]

    monkeypatch.setattr("longrun_agent.cli.Harness.run_loop", _fake_run_loop)

    code = run_improvement_cycle(
        config_path=config_path,
        window=20,
        max_failure_rate=0.5,
        max_no_progress_rate=1.0,
        min_sessions=1,
        auto_bootstrap=True,
        bootstrap_sessions=1,
        auto_research=False,
        topic=None,
        enforce_budget=False,
        as_json=False,
    )

    assert code == 0
    payload = json.loads((tmp_path / ".longrun" / "artifacts" / "improvement-cycle.json").read_text())
    assert payload["diagnosis"]["session_count"] == 1
    assert payload["orchestration"]["auto_bootstrap"]["attempted"] is True
    assert payload["orchestration"]["auto_bootstrap"]["sampled_sessions"] == 1


def test_run_improvement_cycle_auto_bootstraps_when_only_initializer_samples_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)
    state_dir = tmp_path / ".longrun"
    _write_session(
        state_dir,
        session_id=1,
        phase="initializer",
        success=False,
        passing=0,
        total=0,
        progress_made=None,
    )

    def _fake_run_loop(self, max_sessions=None, continue_on_failure=False):
        del max_sessions, continue_on_failure
        _write_session(
            self.state_dir,
            session_id=2,
            phase="coding",
            success=True,
            passing=1,
            total=5,
            progress_made=True,
        )
        return [SimpleNamespace(success=True)]

    monkeypatch.setattr("longrun_agent.cli.Harness.run_loop", _fake_run_loop)

    code = run_improvement_cycle(
        config_path=config_path,
        window=20,
        max_failure_rate=1.0,
        max_no_progress_rate=1.0,
        min_sessions=1,
        auto_bootstrap=True,
        bootstrap_sessions=1,
        auto_research=False,
        topic=None,
        enforce_budget=False,
        as_json=False,
    )

    assert code == 0
    payload = json.loads((tmp_path / ".longrun" / "artifacts" / "improvement-cycle.json").read_text())
    assert payload["orchestration"]["auto_bootstrap"]["attempted"] is True
    assert payload["orchestration"]["auto_bootstrap"]["reason"] == "no_coding_signal"


def test_run_improvement_cycle_auto_continues_when_reliability_budget_holds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)
    state_dir = tmp_path / ".longrun"
    _write_session(
        state_dir,
        session_id=1,
        phase="coding",
        success=False,
        passing=0,
        total=5,
        progress_made=False,
    )
    _write_session(
        state_dir,
        session_id=2,
        phase="coding",
        success=False,
        passing=0,
        total=5,
        progress_made=False,
    )

    def _fake_run_loop(self, max_sessions=None, continue_on_failure=False):
        assert max_sessions == 1
        assert continue_on_failure is True
        _write_session(
            self.state_dir,
            session_id=3,
            phase="coding",
            success=True,
            passing=1,
            total=5,
            progress_made=True,
        )
        return [SimpleNamespace(success=True)]

    monkeypatch.setattr("longrun_agent.cli.Harness.run_loop", _fake_run_loop)

    code = run_improvement_cycle(
        config_path=config_path,
        window=20,
        max_failure_rate=0.7,
        max_no_progress_rate=1.0,
        min_sessions=1,
        auto_bootstrap=True,
        bootstrap_sessions=1,
        auto_research=False,
        topic=None,
        enforce_budget=False,
        as_json=False,
    )

    assert code == 0
    payload = json.loads((tmp_path / ".longrun" / "artifacts" / "improvement-cycle.json").read_text())
    assert payload["orchestration"]["auto_continue"]["attempted"] is True
    assert payload["orchestration"]["auto_continue"]["sampled_sessions"] == 1
    assert payload["orchestration"]["auto_continue"]["post_budget_gate"] == "promote"
    assert payload["diagnosis"]["session_count"] == 3
    assert payload["budget_gate"]["status"] == "promote"


def test_run_improvement_cycle_auto_continue_scales_sampling_budget_for_severe_hold(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    write_default_config(config_path, project_dir=tmp_path)
    state_dir = tmp_path / ".longrun"
    _write_session(
        state_dir,
        session_id=1,
        phase="coding",
        success=False,
        passing=0,
        total=5,
        progress_made=False,
    )
    _write_session(
        state_dir,
        session_id=2,
        phase="coding",
        success=True,
        passing=1,
        total=5,
        progress_made=True,
    )
    _write_session(
        state_dir,
        session_id=3,
        phase="coding",
        success=True,
        passing=2,
        total=5,
        progress_made=True,
    )
    _write_session(
        state_dir,
        session_id=4,
        phase="coding",
        success=False,
        passing=2,
        total=5,
        progress_made=False,
    )
    _write_session(
        state_dir,
        session_id=5,
        phase="coding",
        success=False,
        passing=2,
        total=5,
        progress_made=False,
    )

    captured_max_sessions: list[int | None] = []

    def _fake_run_loop(self, max_sessions=None, continue_on_failure=False):
        captured_max_sessions.append(max_sessions)
        assert continue_on_failure is True
        return []

    monkeypatch.setattr("longrun_agent.cli.Harness.run_loop", _fake_run_loop)

    code = run_improvement_cycle(
        config_path=config_path,
        window=20,
        max_failure_rate=0.1,
        max_no_progress_rate=1.0,
        min_sessions=1,
        auto_bootstrap=True,
        bootstrap_sessions=None,
        auto_research=False,
        topic=None,
        enforce_budget=False,
        as_json=False,
    )

    assert code == 0
    assert captured_max_sessions == [20]
    payload = json.loads((tmp_path / ".longrun" / "artifacts" / "improvement-cycle.json").read_text())
    assert payload["orchestration"]["auto_continue"]["attempted"] is True
    assert payload["orchestration"]["auto_continue"]["requested_sessions"] == 20
