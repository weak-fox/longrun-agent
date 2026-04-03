"""Tests for the Repair mechanism."""

import unittest

from longrun_agent.repair import (
    RepairAttempt,
    RepairEngine,
    RepairLevel,
    RepairPolicy,
)


class TestRepairPolicy(unittest.TestCase):
    """Tests for RepairPolicy configuration."""

    def test_defaults(self):
        policy = RepairPolicy()
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.max_repairs_per_session, 10)
        self.assertEqual(policy.max_consecutive_repairs, 3)
        self.assertEqual(policy.escalation_threshold, 2)


class TestRepairEngine(unittest.TestCase):
    """Tests for RepairEngine behavior."""

    def test_can_repair_when_enabled(self):
        engine = RepairEngine()
        self.assertTrue(engine.can_repair)

    def test_cannot_repair_when_disabled(self):
        engine = RepairEngine(policy=RepairPolicy(enabled=False))
        self.assertFalse(engine.can_repair)

    def test_build_repair_message_hint(self):
        engine = RepairEngine()
        msg = engine.build_repair_message(
            trigger="syntax_check",
            error_detail="SyntaxError at line 10",
            iteration=1,
        )
        self.assertIsNotNone(msg)
        self.assertIn("SyntaxError", msg)
        self.assertEqual(engine.total_repairs, 1)
        self.assertEqual(engine.attempts[0].level, RepairLevel.HINT)

    def test_build_repair_with_file_context(self):
        engine = RepairEngine()
        msg = engine.build_repair_message(
            trigger="syntax_check",
            error_detail="IndentationError",
            context={"file": "main.py"},
        )
        self.assertIn("main.py", msg)

    def test_escalation_to_guided(self):
        """After escalation_threshold repairs for same trigger, level goes to GUIDED."""
        engine = RepairEngine(policy=RepairPolicy(escalation_threshold=2))

        # First 2 repairs should be HINT
        engine.build_repair_message(trigger="syntax", error_detail="err1")
        engine.build_repair_message(trigger="syntax", error_detail="err2")
        self.assertEqual(engine.attempts[-1].level, RepairLevel.HINT)

        # Third should be GUIDED (count >= threshold)
        engine.build_repair_message(trigger="syntax", error_detail="err3")
        self.assertEqual(engine.attempts[-1].level, RepairLevel.GUIDED)

    def test_escalation_to_rollback(self):
        """After 2*threshold repairs for same trigger, level goes to ROLLBACK."""
        engine = RepairEngine(policy=RepairPolicy(escalation_threshold=1))

        engine.build_repair_message(trigger="err", error_detail="fail1")
        self.assertEqual(engine.attempts[-1].level, RepairLevel.HINT)

        engine.build_repair_message(trigger="err", error_detail="fail2")
        self.assertEqual(engine.attempts[-1].level, RepairLevel.GUIDED)

        engine.build_repair_message(trigger="err", error_detail="fail3")
        self.assertEqual(engine.attempts[-1].level, RepairLevel.ROLLBACK)

    def test_max_repairs_per_session(self):
        engine = RepairEngine(policy=RepairPolicy(max_repairs_per_session=2))

        engine.build_repair_message(trigger="a", error_detail="e1")
        engine.build_repair_message(trigger="b", error_detail="e2")

        msg = engine.build_repair_message(trigger="c", error_detail="e3")
        self.assertIsNone(msg)
        self.assertFalse(engine.can_repair)

    def test_max_consecutive_repairs(self):
        engine = RepairEngine(
            policy=RepairPolicy(max_consecutive_repairs=2, max_repairs_per_session=10)
        )

        engine.build_repair_message(trigger="a", error_detail="e1")
        engine.build_repair_message(trigger="a", error_detail="e2")

        msg = engine.build_repair_message(trigger="a", error_detail="e3")
        self.assertIsNone(msg)

    def test_mark_resolved_resets_consecutive(self):
        engine = RepairEngine(
            policy=RepairPolicy(max_consecutive_repairs=2, max_repairs_per_session=10)
        )

        engine.build_repair_message(trigger="a", error_detail="e1")
        engine.build_repair_message(trigger="a", error_detail="e2")
        self.assertFalse(engine.can_repair)  # Consecutive limit hit

        engine.mark_resolved()
        self.assertTrue(engine.can_repair)  # Reset after resolution

    def test_get_summary(self):
        engine = RepairEngine()
        engine.build_repair_message(trigger="syntax", error_detail="e1", iteration=1)
        engine.build_repair_message(trigger="bash", error_detail="e2", iteration=2)
        engine.mark_resolved()

        summary = engine.get_summary()
        self.assertEqual(summary["total_repairs"], 2)
        self.assertEqual(summary["resolved_count"], 1)
        self.assertEqual(summary["repairs_by_trigger"]["syntax"], 1)
        self.assertEqual(summary["repairs_by_trigger"]["bash"], 1)

    def test_different_triggers_dont_affect_escalation(self):
        """Escalation is per-trigger, not global."""
        engine = RepairEngine(policy=RepairPolicy(escalation_threshold=2))

        engine.build_repair_message(trigger="a", error_detail="e1")
        engine.build_repair_message(trigger="a", error_detail="e2")
        engine.build_repair_message(trigger="b", error_detail="e3")  # Different trigger

        # "b" should be HINT (first time for this trigger)
        self.assertEqual(engine.attempts[-1].level, RepairLevel.HINT)

    def test_rollback_message_content(self):
        engine = RepairEngine(policy=RepairPolicy(escalation_threshold=1))
        engine.build_repair_message(trigger="err", error_detail="f1")
        engine.build_repair_message(trigger="err", error_detail="f2")
        msg = engine.build_repair_message(trigger="err", error_detail="f3")

        self.assertIn("STOP", msg)
        self.assertIn("different approach", msg)


if __name__ == "__main__":
    unittest.main()
