"""PR pipeline stage planning for CI workflows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PullRequestContext:
    """Inputs used to determine which CI stages should run."""

    repository_language: str
    source_branch: str
    target_branch: str
    commit_sha: str


@dataclass(frozen=True, slots=True)
class PipelineStage:
    """Single CI stage in the execution plan."""

    key: str
    label: str


@dataclass(frozen=True, slots=True)
class PipelinePlan:
    """Ordered CI stage plan for one PR update."""

    pr: PullRequestContext
    stages: tuple[PipelineStage, ...]


QUICK_CHECK = PipelineStage(key="quick_check", label="快速检查")
CORE_REGRESSION = PipelineStage(key="core_regression", label="核心回归")
FULL_REGRESSION = PipelineStage(key="full_regression", label="全量回归")
DEFAULT_MAINLINE_BRANCHES = ("main", "master", "trunk")


def _is_mainline_related(target_branch: str, mainline_branches: tuple[str, ...]) -> bool:
    normalized = target_branch.strip().lower()
    return normalized in {branch.strip().lower() for branch in mainline_branches}


def plan_pipeline_for_pr(
    pr: PullRequestContext,
    *,
    mainline_branches: tuple[str, ...] = DEFAULT_MAINLINE_BRANCHES,
) -> PipelinePlan:
    """Plan stage order for a PR execution."""
    if not mainline_branches:
        raise ValueError("mainline_branches cannot be empty")

    stages = [QUICK_CHECK, CORE_REGRESSION]
    if _is_mainline_related(pr.target_branch, mainline_branches):
        stages.append(FULL_REGRESSION)

    return PipelinePlan(pr=pr, stages=tuple(stages))
