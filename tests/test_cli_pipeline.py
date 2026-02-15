from longrun_agent.cli import build_parser, run_simulate_pr


def test_parser_accepts_simulate_pr_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "simulate-pr",
            "--repository-language",
            "go",
            "--source-branch",
            "feature/ci-speedup",
            "--target-branch",
            "develop",
            "--commit-sha",
            "abc1234",
        ]
    )

    assert args.command == "simulate-pr"
    assert args.repository_language == "go"
    assert args.source_branch == "feature/ci-speedup"
    assert args.target_branch == "develop"
    assert args.commit_sha == "abc1234"


def test_run_simulate_pr_for_non_main_branch_prints_two_stages(capsys) -> None:
    code = run_simulate_pr(
        repository_language="go",
        source_branch="feature/ci-speedup",
        target_branch="develop",
        commit_sha="abc1234",
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "快速检查" in output
    assert "核心回归" in output
    assert "全量回归" not in output
