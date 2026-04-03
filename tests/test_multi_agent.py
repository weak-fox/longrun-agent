"""Tests for multi_agent module — Pipeline-based multi-agent collaboration.

Covers:
- AgentConfig creation and serialization
- Phase construction, prompt rendering, and serialization
- Pipeline construction, ordering, validation, and dependency management
- PipelineExecutor execution flow, context passing, and failure handling
- Rollback policies (STOP, SKIP_DOWNSTREAM, RETRY_ONCE, CONTINUE)
- Pre-built pipeline templates
- Pipeline save/load
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from longrun_agent.multi_agent import (
    AgentConfig,
    Phase,
    PhaseResult,
    PhaseStatus,
    Pipeline,
    PipelineExecutor,
    PipelineResult,
    RollbackPolicy,
    create_coding_review_pipeline,
    create_repair_pipeline,
)


# ──────────── AgentConfig Tests ────────────


class TestAgentConfig:
    def test_defaults(self):
        config = AgentConfig()
        assert config.model == "claude-sonnet-4-20250514"
        assert config.max_tokens == 16384
        assert config.max_iterations == 100
        assert config.temperature is None
        assert config.system_prompt_override is None

    def test_serialization_round_trip(self):
        config = AgentConfig(
            model="custom-model",
            max_tokens=4096,
            temperature=0.5,
            extra_params={"top_k": 50},
        )
        data = config.to_dict()
        restored = AgentConfig.from_dict(data)
        assert restored.model == "custom-model"
        assert restored.max_tokens == 4096
        assert restored.temperature == 0.5
        assert restored.extra_params == {"top_k": 50}

    def test_to_dict_omits_none(self):
        config = AgentConfig()
        data = config.to_dict()
        assert "temperature" not in data
        assert "system_prompt_override" not in data
        assert "tools_override" not in data


# ──────────── Phase Tests ────────────


class TestPhase:
    def test_defaults(self):
        phase = Phase(name="test")
        assert phase.name == "test"
        assert phase.description == ""
        assert phase.enabled is True
        assert phase.rollback_policy == RollbackPolicy.STOP
        assert phase.depends_on == []

    def test_render_prompt_simple(self):
        phase = Phase(
            name="coding",
            prompt_template="Implement {feature} in {language}",
        )
        rendered = phase.render_prompt({
            "feature": "user auth",
            "language": "Python",
        })
        assert rendered == "Implement user auth in Python"

    def test_render_prompt_missing_key(self):
        phase = Phase(
            name="test",
            prompt_template="Build {feature} with {framework}",
        )
        rendered = phase.render_prompt({"feature": "auth"})
        # Missing keys are left as-is
        assert "{framework}" in rendered
        assert "auth" in rendered

    def test_render_prompt_extra_keys_ignored(self):
        phase = Phase(
            name="test",
            prompt_template="Hello {name}",
        )
        rendered = phase.render_prompt({"name": "World", "extra": "ignored"})
        assert rendered == "Hello World"

    def test_serialization_round_trip(self):
        phase = Phase(
            name="review",
            description="Code review phase",
            agent_config=AgentConfig(model="fast-model", max_tokens=2048),
            prompt_template="Review: {code}",
            depends_on=["coding"],
            rollback_policy=RollbackPolicy.CONTINUE,
            gates=[{"type": "syntax_check"}],
            timeout_seconds=1800,
            enabled=True,
            metadata={"priority": "high"},
        )
        data = phase.to_dict()
        restored = Phase.from_dict(data)

        assert restored.name == "review"
        assert restored.description == "Code review phase"
        assert restored.agent_config.model == "fast-model"
        assert restored.depends_on == ["coding"]
        assert restored.rollback_policy == RollbackPolicy.CONTINUE
        assert restored.gates == [{"type": "syntax_check"}]
        assert restored.metadata == {"priority": "high"}


# ──────────── Pipeline Tests ────────────


class TestPipeline:
    def test_empty_pipeline(self):
        pipeline = Pipeline(name="empty")
        assert pipeline.size == 0
        assert pipeline.phases == []

    def test_add_phase(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="phase1"))
        assert pipeline.size == 1
        assert pipeline.get_phase("phase1").name == "phase1"

    def test_add_duplicate_raises(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="p1"))
        with pytest.raises(ValueError, match="already exists"):
            pipeline.add_phase(Phase(name="p1"))

    def test_remove_phase(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="p1"))
        pipeline.add_phase(Phase(name="p2", depends_on=["p1"]))
        removed = pipeline.remove_phase("p1")
        assert removed.name == "p1"
        assert pipeline.size == 1
        assert "p1" not in pipeline.get_phase("p2").depends_on

    def test_remove_nonexistent_raises(self):
        pipeline = Pipeline(name="test")
        with pytest.raises(KeyError, match="not found"):
            pipeline.remove_phase("missing")

    def test_get_phase_nonexistent_raises(self):
        pipeline = Pipeline(name="test")
        with pytest.raises(KeyError, match="not found"):
            pipeline.get_phase("missing")

    def test_execution_order_no_deps(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="a"))
        pipeline.add_phase(Phase(name="b"))
        pipeline.add_phase(Phase(name="c"))
        order = pipeline.execution_order()
        assert order == ["a", "b", "c"]  # Insertion order

    def test_execution_order_with_deps(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="build"))
        pipeline.add_phase(Phase(name="test", depends_on=["build"]))
        pipeline.add_phase(Phase(name="deploy", depends_on=["test"]))
        order = pipeline.execution_order()
        assert order == ["build", "test", "deploy"]

    def test_execution_order_skips_disabled(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="a"))
        pipeline.add_phase(Phase(name="b", enabled=False))
        pipeline.add_phase(Phase(name="c"))
        order = pipeline.execution_order()
        assert "b" not in order

    def test_circular_dependency_raises(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="a", depends_on=["b"]))
        pipeline.add_phase(Phase(name="b", depends_on=["a"]))
        with pytest.raises(ValueError, match="Circular"):
            pipeline.execution_order()

    def test_validate_empty_pipeline(self):
        pipeline = Pipeline(name="empty")
        issues = pipeline.validate()
        assert any("no phases" in i for i in issues)

    def test_validate_missing_dependency(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="p1", depends_on=["missing"]))
        issues = pipeline.validate()
        assert any("unknown phase" in i for i in issues)

    def test_validate_valid_pipeline(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="p1"))
        pipeline.add_phase(Phase(name="p2", depends_on=["p1"]))
        issues = pipeline.validate()
        assert issues == []

    def test_serialization_round_trip(self):
        pipeline = Pipeline(name="test_pipe", description="A test pipeline")
        pipeline.add_phase(Phase(name="a"))
        pipeline.add_phase(Phase(name="b", depends_on=["a"]))

        data = pipeline.to_dict()
        restored = Pipeline.from_dict(data)
        assert restored.name == "test_pipe"
        assert restored.description == "A test pipeline"
        assert restored.size == 2
        assert restored.get_phase("b").depends_on == ["a"]

    def test_save_and_load(self):
        pipeline = Pipeline(name="persist_test")
        pipeline.add_phase(Phase(name="build"))
        pipeline.add_phase(Phase(name="test", depends_on=["build"]))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pipeline.json"
            pipeline.save(path)
            loaded = Pipeline.load(path)
            assert loaded.size == 2
            assert loaded.name == "persist_test"


# ──────────── PipelineExecutor Tests ────────────


class TestPipelineExecutor:
    def _simple_pipeline(self) -> Pipeline:
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(
            name="step1",
            prompt_template="Do step 1: {input}",
        ))
        pipeline.add_phase(Phase(
            name="step2",
            prompt_template="Do step 2 with: {step1_output}",
            depends_on=["step1"],
        ))
        return pipeline

    def test_basic_execution(self):
        pipeline = self._simple_pipeline()
        executor = PipelineExecutor(pipeline)
        result = executor.run(context={"input": "hello"})

        assert result.success is True
        assert result.completed_phases == 2
        assert result.failed_phases == 0
        assert result.total_duration > 0

    def test_context_passing(self):
        """Verify that phase outputs flow into downstream phase context."""
        pipeline = self._simple_pipeline()
        captured_contexts = []

        def capturing_executor(phase, context):
            captured_contexts.append((phase.name, dict(context)))
            rendered = phase.render_prompt(context)
            return PhaseResult(
                phase_name=phase.name,
                status=PhaseStatus.COMPLETED,
                output=f"output_of_{phase.name}",
                artifacts={"result": f"artifact_{phase.name}"},
            )

        executor = PipelineExecutor(pipeline, phase_executor=capturing_executor)
        result = executor.run(context={"input": "data"})

        assert result.success is True
        # Step 2 should have step1's output in context
        step2_ctx = captured_contexts[1][1]
        assert "step1_output" in step2_ctx
        assert step2_ctx["step1_output"] == "output_of_step1"
        assert step2_ctx["step1_artifacts"]["result"] == "artifact_step1"

    def test_failure_stops_pipeline(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(
            name="fails",
            rollback_policy=RollbackPolicy.STOP,
        ))
        pipeline.add_phase(Phase(
            name="never_runs",
            depends_on=["fails"],
        ))

        def failing_executor(phase, context):
            if phase.name == "fails":
                return PhaseResult(
                    phase_name=phase.name,
                    status=PhaseStatus.FAILED,
                    error_message="boom",
                )
            return PhaseResult(phase_name=phase.name, status=PhaseStatus.COMPLETED)

        executor = PipelineExecutor(pipeline, phase_executor=failing_executor)
        result = executor.run()

        assert result.success is False
        assert result.failed_phases == 1
        assert result.skipped_phases == 1

    def test_rollback_continue(self):
        """With CONTINUE policy, downstream phases run even after failure."""
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(
            name="soft_fail",
            rollback_policy=RollbackPolicy.CONTINUE,
        ))
        pipeline.add_phase(Phase(name="runs_anyway"))

        def executor_fn(phase, context):
            if phase.name == "soft_fail":
                return PhaseResult(
                    phase_name=phase.name,
                    status=PhaseStatus.FAILED,
                    error_message="soft error",
                )
            return PhaseResult(phase_name=phase.name, status=PhaseStatus.COMPLETED)

        executor = PipelineExecutor(pipeline, phase_executor=executor_fn)
        result = executor.run()

        assert result.success is False  # Not all phases succeeded
        assert result.completed_phases == 1
        assert result.failed_phases == 1

    def test_rollback_retry_once(self):
        call_count = {"fails": 0}

        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(
            name="flaky",
            rollback_policy=RollbackPolicy.RETRY_ONCE,
        ))

        def flaky_executor(phase, context):
            call_count["fails"] += 1
            if call_count["fails"] <= 1:
                return PhaseResult(
                    phase_name=phase.name,
                    status=PhaseStatus.FAILED,
                    error_message="transient",
                )
            return PhaseResult(phase_name=phase.name, status=PhaseStatus.COMPLETED)

        executor = PipelineExecutor(pipeline, phase_executor=flaky_executor)
        result = executor.run()

        assert result.success is True
        assert call_count["fails"] == 2  # Original + 1 retry

    def test_disabled_phase_excluded(self):
        """Disabled phases are excluded from execution plan entirely."""
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="active"))
        pipeline.add_phase(Phase(name="disabled", enabled=False))
        pipeline.add_phase(Phase(name="also_active"))

        executor = PipelineExecutor(pipeline)
        result = executor.run()

        assert result.completed_phases == 2
        assert result.success is True
        # Disabled phase is excluded from execution, not tracked as skipped
        phase_names = [r.phase_name for r in result.phase_results]
        assert "disabled" not in phase_names
        assert len(result.phase_results) == 2

    def test_dependency_not_met_skips_phase(self):
        """If a dependency failed, dependent phase should be skipped."""
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(
            name="fails",
            rollback_policy=RollbackPolicy.CONTINUE,  # Don't stop, but mark failed
        ))
        pipeline.add_phase(Phase(
            name="depends_on_failed",
            depends_on=["fails"],
        ))

        def failing_executor(phase, context):
            if phase.name == "fails":
                return PhaseResult(
                    phase_name=phase.name,
                    status=PhaseStatus.FAILED,
                    error_message="nope",
                )
            return PhaseResult(phase_name=phase.name, status=PhaseStatus.COMPLETED)

        executor = PipelineExecutor(pipeline, phase_executor=failing_executor)
        result = executor.run()

        assert result.skipped_phases == 1

    def test_phase_exception_caught(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="throws"))

        def throwing_executor(phase, context):
            raise RuntimeError("unexpected")

        executor = PipelineExecutor(pipeline, phase_executor=throwing_executor)
        result = executor.run()

        assert result.success is False
        assert result.failed_phases == 1
        assert "RuntimeError" in result.phase_results[0].error_message

    def test_callbacks_invoked(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="p1"))

        start_calls = []
        complete_calls = []

        executor = PipelineExecutor(
            pipeline,
            on_phase_start=lambda name: start_calls.append(name),
            on_phase_complete=lambda result: complete_calls.append(result.phase_name),
        )
        executor.run()

        assert start_calls == ["p1"]
        assert complete_calls == ["p1"]

    def test_circular_dependency_returns_failure(self):
        pipeline = Pipeline(name="test")
        pipeline.add_phase(Phase(name="a", depends_on=["b"]))
        pipeline.add_phase(Phase(name="b", depends_on=["a"]))

        executor = PipelineExecutor(pipeline)
        result = executor.run()

        assert result.success is False
        assert any("Circular" in r.error_message for r in result.phase_results if r.error_message)


# ──────────── PhaseResult Tests ────────────


class TestPhaseResult:
    def test_to_dict(self):
        result = PhaseResult(
            phase_name="test",
            status=PhaseStatus.COMPLETED,
            output="done",
            artifacts={"file": "main.py"},
            duration_seconds=5.0,
        )
        data = result.to_dict()
        assert data["phase_name"] == "test"
        assert data["status"] == "completed"
        assert data["artifacts"] == {"file": "main.py"}


# ──────────── PipelineResult Tests ────────────


class TestPipelineResult:
    def test_counts(self):
        result = PipelineResult(
            pipeline_name="test",
            success=False,
            phase_results=[
                PhaseResult(phase_name="a", status=PhaseStatus.COMPLETED),
                PhaseResult(phase_name="b", status=PhaseStatus.FAILED),
                PhaseResult(phase_name="c", status=PhaseStatus.SKIPPED),
            ],
        )
        assert result.completed_phases == 1
        assert result.failed_phases == 1
        assert result.skipped_phases == 1

    def test_to_dict(self):
        result = PipelineResult(
            pipeline_name="test",
            success=True,
            total_duration=10.0,
        )
        data = result.to_dict()
        assert data["pipeline_name"] == "test"
        assert data["success"] is True
        assert data["total_duration"] == 10.0


# ──────────── Pre-built Pipeline Templates ────────────


class TestPipelineTemplates:
    def test_coding_review_pipeline(self):
        pipeline = create_coding_review_pipeline(
            feature_description="Add login",
            coding_model="model-a",
            review_model="model-b",
        )
        assert pipeline.name == "coding_review_test"
        assert pipeline.size == 3
        phases = [p.name for p in pipeline.phases]
        assert phases == ["coding", "review", "testing"]

        # Verify dependencies
        assert pipeline.get_phase("review").depends_on == ["coding"]
        assert pipeline.get_phase("testing").depends_on == ["coding"]

        # Verify models
        assert pipeline.get_phase("coding").agent_config.model == "model-a"
        assert pipeline.get_phase("review").agent_config.model == "model-b"

        # Verify execution order
        order = pipeline.execution_order()
        assert order[0] == "coding"
        assert set(order[1:]) == {"review", "testing"}

    def test_repair_pipeline(self):
        pipeline = create_repair_pipeline(failure_description="Tests fail")
        assert pipeline.name == "repair"
        assert pipeline.size == 3
        order = pipeline.execution_order()
        assert order == ["diagnose", "fix", "verify"]

    def test_coding_review_pipeline_validates(self):
        pipeline = create_coding_review_pipeline()
        issues = pipeline.validate()
        assert issues == []

    def test_repair_pipeline_validates(self):
        pipeline = create_repair_pipeline()
        issues = pipeline.validate()
        assert issues == []
