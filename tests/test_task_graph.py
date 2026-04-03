"""Tests for task_graph module — Task Dependency Graph with skip strategies.

Covers:
- TaskNode lifecycle and serialization
- TaskGraph construction, validation, and cycle detection
- Topological ordering with deterministic tie-breaking
- Skip strategies (SKIP_DEPENDENTS, FAIL_FAST, FORCE_CONTINUE, ISOLATE)
- Ready-task detection and status transitions
- Retry mechanism
- Feature list integration
- Graph serialization round-trip
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from longrun_agent.task_graph import (
    CyclicDependencyError,
    SkipStrategy,
    TaskGraph,
    TaskNode,
    TaskStatus,
)


# ──────────── TaskNode Tests ────────────


class TestTaskNode:
    def test_default_values(self):
        node = TaskNode(task_id="t1")
        assert node.task_id == "t1"
        assert node.description == ""
        assert node.depends_on == []
        assert node.status == TaskStatus.PENDING
        assert node.skip_strategy == SkipStrategy.SKIP_DEPENDENTS
        assert node.error_message is None
        assert node.started_at is None
        assert node.finished_at is None
        assert node.retry_count == 0
        assert node.max_retries == 0

    def test_duration_seconds(self):
        node = TaskNode(task_id="t1", started_at=100.0, finished_at=105.5)
        assert node.duration_seconds == pytest.approx(5.5)

    def test_duration_seconds_incomplete(self):
        node = TaskNode(task_id="t1", started_at=100.0)
        assert node.duration_seconds is None

    def test_can_retry(self):
        node = TaskNode(task_id="t1", max_retries=2, retry_count=0)
        assert node.can_retry is True
        node.retry_count = 2
        assert node.can_retry is False

    def test_serialization_round_trip(self):
        node = TaskNode(
            task_id="t1",
            description="Test task",
            depends_on=["t0"],
            status=TaskStatus.DONE,
            skip_strategy=SkipStrategy.ISOLATE,
            metadata={"key": "value"},
            error_message=None,
            started_at=100.0,
            finished_at=110.0,
            retry_count=1,
            max_retries=3,
        )
        data = node.to_dict()
        restored = TaskNode.from_dict(data)

        assert restored.task_id == node.task_id
        assert restored.description == node.description
        assert restored.depends_on == node.depends_on
        assert restored.status == node.status
        assert restored.skip_strategy == node.skip_strategy
        assert restored.metadata == node.metadata
        assert restored.started_at == node.started_at
        assert restored.finished_at == node.finished_at
        assert restored.retry_count == node.retry_count
        assert restored.max_retries == node.max_retries


# ──────────── TaskGraph Construction Tests ────────────


class TestTaskGraphConstruction:
    def test_empty_graph(self):
        graph = TaskGraph()
        assert graph.size == 0
        assert graph.nodes == {}

    def test_add_task(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1", description="First"))
        assert graph.size == 1
        assert graph.get_task("t1").description == "First"

    def test_add_duplicate_task_raises(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        with pytest.raises(ValueError, match="already exists"):
            graph.add_task(TaskNode(task_id="t1"))

    def test_remove_task(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.add_task(TaskNode(task_id="t2", depends_on=["t1"]))
        removed = graph.remove_task("t1")
        assert removed.task_id == "t1"
        assert graph.size == 1
        # t2's dependency on t1 should be cleaned up
        assert "t1" not in graph.get_task("t2").depends_on

    def test_remove_nonexistent_raises(self):
        graph = TaskGraph()
        with pytest.raises(KeyError, match="not found"):
            graph.remove_task("nonexistent")

    def test_get_task_nonexistent_raises(self):
        graph = TaskGraph()
        with pytest.raises(KeyError, match="not found"):
            graph.get_task("nonexistent")

    def test_get_dependents(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.add_task(TaskNode(task_id="t2", depends_on=["t1"]))
        graph.add_task(TaskNode(task_id="t3", depends_on=["t1"]))
        assert graph.get_dependents("t1") == {"t2", "t3"}

    def test_get_dependencies(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.add_task(TaskNode(task_id="t2"))
        graph.add_task(TaskNode(task_id="t3", depends_on=["t1", "t2"]))
        assert sorted(graph.get_dependencies("t3")) == ["t1", "t2"]

    def test_get_transitive_dependents(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.add_task(TaskNode(task_id="t2", depends_on=["t1"]))
        graph.add_task(TaskNode(task_id="t3", depends_on=["t2"]))
        graph.add_task(TaskNode(task_id="t4", depends_on=["t3"]))
        trans = graph.get_transitive_dependents("t1")
        assert trans == {"t2", "t3", "t4"}


# ──────────── Validation & Cycle Detection ────────────


class TestValidation:
    def test_valid_graph(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.add_task(TaskNode(task_id="t2", depends_on=["t1"]))
        assert graph.validate() == []

    def test_self_dependency(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1", depends_on=["t1"]))
        issues = graph.validate()
        assert any("depends on itself" in i for i in issues)

    def test_missing_dependency(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1", depends_on=["nonexistent"]))
        issues = graph.validate()
        assert any("unknown task" in i for i in issues)

    def test_cycle_detection_simple(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="a", depends_on=["b"]))
        graph.add_task(TaskNode(task_id="b", depends_on=["a"]))
        issues = graph.validate()
        assert any("Cyclic" in i or "yclic" in i for i in issues)

    def test_cycle_detection_three_nodes(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="a", depends_on=["c"]))
        graph.add_task(TaskNode(task_id="b", depends_on=["a"]))
        graph.add_task(TaskNode(task_id="c", depends_on=["b"]))
        issues = graph.validate()
        assert len(issues) > 0


# ──────────── Topological Ordering ────────────


class TestTopologicalOrder:
    def test_linear_chain(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.add_task(TaskNode(task_id="t2", depends_on=["t1"]))
        graph.add_task(TaskNode(task_id="t3", depends_on=["t2"]))
        order = graph.topological_order()
        assert order == ["t1", "t2", "t3"]

    def test_diamond_dependency(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="root"))
        graph.add_task(TaskNode(task_id="left", depends_on=["root"]))
        graph.add_task(TaskNode(task_id="right", depends_on=["root"]))
        graph.add_task(TaskNode(task_id="join", depends_on=["left", "right"]))
        order = graph.topological_order()
        assert order[0] == "root"
        assert order[-1] == "join"
        assert set(order[1:3]) == {"left", "right"}

    def test_independent_tasks(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="c"))
        graph.add_task(TaskNode(task_id="a"))
        graph.add_task(TaskNode(task_id="b"))
        order = graph.topological_order()
        # Deterministic: sorted alphabetically for tie-breaking
        assert order == ["a", "b", "c"]

    def test_cycle_raises(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="a", depends_on=["b"]))
        graph.add_task(TaskNode(task_id="b", depends_on=["a"]))
        with pytest.raises(CyclicDependencyError):
            graph.topological_order()

    def test_missing_dep_raises_value_error(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1", depends_on=["missing"]))
        with pytest.raises(ValueError, match="unknown dependency"):
            graph.topological_order()


# ──────────── Skip Strategies ────────────


class TestSkipStrategies:
    def _build_graph(self, strategy: SkipStrategy) -> TaskGraph:
        """Build a graph: root -> mid -> leaf, with strategy on root."""
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="root", skip_strategy=strategy))
        graph.add_task(TaskNode(task_id="mid", depends_on=["root"]))
        graph.add_task(TaskNode(task_id="leaf", depends_on=["mid"]))
        graph.add_task(TaskNode(task_id="independent"))
        return graph

    def test_skip_dependents(self):
        graph = self._build_graph(SkipStrategy.SKIP_DEPENDENTS)
        graph.mark_running("root")
        skipped = graph.mark_failed("root", "error")
        assert set(skipped) == {"mid", "leaf"}
        assert graph.get_task("mid").status == TaskStatus.SKIPPED
        assert graph.get_task("leaf").status == TaskStatus.SKIPPED
        assert graph.get_task("independent").status == TaskStatus.PENDING

    def test_fail_fast(self):
        graph = self._build_graph(SkipStrategy.FAIL_FAST)
        graph.mark_running("root")
        skipped = graph.mark_failed("root", "error")
        # All pending tasks skipped, including independent
        assert "mid" in skipped
        assert "leaf" in skipped
        assert "independent" in skipped

    def test_force_continue(self):
        graph = self._build_graph(SkipStrategy.FORCE_CONTINUE)
        graph.mark_running("root")
        skipped = graph.mark_failed("root", "error")
        assert skipped == []
        # Dependents should NOT be skipped
        assert graph.get_task("mid").status == TaskStatus.PENDING

    def test_isolate(self):
        graph = self._build_graph(SkipStrategy.ISOLATE)
        graph.mark_running("root")
        skipped = graph.mark_failed("root", "error")
        # Only direct dependent (mid) is skipped
        assert "mid" in skipped
        assert "leaf" not in skipped
        assert graph.get_task("leaf").status == TaskStatus.PENDING

    def test_no_apply_skip(self):
        graph = self._build_graph(SkipStrategy.SKIP_DEPENDENTS)
        graph.mark_running("root")
        skipped = graph.mark_failed("root", "error", apply_skip=False)
        assert skipped == []
        assert graph.get_task("mid").status == TaskStatus.PENDING


# ──────────── Ready Tasks & Status Transitions ────────────


class TestReadyTasks:
    def test_initial_ready_tasks(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.add_task(TaskNode(task_id="t2", depends_on=["t1"]))
        ready = graph.get_ready_tasks()
        assert ready == ["t1"]

    def test_ready_after_dependency_done(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.add_task(TaskNode(task_id="t2", depends_on=["t1"]))
        graph.mark_running("t1")
        graph.mark_done("t1")
        ready = graph.get_ready_tasks()
        assert ready == ["t2"]

    def test_cannot_start_non_pending_task(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.mark_running("t1")
        graph.mark_done("t1")
        with pytest.raises(ValueError, match="Cannot start"):
            graph.mark_running("t1")

    def test_mark_skipped(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.mark_skipped("t1", "Not needed")
        assert graph.get_task("t1").status == TaskStatus.SKIPPED
        assert graph.get_task("t1").error_message == "Not needed"


# ──────────── Retry Mechanism ────────────


class TestRetry:
    def test_retry_resets_status(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1", max_retries=2))
        graph.mark_running("t1")
        graph.mark_failed("t1", "transient error", apply_skip=False)
        assert graph.retry_task("t1") is True
        assert graph.get_task("t1").status == TaskStatus.READY
        assert graph.get_task("t1").retry_count == 1

    def test_retry_exhausted(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1", max_retries=0))
        graph.mark_running("t1")
        graph.mark_failed("t1", "error", apply_skip=False)
        assert graph.retry_task("t1") is False

    def test_retry_unskips_dependents(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1", max_retries=1))
        graph.add_task(TaskNode(task_id="t2", depends_on=["t1"]))
        graph.mark_running("t1")
        graph.mark_failed("t1", "error")
        assert graph.get_task("t2").status == TaskStatus.SKIPPED
        graph.retry_task("t1")
        assert graph.get_task("t2").status == TaskStatus.PENDING

    def test_cannot_retry_non_failed(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1", max_retries=3))
        assert graph.retry_task("t1") is False  # PENDING, not FAILED


# ──────────── Progress & Summary ────────────


class TestProgressAndSummary:
    def test_progress_counts(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.add_task(TaskNode(task_id="t2"))
        graph.add_task(TaskNode(task_id="t3"))
        graph.mark_running("t1")
        graph.mark_done("t1")
        graph.mark_running("t2")
        graph.mark_failed("t2", "err", apply_skip=False)

        prog = graph.progress()
        assert prog["done"] == 1
        assert prog["failed"] == 1
        assert prog["pending"] == 1

    def test_is_complete(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.add_task(TaskNode(task_id="t2"))
        assert graph.is_complete() is False

        graph.mark_running("t1")
        graph.mark_done("t1")
        graph.mark_skipped("t2")
        assert graph.is_complete() is True

    def test_summary(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1"))
        graph.add_task(TaskNode(task_id="t2"))
        graph.mark_running("t1")
        graph.mark_done("t1")
        graph.mark_running("t2")
        graph.mark_failed("t2", "boom", apply_skip=False)

        summary = graph.summary()
        assert summary["total"] == 2
        assert summary["success_rate"] == pytest.approx(50.0)
        assert len(summary["failed_tasks"]) == 1
        assert summary["failed_tasks"][0]["task_id"] == "t2"


# ──────────── Serialization ────────────


class TestSerialization:
    def test_round_trip(self):
        graph = TaskGraph(default_skip_strategy=SkipStrategy.ISOLATE)
        graph.add_task(TaskNode(task_id="a"))
        graph.add_task(TaskNode(task_id="b", depends_on=["a"], metadata={"x": 1}))
        graph.mark_running("a")
        graph.mark_done("a")

        data = graph.to_dict()
        restored = TaskGraph.from_dict(data)

        assert restored.size == 2
        assert restored.default_skip_strategy == SkipStrategy.ISOLATE
        assert restored.get_task("a").status == TaskStatus.DONE
        assert restored.get_task("b").metadata == {"x": 1}

    def test_save_and_load(self):
        graph = TaskGraph()
        graph.add_task(TaskNode(task_id="t1", description="Save test"))
        graph.add_task(TaskNode(task_id="t2", depends_on=["t1"]))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "graph.json"
            graph.save(path)
            loaded = TaskGraph.load(path)
            assert loaded.size == 2
            assert loaded.get_task("t1").description == "Save test"


# ──────────── Feature List Integration ────────────


class TestFeatureListIntegration:
    def test_from_feature_list_sequential(self):
        features = [
            {"category": "core", "description": "Setup", "steps": ["s1"], "passes": True},
            {"category": "auth", "description": "Login", "steps": ["s2"], "passes": False},
            {"category": "api", "description": "REST", "steps": ["s3"], "passes": False},
        ]
        graph = TaskGraph.from_feature_list(features, sequential=True)
        assert graph.size == 3
        assert graph.get_task("feature_0").status == TaskStatus.DONE
        assert graph.get_task("feature_1").status == TaskStatus.PENDING
        order = graph.topological_order()
        assert order == ["feature_0", "feature_1", "feature_2"]

    def test_from_feature_list_category_deps(self):
        features = [
            {"category": "core", "description": "A", "steps": ["s"], "passes": True},
            {"category": "core", "description": "B", "steps": ["s"], "passes": False},
            {"category": "ui", "description": "C", "steps": ["s"], "passes": False},
            {"category": "core", "description": "D", "steps": ["s"], "passes": False},
        ]
        graph = TaskGraph.from_feature_list(features, category_deps=True)
        # Within "core": feature_0 -> feature_1 -> feature_3
        assert "feature_0" in graph.get_task("feature_1").depends_on
        assert "feature_1" in graph.get_task("feature_3").depends_on
        # "ui" is independent
        assert graph.get_task("feature_2").depends_on == []

    def test_from_feature_list_no_deps(self):
        features = [
            {"category": "a", "description": "X", "steps": ["s"], "passes": False},
            {"category": "b", "description": "Y", "steps": ["s"], "passes": False},
        ]
        graph = TaskGraph.from_feature_list(features, sequential=False, category_deps=False)
        assert graph.get_task("feature_0").depends_on == []
        assert graph.get_task("feature_1").depends_on == []

    def test_metadata_preserved(self):
        features = [
            {"category": "core", "description": "Test", "steps": ["step1", "step2"], "passes": False},
        ]
        graph = TaskGraph.from_feature_list(features)
        meta = graph.get_task("feature_0").metadata
        assert meta["feature_index"] == 0
        assert meta["category"] == "core"
        assert meta["steps"] == ["step1", "step2"]
