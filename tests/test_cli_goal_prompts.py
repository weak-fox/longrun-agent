from longrun_agent.cli import _build_goal_expansion_prompt, _build_goal_question_prompt


def test_goal_expansion_prompt_uses_backend_specific_instruction_file_for_codex() -> None:
    prompt = _build_goal_expansion_prompt("Build a task board", backend_name="codex_cli")

    assert "`AGENTS.md`" in prompt
    assert "`claude.md`" in prompt
    assert "Layered repository reading" in prompt
    assert "Do not read the entire repository by default" in prompt


def test_goal_question_prompt_uses_backend_specific_instruction_file_for_claude() -> None:
    prompt = _build_goal_question_prompt(
        "Build a task board",
        history=[],
        backend_name="claude_sdk",
    )

    assert "`claude.md`" in prompt
    assert "`AGENTS.md`" in prompt
    assert "Layered repository reading" in prompt
