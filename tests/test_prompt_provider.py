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


def test_codex_prompt_provider_requires_agents_md_and_layered_reading() -> None:
    provider = PromptProvider(profile="default", backend_name="codex_cli")
    initializer = provider.build_initializer_prompt(app_spec="Build app", feature_target=3)

    assert "`AGENTS.md`" in initializer
    assert "`claude.md`" in initializer
    assert "Layered repository reading" in initializer
    assert "Do not read the entire repository by default" in initializer


def test_claude_prompt_provider_requires_claude_md_and_layered_reading() -> None:
    provider = PromptProvider(profile="default", backend_name="claude_sdk")
    coding = provider.build_coding_prompt(
        app_spec="Build app",
        feature_index=0,
        feature={"category": "functional", "description": "Do thing", "steps": ["a"], "passes": False},
        passing=0,
        total=1,
    )

    assert "`claude.md`" in coding
    assert "`AGENTS.md`" in coding
    assert "Layered repository reading" in coding
