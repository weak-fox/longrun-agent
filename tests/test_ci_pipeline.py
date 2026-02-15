from longrun_agent.ci_pipeline import PullRequestContext, plan_pipeline_for_pr


def test_non_main_pr_runs_quick_then_core_without_full() -> None:
    plan = plan_pipeline_for_pr(
        PullRequestContext(
            repository_language="go",
            source_branch="feature/ci-speedup",
            target_branch="develop",
            commit_sha="abc1234",
        )
    )

    assert [stage.label for stage in plan.stages] == ["快速检查", "核心回归"]
