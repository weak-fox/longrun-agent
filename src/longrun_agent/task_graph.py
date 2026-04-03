"""Task Dependency Graph with topological execution and skip strategies.

This module provides a DAG-based task orchestration system that models
tasks with explicit dependencies, enabling:

- **Dependency tracking**: Tasks declare upstream dependencies
- **Topological ordering**: Correct execution order respecting dependencies
- **Skip strategies**: When a task fails, dependents can be skipped, force-run, or marked as blocked
- **Cycle detection**: Prevents circular dependency deadlocks
- **Status tracking**: Rich task lifecycle (PENDING → READY → RUNNING → DONE/FAILED/SKIPPED)
- **Serialization**: Full graph state can be checkpointed and restored

Architecture:
    TaskNode: Individual task with metadata, dependencies, and status
    SkipStrategy: Enum controlling what happens to dependents of failed tasks
    TaskGraph: The DAG container with ordering, execution control, and serialization

Integration with Harness:
    The feature_list.json features can be loaded into a TaskGraph, with
    each feature becoming a TaskNode. Dependencies can be inferred from
    category grouping or explicitly declared.

Example:
    >>> graph = TaskGraph()
    >>> graph.add_task(TaskNode(task_id="setup", description="Project setup"))
    >>> graph.add_task(TaskNode(task_id="auth", description="Auth module", depends_on=["setup"]))
    >>> graph.add_task(TaskNode(task_id="api", description="API layer", depends_on=["setup"]))
    >>> graph.add_task(TaskNode(task_id="integration", description="Integration tests", depends_on=["auth", "api"]))
    >>> order = graph.topological_order()
    >>> # order: ["setup", "auth"|"api", "auth"|"api", "integration"]
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class TaskStatus(Enum):
    """Lifecycle states for a task node."""

    PENDING = "pending"      # Not yet ready (unresolved deps)
    READY = "ready"          # All deps satisfied, can run
    RUNNING = "running"      # Currently executing
    DONE = "done"            # Completed successfully
    FAILED = "failed"        # Execution failed
    SKIPPED = "skipped"      # Skipped due to failed dependency


class SkipStrategy(Enum):
    """Controls behavior of dependent tasks when an upstream fails.

    SKIP_DEPENDENTS:  All transitive dependents are marked SKIPPED (default, safest).
    FAIL_FAST:        Entire graph execution stops immediately.
    FORCE_CONTINUE:   Dependents run anyway (useful for independent test suites).
    ISOLATE:          Only direct dependents are skipped; indirect ones may still run
                      if they have alternative satisfied paths.
    """

    SKIP_DEPENDENTS = "skip_dependents"
    FAIL_FAST = "fail_fast"
    FORCE_CONTINUE = "force_continue"
    ISOLATE = "isolate"


@dataclass
class TaskNode:
    """Single task in the dependency graph.

    Attributes:
        task_id: Unique identifier for this task.
        description: Human-readable description.
        depends_on: List of task_ids that must complete before this task.
        status: Current lifecycle state.
        skip_strategy: What to do with dependents if this task fails.
        metadata: Arbitrary key-value metadata (category, feature index, etc.).
        error_message: Error details if status is FAILED.
        started_at: Timestamp when execution started.
        finished_at: Timestamp when execution completed.
        retry_count: Number of retry attempts made.
        max_retries: Maximum retries allowed for this task.
    """

    task_id: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    skip_strategy: SkipStrategy = SkipStrategy.SKIP_DEPENDENTS
    metadata: dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    retry_count: int = 0
    max_retries: int = 0

    @property
    def duration_seconds(self) -> Optional[float]:
        """Wall-clock duration if both timestamps exist."""
        if self.started_at is not None and self.finished_at is not None:
            return self.finished_at - self.started_at
        return None

    @property
    def can_retry(self) -> bool:
        """Whether the task has retries remaining."""
        return self.retry_count < self.max_retries

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "task_id": self.task_id,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "status": self.status.value,
            "skip_strategy": self.skip_strategy.value,
            "metadata": dict(self.metadata),
            "error_message": self.error_message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskNode:
        """Deserialize from a dictionary."""
        return cls(
            task_id=data["task_id"],
            description=data.get("description", ""),
            depends_on=list(data.get("depends_on", [])),
            status=TaskStatus(data.get("status", "pending")),
            skip_strategy=SkipStrategy(data.get("skip_strategy", "skip_dependents")),
            metadata=dict(data.get("metadata", {})),
            error_message=data.get("error_message"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 0),
        )


class CyclicDependencyError(Exception):
    """Raised when the task graph contains a cycle."""

    def __init__(self, cycle_nodes: list[str]):
        self.cycle_nodes = cycle_nodes
        super().__init__(f"Cyclic dependency detected involving: {', '.join(cycle_nodes)}")


class TaskGraph:
    """Directed Acyclic Graph (DAG) for task orchestration.

    Manages task nodes with dependencies, provides topological ordering,
    and implements skip strategies for failed tasks.
    """

    def __init__(self, default_skip_strategy: SkipStrategy = SkipStrategy.SKIP_DEPENDENTS):
        self._nodes: dict[str, TaskNode] = {}
        self._dependents: dict[str, set[str]] = {}  # task_id → set of tasks that depend on it
        self.default_skip_strategy = default_skip_strategy

    @property
    def nodes(self) -> dict[str, TaskNode]:
        """Read-only access to task nodes."""
        return dict(self._nodes)

    @property
    def size(self) -> int:
        """Number of tasks in the graph."""
        return len(self._nodes)

    def add_task(self, task: TaskNode) -> None:
        """Add a task node to the graph.

        Raises:
            ValueError: If task_id already exists or references unknown dependencies.
        """
        if task.task_id in self._nodes:
            raise ValueError(f"Task '{task.task_id}' already exists in the graph")

        # Validate dependencies exist (allow forward references to be added later
        # by deferring validation to build-time methods like topological_order)
        self._nodes[task.task_id] = task
        self._dependents.setdefault(task.task_id, set())

        for dep_id in task.depends_on:
            self._dependents.setdefault(dep_id, set())
            self._dependents[dep_id].add(task.task_id)

    def remove_task(self, task_id: str) -> TaskNode:
        """Remove a task and clean up dependency references.

        Raises:
            KeyError: If task_id does not exist.
        """
        if task_id not in self._nodes:
            raise KeyError(f"Task '{task_id}' not found in graph")

        node = self._nodes.pop(task_id)

        # Remove from dependents index
        for dep_id in node.depends_on:
            if dep_id in self._dependents:
                self._dependents[dep_id].discard(task_id)

        # Remove dependents entries for this task
        if task_id in self._dependents:
            del self._dependents[task_id]

        # Remove this task from other tasks' depends_on lists
        for other_node in self._nodes.values():
            if task_id in other_node.depends_on:
                other_node.depends_on.remove(task_id)

        return node

    def get_task(self, task_id: str) -> TaskNode:
        """Get a task by ID.

        Raises:
            KeyError: If task_id does not exist.
        """
        if task_id not in self._nodes:
            raise KeyError(f"Task '{task_id}' not found in graph")
        return self._nodes[task_id]

    def get_dependents(self, task_id: str) -> set[str]:
        """Get direct dependents (downstream tasks) of a given task."""
        return set(self._dependents.get(task_id, set()))

    def get_dependencies(self, task_id: str) -> list[str]:
        """Get direct dependencies (upstream tasks) of a given task."""
        if task_id not in self._nodes:
            raise KeyError(f"Task '{task_id}' not found in graph")
        return list(self._nodes[task_id].depends_on)

    def get_transitive_dependents(self, task_id: str) -> set[str]:
        """Get all transitive dependents (downstream closure) of a task."""
        visited: set[str] = set()
        queue = deque([task_id])

        while queue:
            current = queue.popleft()
            for dependent in self._dependents.get(current, set()):
                if dependent not in visited:
                    visited.add(dependent)
                    queue.append(dependent)

        return visited

    def validate(self) -> list[str]:
        """Validate graph integrity, returning a list of issues.

        Checks:
        - No missing dependency references
        - No cycles
        - No self-dependencies
        """
        issues: list[str] = []

        for task_id, node in self._nodes.items():
            # Self-dependency check
            if task_id in node.depends_on:
                issues.append(f"Task '{task_id}' depends on itself")

            # Missing dependency check
            for dep_id in node.depends_on:
                if dep_id not in self._nodes:
                    issues.append(
                        f"Task '{task_id}' depends on unknown task '{dep_id}'"
                    )

        # Cycle detection
        try:
            self._detect_cycles()
        except CyclicDependencyError as e:
            issues.append(str(e))

        return issues

    def _detect_cycles(self) -> None:
        """Detect cycles using Kahn's algorithm.

        Raises:
            CyclicDependencyError: If a cycle is detected.
        """
        # Build in-degree map
        in_degree: dict[str, int] = {tid: 0 for tid in self._nodes}
        for node in self._nodes.values():
            for dep_id in node.depends_on:
                if dep_id in in_degree:
                    in_degree[node.task_id] += 0  # placeholder
            # Actually count in-degrees
        for node in self._nodes.values():
            valid_deps = [d for d in node.depends_on if d in self._nodes]
            in_degree[node.task_id] = len(valid_deps)

        # BFS from zero in-degree nodes
        queue = deque([tid for tid, deg in in_degree.items() if deg == 0])
        visited_count = 0

        while queue:
            current = queue.popleft()
            visited_count += 1
            for dependent in self._dependents.get(current, set()):
                if dependent in in_degree:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        if visited_count < len(self._nodes):
            # Find nodes involved in cycles
            cycle_nodes = [tid for tid, deg in in_degree.items() if deg > 0]
            raise CyclicDependencyError(cycle_nodes)

    def topological_order(self) -> list[str]:
        """Return tasks in a valid topological execution order.

        Uses Kahn's algorithm (BFS-based) for stable, deterministic ordering.

        Raises:
            CyclicDependencyError: If the graph contains a cycle.
            ValueError: If there are missing dependency references.
        """
        # Pre-validate references
        for node in self._nodes.values():
            for dep_id in node.depends_on:
                if dep_id not in self._nodes:
                    raise ValueError(
                        f"Task '{node.task_id}' references unknown dependency '{dep_id}'"
                    )

        # Build in-degree map
        in_degree: dict[str, int] = {}
        for tid, node in self._nodes.items():
            in_degree[tid] = len([d for d in node.depends_on if d in self._nodes])

        # Use sorted() for deterministic tie-breaking
        queue = deque(sorted(tid for tid, deg in in_degree.items() if deg == 0))
        result: list[str] = []

        while queue:
            current = queue.popleft()
            result.append(current)

            # Collect and sort dependents for determinism
            dependents = sorted(self._dependents.get(current, set()))
            for dependent in dependents:
                if dependent in in_degree:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        if len(result) < len(self._nodes):
            cycle_nodes = sorted(tid for tid, deg in in_degree.items() if deg > 0)
            raise CyclicDependencyError(cycle_nodes)

        return result

    def get_ready_tasks(self) -> list[str]:
        """Get tasks that are ready to execute (all dependencies satisfied).

        A task is ready when:
        - Its status is PENDING or READY
        - All dependencies have status DONE (or SKIPPED with FORCE_CONTINUE strategy)
        """
        ready: list[str] = []
        for task_id, node in self._nodes.items():
            if node.status not in (TaskStatus.PENDING, TaskStatus.READY):
                continue

            deps_satisfied = True
            for dep_id in node.depends_on:
                dep_node = self._nodes.get(dep_id)
                if dep_node is None:
                    deps_satisfied = False
                    break
                if dep_node.status == TaskStatus.DONE:
                    continue
                if dep_node.status == TaskStatus.SKIPPED and dep_node.skip_strategy == SkipStrategy.FORCE_CONTINUE:
                    continue
                deps_satisfied = False
                break

            if deps_satisfied:
                node.status = TaskStatus.READY
                ready.append(task_id)

        return sorted(ready)

    def mark_running(self, task_id: str) -> None:
        """Mark a task as currently running."""
        node = self.get_task(task_id)
        if node.status not in (TaskStatus.PENDING, TaskStatus.READY):
            raise ValueError(
                f"Cannot start task '{task_id}' in state {node.status.value}"
            )
        node.status = TaskStatus.RUNNING
        node.started_at = time.time()

    def mark_done(self, task_id: str) -> None:
        """Mark a task as successfully completed."""
        node = self.get_task(task_id)
        node.status = TaskStatus.DONE
        node.finished_at = time.time()
        node.error_message = None

    def mark_failed(
        self,
        task_id: str,
        error_message: str = "",
        apply_skip: bool = True,
    ) -> list[str]:
        """Mark a task as failed and optionally apply skip strategy.

        Args:
            task_id: The failed task.
            error_message: Description of the failure.
            apply_skip: Whether to propagate skip strategy to dependents.

        Returns:
            List of task IDs that were skipped as a result.
        """
        node = self.get_task(task_id)
        node.status = TaskStatus.FAILED
        node.finished_at = time.time()
        node.error_message = error_message

        skipped: list[str] = []
        if apply_skip:
            skipped = self._apply_skip_strategy(task_id)

        return skipped

    def mark_skipped(self, task_id: str, reason: str = "") -> None:
        """Manually mark a task as skipped."""
        node = self.get_task(task_id)
        node.status = TaskStatus.SKIPPED
        node.finished_at = time.time()
        node.error_message = reason or "Skipped by user/policy"

    def retry_task(self, task_id: str) -> bool:
        """Retry a failed task if retries are available.

        Returns:
            True if the task was reset for retry, False if no retries remain.
        """
        node = self.get_task(task_id)
        if node.status != TaskStatus.FAILED:
            return False
        if not node.can_retry:
            return False

        node.retry_count += 1
        node.status = TaskStatus.READY
        node.error_message = None
        node.started_at = None
        node.finished_at = None

        # Un-skip dependents that were skipped due to this failure
        for dep_id in self.get_transitive_dependents(task_id):
            dep_node = self._nodes[dep_id]
            if dep_node.status == TaskStatus.SKIPPED:
                dep_node.status = TaskStatus.PENDING
                dep_node.error_message = None

        return True

    def _apply_skip_strategy(self, failed_task_id: str) -> list[str]:
        """Apply the failed task's skip strategy to its dependents.

        Returns:
            List of task IDs that were skipped.
        """
        node = self._nodes[failed_task_id]
        strategy = node.skip_strategy
        skipped: list[str] = []

        if strategy == SkipStrategy.FORCE_CONTINUE:
            # Don't skip anything; dependents will run anyway
            return skipped

        if strategy == SkipStrategy.FAIL_FAST:
            # Skip ALL remaining non-terminal tasks
            for tid, tnode in self._nodes.items():
                if tnode.status in (TaskStatus.PENDING, TaskStatus.READY):
                    tnode.status = TaskStatus.SKIPPED
                    tnode.error_message = (
                        f"Skipped: fail-fast triggered by '{failed_task_id}'"
                    )
                    skipped.append(tid)
            return skipped

        if strategy == SkipStrategy.ISOLATE:
            # Only skip direct dependents
            for dep_id in self._dependents.get(failed_task_id, set()):
                dep_node = self._nodes.get(dep_id)
                if dep_node and dep_node.status in (TaskStatus.PENDING, TaskStatus.READY):
                    dep_node.status = TaskStatus.SKIPPED
                    dep_node.error_message = (
                        f"Skipped: direct dependency '{failed_task_id}' failed (isolate)"
                    )
                    skipped.append(dep_id)
            return skipped

        # Default: SKIP_DEPENDENTS — skip all transitive dependents
        for dep_id in self.get_transitive_dependents(failed_task_id):
            dep_node = self._nodes[dep_id]
            if dep_node.status in (TaskStatus.PENDING, TaskStatus.READY):
                dep_node.status = TaskStatus.SKIPPED
                dep_node.error_message = (
                    f"Skipped: upstream dependency '{failed_task_id}' failed"
                )
                skipped.append(dep_id)

        return skipped

    # ──────────── Status & Reporting ────────────

    def progress(self) -> dict[str, int]:
        """Return count of tasks in each status."""
        counts: dict[str, int] = {s.value: 0 for s in TaskStatus}
        for node in self._nodes.values():
            counts[node.status.value] += 1
        return counts

    def is_complete(self) -> bool:
        """Check if all tasks are in a terminal state (DONE, FAILED, or SKIPPED)."""
        terminal = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.SKIPPED}
        return all(node.status in terminal for node in self._nodes.values())

    def summary(self) -> dict[str, Any]:
        """Generate a comprehensive execution summary."""
        prog = self.progress()
        total = self.size
        done = prog.get("done", 0)
        failed_nodes = [
            {"task_id": n.task_id, "error": n.error_message}
            for n in self._nodes.values()
            if n.status == TaskStatus.FAILED
        ]
        skipped_nodes = [
            {"task_id": n.task_id, "reason": n.error_message}
            for n in self._nodes.values()
            if n.status == TaskStatus.SKIPPED
        ]

        return {
            "total": total,
            "progress": prog,
            "success_rate": (done / total * 100) if total > 0 else 0.0,
            "is_complete": self.is_complete(),
            "failed_tasks": failed_nodes,
            "skipped_tasks": skipped_nodes,
        }

    # ──────────── Serialization ────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full graph state."""
        return {
            "default_skip_strategy": self.default_skip_strategy.value,
            "tasks": [node.to_dict() for node in self._nodes.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskGraph:
        """Restore a graph from serialized state."""
        strategy = SkipStrategy(data.get("default_skip_strategy", "skip_dependents"))
        graph = cls(default_skip_strategy=strategy)
        for task_data in data.get("tasks", []):
            graph.add_task(TaskNode.from_dict(task_data))
        return graph

    def save(self, path: Path) -> None:
        """Save graph state to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: Path) -> TaskGraph:
        """Load graph state from a JSON file."""
        data = json.loads(path.read_text())
        return cls.from_dict(data)

    # ──────────── Feature List Integration ────────────

    @classmethod
    def from_feature_list(
        cls,
        features: list[dict[str, Any]],
        sequential: bool = False,
        category_deps: bool = True,
    ) -> TaskGraph:
        """Build a TaskGraph from a feature_list.json structure.

        Args:
            features: List of feature dicts with category, description, steps, passes.
            sequential: If True, each feature depends on the previous one.
            category_deps: If True, features within the same category form a chain.

        Returns:
            A TaskGraph with one TaskNode per feature.
        """
        graph = cls()
        category_last: dict[str, str] = {}  # category → last task_id in that category

        for index, feature in enumerate(features):
            task_id = f"feature_{index}"
            depends_on: list[str] = []

            if sequential and index > 0:
                depends_on.append(f"feature_{index - 1}")
            elif category_deps:
                category = feature.get("category", "default")
                if category in category_last:
                    depends_on.append(category_last[category])
                category_last[category] = task_id

            status = TaskStatus.DONE if feature.get("passes") else TaskStatus.PENDING

            node = TaskNode(
                task_id=task_id,
                description=feature.get("description", f"Feature #{index}"),
                depends_on=depends_on,
                status=status,
                metadata={
                    "feature_index": index,
                    "category": feature.get("category", ""),
                    "steps": feature.get("steps", []),
                    "passes": feature.get("passes", False),
                },
            )
            graph.add_task(node)

        return graph
