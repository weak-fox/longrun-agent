from longrun_agent.runtime.prompt_provider import PromptProvider


def test_default_prompt_provider_renders_dynamic_prompts() -> None:
    provider = PromptProvider(profile="default")
    initializer = provider.build_initializer_prompt(app_spec="Build app", feature_target=5)
    coding = provider.build_coding_prompt(
        app_spec="Build app",
        feature_index=2,
        feature={"category": "functional", "description": "Do thing", "steps": ["a"], "passes": False},
        passing=1,
        total=5,
    )

    assert "at least 5 end-to-end test cases" in initializer
    assert "Next target feature index: 2" in coding


def test_default_prompts_require_progress_file_updates() -> None:
    provider = PromptProvider(profile="default")
    initializer = provider.build_initializer_prompt(app_spec="Build app", feature_target=3)
    coding = provider.build_coding_prompt(
        app_spec="Build app",
        feature_index=0,
        feature={"category": "functional", "description": "Do thing", "steps": ["a"], "passes": False},
        passing=0,
        total=3,
    )

    assert "claude-progress.txt" in initializer
    assert "claude-progress.txt" in coding


def test_article_prompt_provider_uses_article_assets() -> None:
    provider = PromptProvider(profile="article")
    initializer = provider.build_initializer_prompt(app_spec="ignored", feature_target=200)
    coding = provider.build_coding_prompt(
        app_spec="ignored",
        feature_index=0,
        feature={"category": "functional", "description": "ignored", "steps": ["a"], "passes": False},
        passing=0,
        total=200,
    )

    assert "INITIALIZER AGENT" in initializer
    assert "CODING AGENT" in coding
