import asyncio
from pathlib import Path

import pytest

from longrun_agent.article.prompts import copy_spec_to_project, get_coding_prompt, get_initializer_prompt
from longrun_agent.article.security import bash_security_hook, extract_commands
from longrun_agent.cli import build_parser


def test_article_prompts_load() -> None:
    initializer = get_initializer_prompt()
    coding = get_coding_prompt()

    assert "INITIALIZER AGENT" in initializer
    assert "CODING AGENT" in coding


def test_copy_spec_to_project_creates_app_spec(tmp_path: Path) -> None:
    copy_spec_to_project(tmp_path)

    spec_file = tmp_path / "app_spec.txt"
    assert spec_file.exists()
    assert "project_specification" in spec_file.read_text()


def test_extract_commands_handles_chains() -> None:
    assert extract_commands("npm install && npm run build") == ["npm", "npm"]


def test_bash_security_hook_blocks_disallowed_command() -> None:
    result = asyncio.run(
        bash_security_hook({"tool_name": "Bash", "tool_input": {"command": "curl example.com"}})
    )

    assert result.get("decision") == "block"


def test_bash_security_hook_allows_safe_command() -> None:
    result = asyncio.run(
        bash_security_hook({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
    )

    assert result == {}


def test_cli_parser_rejects_removed_article_run_command() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["article-run"])
