"""Tests for the tool-level Gate system."""

import os
import tempfile
import unittest

from longrun_agent.gates.tool_gates import (
    BashExitCodeGate,
    FileExistsGate,
    GateSeverity,
    ToolGateCheck,
    ToolGateEngine,
    ToolGateResult,
    SyntaxCheckGate,
    get_default_tool_gates,
)


class TestToolGateResult(unittest.TestCase):
    """Tests for ToolGateResult dataclass."""

    def test_passed_result(self):
        r = ToolGateResult(gate_id="test", passed=True, message="OK")
        self.assertTrue(r.passed)
        self.assertFalse(r.is_blocking_failure)

    def test_blocking_failure(self):
        r = ToolGateResult(
            gate_id="test",
            passed=False,
            severity=GateSeverity.BLOCKING,
            message="Syntax error",
        )
        self.assertTrue(r.is_blocking_failure)

    def test_warning_failure_not_blocking(self):
        r = ToolGateResult(
            gate_id="test",
            passed=False,
            severity=GateSeverity.WARNING,
            message="Minor issue",
        )
        self.assertFalse(r.is_blocking_failure)


class TestSyntaxCheckGate(unittest.TestCase):
    """Tests for SyntaxCheckGate."""

    def setUp(self):
        self.gate = SyntaxCheckGate()
        self.tmpdir = tempfile.mkdtemp()

    def test_skips_non_editor_tools(self):
        result = self.gate.check({"tool_name": "bash", "tool_input": {}})
        self.assertTrue(result.passed)

    def test_skips_non_write_operations(self):
        result = self.gate.check({
            "tool_name": "str_replace_editor",
            "tool_input": {"command": "view", "path": "/tmp/test.py"},
        })
        self.assertTrue(result.passed)

    def test_skips_non_python_files(self):
        result = self.gate.check({
            "tool_name": "str_replace_editor",
            "tool_input": {"command": "create", "path": "/tmp/test.txt"},
        })
        self.assertTrue(result.passed)

    def test_detects_valid_python(self):
        path = os.path.join(self.tmpdir, "valid.py")
        with open(path, "w") as f:
            f.write("def hello():\n    return 'world'\n")

        result = self.gate.check({
            "tool_name": "str_replace_editor",
            "tool_input": {"command": "create", "path": path},
        })
        self.assertTrue(result.passed)
        self.assertIn("Syntax OK", result.message)

    def test_detects_syntax_error(self):
        path = os.path.join(self.tmpdir, "broken.py")
        with open(path, "w") as f:
            f.write("def hello(\n    return 'world'\n")  # Missing closing paren

        result = self.gate.check({
            "tool_name": "str_replace_editor",
            "tool_input": {"command": "create", "path": path},
        })
        self.assertFalse(result.passed)
        self.assertEqual(result.severity, GateSeverity.BLOCKING)

    def test_handles_missing_file(self):
        result = self.gate.check({
            "tool_name": "str_replace_editor",
            "tool_input": {"command": "create", "path": "/tmp/nonexistent_xyz.py"},
        })
        self.assertFalse(result.passed)


class TestFileExistsGate(unittest.TestCase):
    """Tests for FileExistsGate."""

    def setUp(self):
        self.gate = FileExistsGate()

    def test_skips_non_editor_tools(self):
        result = self.gate.check({"tool_name": "bash", "tool_input": {}})
        self.assertTrue(result.passed)

    def test_detects_file_not_found_error(self):
        result = self.gate.check({
            "tool_name": "str_replace_editor",
            "tool_input": {"command": "view", "path": "/tmp/missing.py"},
            "tool_result": "Error viewing /tmp/missing.py: File not found",
        })
        self.assertFalse(result.passed)
        self.assertTrue(len(result.remediation) > 0)

    def test_passes_for_successful_operation(self):
        result = self.gate.check({
            "tool_name": "str_replace_editor",
            "tool_input": {"command": "view", "path": "/tmp/test.py"},
            "tool_result": "     1\tprint('hello')",
        })
        self.assertTrue(result.passed)


class TestBashExitCodeGate(unittest.TestCase):
    """Tests for BashExitCodeGate."""

    def setUp(self):
        self.gate = BashExitCodeGate()

    def test_skips_non_bash_tools(self):
        result = self.gate.check({"tool_name": "str_replace_editor", "tool_input": {}})
        self.assertTrue(result.passed)

    def test_detects_non_zero_exit(self):
        result = self.gate.check({
            "tool_name": "bash",
            "tool_input": {"command": "false"},
            "tool_result": "Some output\n[Exit code: 1]",
        })
        self.assertFalse(result.passed)

    def test_detects_error_prefix(self):
        result = self.gate.check({
            "tool_name": "bash",
            "tool_input": {"command": "bad_cmd"},
            "tool_result": "Error: Command timed out after 300 seconds",
        })
        self.assertFalse(result.passed)

    def test_passes_for_successful_command(self):
        result = self.gate.check({
            "tool_name": "bash",
            "tool_input": {"command": "echo hello"},
            "tool_result": "hello",
        })
        self.assertTrue(result.passed)


class TestToolGateEngine(unittest.TestCase):
    """Tests for ToolGateEngine orchestration."""

    def test_default_gates(self):
        gates = get_default_tool_gates()
        self.assertEqual(len(gates), 3)
        gate_ids = {g.gate_id for g in gates}
        self.assertIn("syntax_check", gate_ids)
        self.assertIn("file_exists", gate_ids)
        self.assertIn("bash_exit_code", gate_ids)

    def test_engine_runs_all_gates(self):
        engine = ToolGateEngine()
        results = engine.run_gates({
            "tool_name": "bash",
            "tool_input": {"command": "echo ok"},
            "tool_result": "ok",
        })
        self.assertEqual(len(results), 3)

    def test_engine_disabled(self):
        engine = ToolGateEngine(enabled=False)
        results = engine.run_gates({"tool_name": "bash"})
        self.assertEqual(len(results), 0)

    def test_has_blocking_failures(self):
        results = [
            ToolGateResult(gate_id="a", passed=True),
            ToolGateResult(gate_id="b", passed=False, severity=GateSeverity.BLOCKING),
        ]
        self.assertTrue(ToolGateEngine.has_blocking_failures(results))

    def test_no_blocking_failures(self):
        results = [
            ToolGateResult(gate_id="a", passed=True),
            ToolGateResult(gate_id="b", passed=False, severity=GateSeverity.WARNING),
        ]
        self.assertFalse(ToolGateEngine.has_blocking_failures(results))

    def test_get_failures(self):
        results = [
            ToolGateResult(gate_id="a", passed=True),
            ToolGateResult(gate_id="b", passed=False),
            ToolGateResult(gate_id="c", passed=False),
        ]
        failures = ToolGateEngine.get_failures(results)
        self.assertEqual(len(failures), 2)

    def test_format_failures_for_prompt(self):
        failures = [
            ToolGateResult(
                gate_id="syntax_check",
                passed=False,
                message="Syntax error in test.py",
                remediation=["Fix the missing colon on line 5"],
            ),
        ]
        text = ToolGateEngine.format_failures_for_prompt(failures)
        self.assertIn("syntax_check", text)
        self.assertIn("Fix the missing colon", text)

    def test_format_failures_empty(self):
        self.assertEqual(ToolGateEngine.format_failures_for_prompt([]), "")

    def test_history_tracking(self):
        engine = ToolGateEngine()
        engine.run_gates({"tool_name": "bash", "tool_input": {}, "tool_result": "ok"})
        engine.run_gates({"tool_name": "bash", "tool_input": {}, "tool_result": "ok"})
        self.assertEqual(len(engine.history), 2)

    def test_clear_history(self):
        engine = ToolGateEngine()
        engine.run_gates({"tool_name": "bash", "tool_input": {}, "tool_result": "ok"})
        engine.clear_history()
        self.assertEqual(len(engine.history), 0)

    def test_engine_handles_gate_exception(self):
        """Engine should gracefully handle a gate that raises."""

        class BrokenGate(ToolGateCheck):
            @property
            def gate_id(self):
                return "broken"

            def check(self, context):
                raise RuntimeError("Gate crashed")

        engine = ToolGateEngine(gates=[BrokenGate()])
        results = engine.run_gates({"tool_name": "bash"})
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].passed)
        self.assertIn("exception", results[0].message)

    def test_custom_gates(self):
        """Engine should accept custom gate implementations."""

        class AlwaysPassGate(ToolGateCheck):
            @property
            def gate_id(self):
                return "always_pass"

            def check(self, context):
                return ToolGateResult(gate_id=self.gate_id, passed=True)

        engine = ToolGateEngine(gates=[AlwaysPassGate()])
        results = engine.run_gates({"tool_name": "bash"})
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].passed)


if __name__ == "__main__":
    unittest.main()
