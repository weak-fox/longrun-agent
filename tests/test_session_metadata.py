"""Tests for SessionMetadata and SessionTracker."""

import time
import unittest
from unittest.mock import MagicMock

from longrun_agent.session_metadata import (
    SessionMetadata,
    SessionTracker,
    ToolCallRecord,
)


class TestSessionMetadata(unittest.TestCase):
    """Tests for SessionMetadata dataclass."""

    def test_default_values(self):
        meta = SessionMetadata()
        self.assertEqual(meta.iterations, 0)
        self.assertEqual(meta.total_tokens, 0)
        self.assertEqual(meta.tool_call_count, 0)
        self.assertAlmostEqual(meta.tool_success_rate, 1.0)

    def test_duration_seconds(self):
        meta = SessionMetadata(start_time=100.0, end_time=105.5)
        self.assertAlmostEqual(meta.duration_seconds, 5.5)

    def test_duration_seconds_not_finished(self):
        meta = SessionMetadata(start_time=100.0)
        self.assertAlmostEqual(meta.duration_seconds, 0.0)

    def test_total_tokens(self):
        meta = SessionMetadata(total_input_tokens=500, total_output_tokens=200)
        self.assertEqual(meta.total_tokens, 700)

    def test_tool_success_rate(self):
        meta = SessionMetadata(
            tool_calls=[
                ToolCallRecord(tool_name="bash", duration_ms=100, success=True),
                ToolCallRecord(tool_name="bash", duration_ms=50, success=False),
                ToolCallRecord(tool_name="edit", duration_ms=80, success=True),
            ]
        )
        self.assertAlmostEqual(meta.tool_success_rate, 2.0 / 3.0)

    def test_tool_counts_by_name(self):
        meta = SessionMetadata(
            tool_calls=[
                ToolCallRecord(tool_name="bash", duration_ms=100, success=True),
                ToolCallRecord(tool_name="bash", duration_ms=50, success=True),
                ToolCallRecord(tool_name="edit", duration_ms=80, success=True),
            ]
        )
        counts = meta.tool_counts_by_name()
        self.assertEqual(counts, {"bash": 2, "edit": 1})

    def test_to_dict(self):
        meta = SessionMetadata(
            session_id="test-001",
            start_time=100.0,
            end_time=110.0,
            iterations=5,
            stop_reason="end_turn",
            total_input_tokens=1000,
            total_output_tokens=500,
            api_calls=5,
        )
        d = meta.to_dict()
        self.assertEqual(d["session_id"], "test-001")
        self.assertAlmostEqual(d["duration_seconds"], 10.0)
        self.assertEqual(d["total_tokens"], 1500)
        self.assertEqual(d["stop_reason"], "end_turn")


class TestSessionTracker(unittest.TestCase):
    """Tests for SessionTracker lifecycle."""

    def test_start_and_finish(self):
        tracker = SessionTracker(session_id="s1")
        tracker.start()
        self.assertTrue(tracker.is_running)

        time.sleep(0.01)  # Small delay to ensure non-zero duration
        tracker.finish(stop_reason="end_turn")
        self.assertFalse(tracker.is_running)
        self.assertGreater(tracker.metadata.duration_seconds, 0)
        self.assertEqual(tracker.metadata.stop_reason, "end_turn")
        self.assertNotEqual(tracker.metadata.wall_start, "")
        self.assertNotEqual(tracker.metadata.wall_end, "")

    def test_record_iteration(self):
        tracker = SessionTracker()
        tracker.start()
        tracker.record_iteration()
        tracker.record_iteration()
        self.assertEqual(tracker.metadata.iterations, 2)

    def test_record_api_call_with_usage(self):
        tracker = SessionTracker()
        tracker.start()
        usage = MagicMock()
        usage.input_tokens = 100
        usage.output_tokens = 50
        tracker.record_api_call(usage)
        self.assertEqual(tracker.metadata.api_calls, 1)
        self.assertEqual(tracker.metadata.total_input_tokens, 100)
        self.assertEqual(tracker.metadata.total_output_tokens, 50)

    def test_record_api_call_no_usage(self):
        tracker = SessionTracker()
        tracker.start()
        tracker.record_api_call()
        self.assertEqual(tracker.metadata.api_calls, 1)
        self.assertEqual(tracker.metadata.total_input_tokens, 0)

    def test_record_tool_call(self):
        tracker = SessionTracker()
        tracker.start()
        tracker.record_tool_call("bash", 150.0, True)
        tracker.record_tool_call("edit", 80.0, False, error="file not found")
        self.assertEqual(tracker.metadata.tool_call_count, 2)
        self.assertEqual(tracker.metadata.tool_calls[1].error, "file not found")

    def test_record_gate_failure(self):
        tracker = SessionTracker()
        tracker.start()
        tracker.record_gate_failure()
        tracker.record_gate_failure()
        self.assertEqual(tracker.metadata.gate_failures, 2)

    def test_record_error(self):
        tracker = SessionTracker()
        tracker.start()
        tracker.record_error("Something went wrong")
        self.assertEqual(tracker.metadata.errors, ["Something went wrong"])


if __name__ == "__main__":
    unittest.main()
