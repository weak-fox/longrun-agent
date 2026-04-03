"""Tests for the enhanced prompt builder with bearings pre-injection."""

import os
import unittest
from unittest.mock import patch, MagicMock

from longrun_agent.prompt import (
    BearingsResult,
    PromptConfig,
    build_system_prompt,
    collect_bearings,
    format_bearings_section,
    run_bearings_command,
)


class TestRunBearingsCommand(unittest.TestCase):
    """Tests for running individual bearings commands."""

    def test_successful_command(self):
        output, success = run_bearings_command("echo hello")
        self.assertTrue(success)
        self.assertEqual(output, "hello")

    def test_failed_command(self):
        output, success = run_bearings_command("exit 1")
        self.assertFalse(success)

    def test_timeout_command(self):
        output, success = run_bearings_command("sleep 10", timeout=1)
        self.assertFalse(success)
        self.assertIn("timed out", output)

    def test_command_with_cwd(self):
        output, success = run_bearings_command("pwd", cwd="/tmp")
        self.assertTrue(success)
        self.assertIn("/tmp", output)

    def test_empty_output(self):
        output, success = run_bearings_command("true")
        self.assertTrue(success)
        self.assertEqual(output, "(no output)")


class TestCollectBearings(unittest.TestCase):
    """Tests for collecting all bearings."""

    def test_collect_with_custom_commands(self):
        config = PromptConfig(
            include_bearings=True,
            bearings_commands=[
                {"label": "echo_test", "command": "echo test_output", "timeout": 5},
                {"label": "date_test", "command": "date +%Y", "timeout": 5},
            ],
        )
        results = collect_bearings(config)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].label, "echo_test")
        self.assertEqual(results[0].output, "test_output")
        self.assertTrue(results[0].success)

    def test_collect_skips_empty_commands(self):
        config = PromptConfig(
            bearings_commands=[
                {"label": "empty", "command": ""},
                {"label": "valid", "command": "echo ok"},
            ],
        )
        results = collect_bearings(config)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].label, "valid")

    def test_collect_records_duration(self):
        config = PromptConfig(
            bearings_commands=[
                {"label": "fast", "command": "echo fast", "timeout": 5},
            ],
        )
        results = collect_bearings(config)
        self.assertEqual(len(results), 1)
        self.assertGreater(results[0].duration_ms, 0)


class TestFormatBearingsSection(unittest.TestCase):
    """Tests for formatting bearings into prompt text."""

    def test_empty_results(self):
        self.assertEqual(format_bearings_section([]), "")

    def test_formats_results(self):
        results = [
            BearingsResult(
                label="git_status",
                command="git status",
                output="nothing to commit",
                success=True,
            ),
            BearingsResult(
                label="broken",
                command="bad_cmd",
                output="command not found",
                success=False,
            ),
        ]
        text = format_bearings_section(results)
        self.assertIn("Environment Bearings", text)
        self.assertIn("git_status ✓", text)
        self.assertIn("nothing to commit", text)
        self.assertIn("broken ✗", text)


class TestBuildSystemPrompt(unittest.TestCase):
    """Tests for the main build_system_prompt function."""

    def test_basic_prompt_without_bearings(self):
        config = PromptConfig(include_bearings=False)
        prompt = build_system_prompt(config=config)
        self.assertIn("AI assistant", prompt)
        self.assertIn("Guidelines", prompt)
        self.assertIn("bash tool", prompt)
        self.assertNotIn("Environment Bearings", prompt)

    def test_prompt_with_pre_collected_bearings(self):
        results = [
            BearingsResult(
                label="test_bearing",
                command="echo test",
                output="test_output",
                success=True,
            ),
        ]
        config = PromptConfig(include_bearings=True)
        prompt = build_system_prompt(config=config, bearings_results=results)
        self.assertIn("Environment Bearings", prompt)
        self.assertIn("test_bearing", prompt)
        self.assertIn("test_output", prompt)

    def test_prompt_with_task_context(self):
        config = PromptConfig(
            include_bearings=False,
            task_context="Build a REST API with FastAPI",
        )
        prompt = build_system_prompt(config=config)
        self.assertIn("Task Context", prompt)
        self.assertIn("REST API", prompt)

    def test_prompt_with_custom_instructions(self):
        config = PromptConfig(
            include_bearings=False,
            custom_instructions="Always write tests first (TDD approach)",
        )
        prompt = build_system_prompt(config=config)
        self.assertIn("Additional Instructions", prompt)
        self.assertIn("TDD approach", prompt)

    def test_prompt_includes_cwd_and_os(self):
        config = PromptConfig(include_bearings=False, cwd="/test/dir")
        prompt = build_system_prompt(config=config)
        self.assertIn("/test/dir", prompt)

    def test_backward_compatibility_no_args(self):
        """build_system_prompt() with no args should still work."""
        prompt = build_system_prompt()
        self.assertIn("AI assistant", prompt)
        self.assertIn("Guidelines", prompt)

    def test_prompt_with_all_sections(self):
        results = [
            BearingsResult(
                label="env_check", command="echo ok", output="ok", success=True
            ),
        ]
        config = PromptConfig(
            include_bearings=True,
            task_context="Implement user authentication",
            custom_instructions="Use bcrypt for password hashing",
        )
        prompt = build_system_prompt(config=config, bearings_results=results)
        # Verify all sections are present and in order
        bearings_pos = prompt.index("Environment Bearings")
        guidelines_pos = prompt.index("Guidelines")
        task_pos = prompt.index("Task Context")
        instructions_pos = prompt.index("Additional Instructions")
        self.assertLess(bearings_pos, guidelines_pos)
        self.assertLess(guidelines_pos, task_pos)
        self.assertLess(task_pos, instructions_pos)


if __name__ == "__main__":
    unittest.main()
