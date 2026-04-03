"""Multi-Agent Collaboration via Pipeline Phases.

This module implements a pipeline-based multi-agent orchestration system
where complex tasks are decomposed into phases, each handled by a
specialized agent configuration.

Architecture:
    Phase: A named stage with its own agent config, prompt template, and gates.
    Pipeline: An ordered sequence of phases with inter-phase data flow.
    PipelineExecutor: Orchestrates phase execution with handoff, rollback, and reporting.

Design Principles:
    1. **Separation of Concerns**: Each phase has a focused role (coding, review, test, deploy).
    2. **Data Handoff**: Phases pass structured artifacts to the next phase.
    3. **Independent Failure**: A phase failure can trigger rollback or skip downstream phases.
    4. **Composability**: Pipelines can be built from reusable phase definitions.
    5. **Integration**: Works with existing Agent, TaskGraph, and Gate infrastructure.

Example:
    >>> pipeline = Pipeline("feature_implementation")
    >>> pipeline.add_phase(Phase(
    ...     name="coding",
    ...     agent_config={"model": "claude-sonnet-4-20250514", "max_tokens": 16384},
    ...     prompt_template="Implement the following feature: {feature_description}",
    ... ))
    >>> pipeline.add_phase(Phase(
    ...     name="review",
    ...     agent_config={"model": "claude-sonnet-4-20250514", "max_tokens": 8192},
    ...     prompt_template="Review the code changes: {coding_output}",
    ...     depends_on=["coding"],
    ... ))
    >>> executor = PipelineExecutor(pipeline)
    >>> result = executor.run(context={"feature_description": "Add user auth"})
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class PhaseStatus(Enum):
    """Lifecycle status of a pipeline phase."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"


class RollbackPolicy(Enum):
    """What to do when a phase fails."""

    STOP = "stop"                  # Stop the pipeline (default)
    SKIP_DOWNSTREAM = "skip_downstream"  # Skip remaining phases
    RETRY_ONCE = "retry_once"      # Retry the failed phase once
    CONTINUE = "continue"          # Continue with next phase regardless


@dataclass
class AgentConfig:
    """Configuration for an agent instance within a phase.

    Each phase can have its own model, token limits, and behavioral settings.
    This allows using different models for different concerns (e.g., a fast model
    for linting, a powerful model for coding, a reasoning model for review).
    """

    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 16384
    max_iterations: int = 100
    temperature: Optional[float] = None
    system_prompt_override: Optional[str] = None
    tools_override: Optional[list[dict]] = None
    extra_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "max_iterations": self.max_iterations,
        }
        if self.temperature is not None:
            result["temperature"] = self.temperature
        if self.system_prompt_override is not None:
            result["system_prompt_override"] = self.system_prompt_override
        if self.tools_override is not None:
            result["tools_override"] = self.tools_override
        if self.extra_params:
            result["extra_params"] = dict(self.extra_params)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentConfig:
        return cls(
            model=data.get("model", "claude-sonnet-4-20250514"),
            max_tokens=data.get("max_tokens", 16384),
            max_iterations=data.get("max_iterations", 100),
            temperature=data.get("temperature"),
            system_prompt_override=data.get("system_prompt_override"),
            tools_override=data.get("tools_override"),
            extra_params=data.get("extra_params", {}),
        )


@dataclass
class PhaseResult:
    """Result of executing a single phase.

    Attributes:
        phase_name: Name of the phase.
        status: Final status of the phase.
        output: The agent's output text.
        artifacts: Structured data passed to downstream phases.
        error_message: Error details if failed.
        duration_seconds: Wall-clock execution time.
        started_at: Timestamp when phase started.
        finished_at: Timestamp when phase finished.
        retry_count: Number of retries attempted.
    """

    phase_name: str
    status: PhaseStatus
    output: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    duration_seconds: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase_name": self.phase_name,
            "status": self.status.value,
            "output": self.output,
            "artifacts": dict(self.artifacts),
            "error_message": self.error_message,
            "duration_seconds": self.duration_seconds,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "retry_count": self.retry_count,
        }


@dataclass
class Phase:
    """A single phase in a multi-agent pipeline.

    Each phase represents a distinct concern or step in a larger workflow.
    Phases have their own agent configuration, prompt template, validation
    gates, and rollback policy.

    Attributes:
        name: Unique phase identifier.
        description: Human-readable description of what this phase does.
        agent_config: Agent configuration for this phase.
        prompt_template: Template string with {variable} placeholders.
        depends_on: Phase names that must complete before this phase.
        rollback_policy: What to do if this phase fails.
        gates: List of validation checks to run after phase execution.
        timeout_seconds: Maximum execution time for this phase.
        enabled: Whether this phase is active (disabled phases are skipped).
        metadata: Arbitrary key-value metadata.
    """

    name: str
    description: str = ""
    agent_config: AgentConfig = field(default_factory=AgentConfig)
    prompt_template: str = ""
    depends_on: list[str] = field(default_factory=list)
    rollback_policy: RollbackPolicy = RollbackPolicy.STOP
    gates: list[dict[str, Any]] = field(default_factory=list)
    timeout_seconds: int = 3600
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def render_prompt(self, context: dict[str, Any]) -> str:
        """Render the prompt template with the given context.

        Uses safe string formatting that ignores missing keys and handles
        nested context gracefully.

        Args:
            context: Dictionary of variables to interpolate.

        Returns:
            The rendered prompt string.
        """
        prompt = self.prompt_template
        for key, value in context.items():
            placeholder = "{" + key + "}"
            if placeholder in prompt:
                prompt = prompt.replace(placeholder, str(value))
        return prompt

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "agent_config": self.agent_config.to_dict(),
            "prompt_template": self.prompt_template,
            "depends_on": list(self.depends_on),
            "rollback_policy": self.rollback_policy.value,
            "gates": list(self.gates),
            "timeout_seconds": self.timeout_seconds,
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Phase:
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            agent_config=AgentConfig.from_dict(data.get("agent_config", {})),
            prompt_template=data.get("prompt_template", ""),
            depends_on=list(data.get("depends_on", [])),
            rollback_policy=RollbackPolicy(data.get("rollback_policy", "stop")),
            gates=list(data.get("gates", [])),
            timeout_seconds=data.get("timeout_seconds", 3600),
            enabled=data.get("enabled", True),
            metadata=data.get("metadata", {}),
        )


class Pipeline:
    """Ordered collection of phases forming a multi-agent workflow.

    A pipeline defines the sequence of phases, manages phase dependencies,
    and provides methods for execution planning.
    """

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._phases: dict[str, Phase] = {}
        self._order: list[str] = []  # Insertion order

    @property
    def phases(self) -> list[Phase]:
        """Get phases in insertion order."""
        return [self._phases[name] for name in self._order]

    @property
    def size(self) -> int:
        return len(self._phases)

    def add_phase(self, phase: Phase) -> None:
        """Add a phase to the pipeline.

        Raises:
            ValueError: If phase name already exists.
        """
        if phase.name in self._phases:
            raise ValueError(f"Phase '{phase.name}' already exists in pipeline")
        self._phases[phase.name] = phase
        self._order.append(phase.name)

    def remove_phase(self, name: str) -> Phase:
        """Remove a phase from the pipeline.

        Raises:
            KeyError: If phase does not exist.
        """
        if name not in self._phases:
            raise KeyError(f"Phase '{name}' not found in pipeline")

        phase = self._phases.pop(name)
        self._order.remove(name)

        # Clean up dependencies
        for other in self._phases.values():
            if name in other.depends_on:
                other.depends_on.remove(name)

        return phase

    def get_phase(self, name: str) -> Phase:
        """Get a phase by name.

        Raises:
            KeyError: If phase does not exist.
        """
        if name not in self._phases:
            raise KeyError(f"Phase '{name}' not found in pipeline")
        return self._phases[name]

    def execution_order(self) -> list[str]:
        """Return phases in a valid execution order.

        If phases have explicit dependencies, performs topological sort.
        Otherwise returns insertion order filtered by enabled status.

        Returns:
            List of phase names in execution order.

        Raises:
            ValueError: If circular dependencies are detected.
        """
        enabled_phases = {
            name: phase for name, phase in self._phases.items() if phase.enabled
        }

        # Check if any phase has explicit dependencies
        has_deps = any(phase.depends_on for phase in enabled_phases.values())

        if not has_deps:
            return [name for name in self._order if name in enabled_phases]

        # Topological sort using Kahn's algorithm
        in_degree: dict[str, int] = {name: 0 for name in enabled_phases}
        for name, phase in enabled_phases.items():
            for dep in phase.depends_on:
                if dep in enabled_phases:
                    in_degree[name] = in_degree.get(name, 0) + 1

        # Recount properly
        for name in enabled_phases:
            in_degree[name] = sum(
                1 for dep in enabled_phases[name].depends_on if dep in enabled_phases
            )

        from collections import deque
        queue = deque(sorted(n for n, d in in_degree.items() if d == 0))
        result: list[str] = []

        while queue:
            current = queue.popleft()
            result.append(current)
            for name, phase in enabled_phases.items():
                if current in phase.depends_on:
                    in_degree[name] -= 1
                    if in_degree[name] == 0:
                        queue.append(name)

        if len(result) < len(enabled_phases):
            missing = set(enabled_phases) - set(result)
            raise ValueError(f"Circular dependency detected among phases: {missing}")

        return result

    def validate(self) -> list[str]:
        """Validate pipeline configuration.

        Returns:
            List of validation issues (empty if valid).
        """
        issues: list[str] = []

        if not self._phases:
            issues.append("Pipeline has no phases")

        for name, phase in self._phases.items():
            for dep in phase.depends_on:
                if dep not in self._phases:
                    issues.append(
                        f"Phase '{name}' depends on unknown phase '{dep}'"
                    )

        try:
            self.execution_order()
        except ValueError as e:
            issues.append(str(e))

        return issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "phases": [self._phases[n].to_dict() for n in self._order],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pipeline:
        pipeline = cls(
            name=data.get("name", "unnamed"),
            description=data.get("description", ""),
        )
        for phase_data in data.get("phases", []):
            pipeline.add_phase(Phase.from_dict(phase_data))
        return pipeline

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: Path) -> Pipeline:
        data = json.loads(path.read_text())
        return cls.from_dict(data)


@dataclass
class PipelineResult:
    """Aggregated result of a full pipeline execution.

    Attributes:
        pipeline_name: Name of the pipeline.
        success: Whether all phases completed successfully.
        phase_results: Results for each executed phase.
        total_duration: Total wall-clock time.
        started_at: Pipeline start timestamp.
        finished_at: Pipeline finish timestamp.
        context: Final accumulated context.
    """

    pipeline_name: str
    success: bool
    phase_results: list[PhaseResult] = field(default_factory=list)
    total_duration: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def completed_phases(self) -> int:
        return sum(1 for r in self.phase_results if r.status == PhaseStatus.COMPLETED)

    @property
    def failed_phases(self) -> int:
        return sum(1 for r in self.phase_results if r.status == PhaseStatus.FAILED)

    @property
    def skipped_phases(self) -> int:
        return sum(1 for r in self.phase_results if r.status == PhaseStatus.SKIPPED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_name": self.pipeline_name,
            "success": self.success,
            "phase_results": [r.to_dict() for r in self.phase_results],
            "total_duration": self.total_duration,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "completed_phases": self.completed_phases,
            "failed_phases": self.failed_phases,
            "skipped_phases": self.skipped_phases,
        }


# Type alias for phase executor function
PhaseExecutorFn = Callable[[Phase, dict[str, Any]], PhaseResult]


class PipelineExecutor:
    """Orchestrates the execution of a multi-agent pipeline.

    The executor runs phases in order, manages context passing between phases,
    handles failures according to rollback policies, and produces a comprehensive
    execution report.

    Args:
        pipeline: The pipeline to execute.
        phase_executor: Optional custom function to execute individual phases.
            If not provided, uses a default executor that calls the Phase's
            prompt template rendering.
        on_phase_start: Callback invoked when a phase begins.
        on_phase_complete: Callback invoked when a phase finishes.
    """

    def __init__(
        self,
        pipeline: Pipeline,
        phase_executor: Optional[PhaseExecutorFn] = None,
        on_phase_start: Optional[Callable[[str], None]] = None,
        on_phase_complete: Optional[Callable[[PhaseResult], None]] = None,
    ):
        self.pipeline = pipeline
        self._phase_executor = phase_executor or self._default_phase_executor
        self._on_phase_start = on_phase_start
        self._on_phase_complete = on_phase_complete

    def run(self, context: Optional[dict[str, Any]] = None) -> PipelineResult:
        """Execute the full pipeline.

        Args:
            context: Initial context variables available to all phases.

        Returns:
            PipelineResult with aggregated execution data.
        """
        ctx = dict(context or {})
        pipeline_start = time.time()
        phase_results: list[PhaseResult] = []
        all_success = True
        should_stop = False

        try:
            execution_order = self.pipeline.execution_order()
        except ValueError as e:
            return PipelineResult(
                pipeline_name=self.pipeline.name,
                success=False,
                phase_results=[PhaseResult(
                    phase_name="__pipeline_validation__",
                    status=PhaseStatus.FAILED,
                    error_message=str(e),
                )],
                total_duration=time.time() - pipeline_start,
                started_at=pipeline_start,
                finished_at=time.time(),
                context=ctx,
            )

        for phase_name in execution_order:
            if should_stop:
                phase_results.append(PhaseResult(
                    phase_name=phase_name,
                    status=PhaseStatus.SKIPPED,
                    error_message="Skipped: pipeline stopped due to upstream failure",
                ))
                continue

            phase = self.pipeline.get_phase(phase_name)

            if not phase.enabled:
                phase_results.append(PhaseResult(
                    phase_name=phase_name,
                    status=PhaseStatus.SKIPPED,
                    error_message="Phase is disabled",
                ))
                continue

            # Check if dependencies are satisfied
            deps_ok = self._check_dependencies(phase, phase_results)
            if not deps_ok:
                phase_results.append(PhaseResult(
                    phase_name=phase_name,
                    status=PhaseStatus.SKIPPED,
                    error_message="Skipped: one or more dependencies not completed",
                ))
                all_success = False
                continue

            if self._on_phase_start:
                self._on_phase_start(phase_name)

            result = self._execute_phase_with_retry(phase, ctx)
            phase_results.append(result)

            if self._on_phase_complete:
                self._on_phase_complete(result)

            if result.status == PhaseStatus.COMPLETED:
                # Merge artifacts into context for downstream phases
                ctx[f"{phase_name}_output"] = result.output
                ctx[f"{phase_name}_artifacts"] = result.artifacts
                ctx.update(result.artifacts)
            else:
                all_success = False
                should_stop = self._should_stop(phase, result)

        pipeline_end = time.time()
        return PipelineResult(
            pipeline_name=self.pipeline.name,
            success=all_success,
            phase_results=phase_results,
            total_duration=pipeline_end - pipeline_start,
            started_at=pipeline_start,
            finished_at=pipeline_end,
            context=ctx,
        )

    def _execute_phase_with_retry(
        self,
        phase: Phase,
        context: dict[str, Any],
    ) -> PhaseResult:
        """Execute a phase with optional retry on failure."""
        result = self._run_single_phase(phase, context)

        if result.status == PhaseStatus.FAILED and phase.rollback_policy == RollbackPolicy.RETRY_ONCE:
            if result.retry_count < 1:
                logger.info("Retrying phase '%s' (policy: retry_once)", phase.name)
                retry_result = self._run_single_phase(phase, context)
                retry_result.retry_count = 1
                return retry_result

        return result

    def _run_single_phase(
        self,
        phase: Phase,
        context: dict[str, Any],
    ) -> PhaseResult:
        """Execute a single phase."""
        start_time = time.time()

        try:
            result = self._phase_executor(phase, context)
            result.started_at = start_time
            result.finished_at = time.time()
            result.duration_seconds = result.finished_at - start_time
            return result
        except Exception as exc:
            end_time = time.time()
            logger.exception("Phase '%s' raised an exception", phase.name)
            return PhaseResult(
                phase_name=phase.name,
                status=PhaseStatus.FAILED,
                error_message=f"Exception: {type(exc).__name__}: {exc}",
                duration_seconds=end_time - start_time,
                started_at=start_time,
                finished_at=end_time,
            )

    def _check_dependencies(
        self,
        phase: Phase,
        completed_results: list[PhaseResult],
    ) -> bool:
        """Check if all dependencies of a phase are satisfied."""
        if not phase.depends_on:
            return True

        completed_phases = {
            r.phase_name
            for r in completed_results
            if r.status == PhaseStatus.COMPLETED
        }

        return all(dep in completed_phases for dep in phase.depends_on)

    def _should_stop(self, phase: Phase, result: PhaseResult) -> bool:
        """Determine if the pipeline should stop based on failure and policy."""
        if result.status != PhaseStatus.FAILED:
            return False

        if phase.rollback_policy == RollbackPolicy.STOP:
            return True
        if phase.rollback_policy == RollbackPolicy.SKIP_DOWNSTREAM:
            return True
        # CONTINUE and RETRY_ONCE (after retry) don't stop
        return False

    @staticmethod
    def _default_phase_executor(phase: Phase, context: dict[str, Any]) -> PhaseResult:
        """Default phase executor that renders the prompt template.

        In production, this would be replaced with an actual Agent.run() call.
        For now, it demonstrates the contract: render prompt → execute → return result.
        """
        try:
            rendered_prompt = phase.render_prompt(context)
            return PhaseResult(
                phase_name=phase.name,
                status=PhaseStatus.COMPLETED,
                output=rendered_prompt,
                artifacts={"rendered_prompt": rendered_prompt},
            )
        except Exception as exc:
            return PhaseResult(
                phase_name=phase.name,
                status=PhaseStatus.FAILED,
                error_message=str(exc),
            )


# ──────────── Pre-built Pipeline Templates ────────────

def create_coding_review_pipeline(
    feature_description: str = "",
    coding_model: str = "claude-sonnet-4-20250514",
    review_model: str = "claude-sonnet-4-20250514",
    test_model: str = "claude-sonnet-4-20250514",
) -> Pipeline:
    """Create a standard coding → review → test pipeline.

    This is the most common multi-agent pattern: one agent writes code,
    another reviews it, and a third runs verification.
    """
    pipeline = Pipeline(
        name="coding_review_test",
        description="Standard feature implementation pipeline with code review and testing",
    )

    pipeline.add_phase(Phase(
        name="coding",
        description="Implement the feature according to specification",
        agent_config=AgentConfig(
            model=coding_model,
            max_tokens=16384,
            max_iterations=200,
        ),
        prompt_template=(
            "You are a senior software engineer. Implement the following feature:\n\n"
            f"{feature_description or '{feature_description}'}\n\n"
            "Requirements:\n"
            "- Write clean, well-tested code\n"
            "- Follow existing project conventions\n"
            "- Include docstrings and type hints\n"
            "- Commit your changes with a descriptive message"
        ),
        rollback_policy=RollbackPolicy.STOP,
    ))

    pipeline.add_phase(Phase(
        name="review",
        description="Review the code changes for quality and correctness",
        agent_config=AgentConfig(
            model=review_model,
            max_tokens=8192,
            max_iterations=50,
        ),
        prompt_template=(
            "You are a code reviewer. Review the following changes:\n\n"
            "{coding_output}\n\n"
            "Check for:\n"
            "- Code correctness and edge cases\n"
            "- Security vulnerabilities\n"
            "- Performance issues\n"
            "- API design and naming\n"
            "- Test coverage gaps\n\n"
            "If issues are found, provide specific fix suggestions."
        ),
        depends_on=["coding"],
        rollback_policy=RollbackPolicy.CONTINUE,
    ))

    pipeline.add_phase(Phase(
        name="testing",
        description="Run tests and verify the implementation",
        agent_config=AgentConfig(
            model=test_model,
            max_tokens=8192,
            max_iterations=100,
        ),
        prompt_template=(
            "You are a QA engineer. Run the test suite and verify:\n\n"
            "1. All existing tests still pass\n"
            "2. New tests cover the implemented feature\n"
            "3. Edge cases are handled\n\n"
            "Review feedback from code review:\n{review_output}\n\n"
            "Fix any issues found during testing."
        ),
        depends_on=["coding"],
        rollback_policy=RollbackPolicy.STOP,
    ))

    return pipeline


def create_repair_pipeline(
    failure_description: str = "",
    model: str = "claude-sonnet-4-20250514",
) -> Pipeline:
    """Create a diagnose → fix → verify repair pipeline."""
    pipeline = Pipeline(
        name="repair",
        description="Automated repair pipeline for failed verifications",
    )

    pipeline.add_phase(Phase(
        name="diagnose",
        description="Analyze the failure and identify root cause",
        agent_config=AgentConfig(model=model, max_tokens=4096, max_iterations=30),
        prompt_template=(
            "Analyze this failure and identify the root cause:\n\n"
            f"{failure_description or '{failure_description}'}\n\n"
            "Output a structured diagnosis with:\n"
            "1. Root cause\n"
            "2. Affected files\n"
            "3. Suggested fix approach"
        ),
        rollback_policy=RollbackPolicy.STOP,
    ))

    pipeline.add_phase(Phase(
        name="fix",
        description="Apply the fix based on diagnosis",
        agent_config=AgentConfig(model=model, max_tokens=16384, max_iterations=100),
        prompt_template=(
            "Apply the fix based on this diagnosis:\n\n"
            "{diagnose_output}\n\n"
            "Make minimal, targeted changes. Do not refactor unrelated code."
        ),
        depends_on=["diagnose"],
        rollback_policy=RollbackPolicy.STOP,
    ))

    pipeline.add_phase(Phase(
        name="verify",
        description="Run verification to confirm the fix",
        agent_config=AgentConfig(model=model, max_tokens=4096, max_iterations=50),
        prompt_template=(
            "Verify that the fix resolved the original issue:\n\n"
            "Original failure: {failure_description}\n"
            "Fix applied: {fix_output}\n\n"
            "Run relevant tests and confirm the fix."
        ),
        depends_on=["fix"],
        rollback_policy=RollbackPolicy.STOP,
    ))

    return pipeline
