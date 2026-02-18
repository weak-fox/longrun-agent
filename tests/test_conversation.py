"""Tests for the ConversationManager."""

import unittest
from unittest.mock import MagicMock
from dataclasses import dataclass
from typing import Any

from longrun_agent.conversation import (
    ConversationManager,
    ConversationStats,
    ToolCall,
)


@dataclass
class FakeTextBlock:
    """Simulates an API text content block."""

    type: str = "text"
    text: str = ""


@dataclass
class FakeToolUseBlock:
    """Simulates an API tool_use content block."""

    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = None

    def __post_init__(self):
        if self.input is None:
            self.input = {}


class TestConversationManager(unittest.TestCase):
    """Tests for ConversationManager message handling."""

    def test_add_user_message(self):
        conv = ConversationManager()
        conv.add_user_message("Hello")
        self.assertEqual(len(conv.messages), 1)
        self.assertEqual(conv.messages[0]["role"], "user")
        self.assertEqual(conv.messages[0]["content"], "Hello")

    def test_add_assistant_message_increments_turn(self):
        conv = ConversationManager()
        conv.add_assistant_message([FakeTextBlock(text="Hi")])
        self.assertEqual(conv.turn_count, 1)
        self.assertEqual(len(conv.messages), 1)
        self.assertEqual(conv.messages[0]["role"], "assistant")

    def test_add_tool_results(self):
        conv = ConversationManager()
        results = [
            {"type": "tool_result", "tool_use_id": "t1", "content": "output"},
        ]
        conv.add_tool_results(results)
        self.assertEqual(len(conv.messages), 1)
        self.assertEqual(conv.messages[0]["role"], "user")
        self.assertEqual(conv.messages[0]["content"], results)

    def test_add_empty_tool_results_no_message(self):
        conv = ConversationManager()
        conv.add_tool_results([])
        self.assertEqual(len(conv.messages), 0)

    def test_get_messages_returns_copy(self):
        conv = ConversationManager()
        conv.add_user_message("Hello")
        msgs = conv.get_messages()
        msgs.append({"role": "user", "content": "extra"})
        self.assertEqual(len(conv.messages), 1)  # Original unchanged

    def test_clear_resets_everything(self):
        conv = ConversationManager()
        conv.add_user_message("Hello")
        conv.add_assistant_message([FakeTextBlock(text="Hi")])
        conv.stats.record_tool_call()
        conv.clear()
        self.assertEqual(len(conv.messages), 0)
        self.assertEqual(conv.turn_count, 0)
        self.assertEqual(conv.stats.tool_calls, 0)


class TestToolCallExtraction(unittest.TestCase):
    """Tests for extracting tool calls from API responses."""

    def test_has_tool_use_true(self):
        content = [
            FakeTextBlock(text="Let me help"),
            FakeToolUseBlock(id="t1", name="bash", input={"command": "ls"}),
        ]
        self.assertTrue(ConversationManager.has_tool_use(content))

    def test_has_tool_use_false_text_only(self):
        content = [FakeTextBlock(text="Just text")]
        self.assertFalse(ConversationManager.has_tool_use(content))

    def test_has_tool_use_false_empty(self):
        self.assertFalse(ConversationManager.has_tool_use([]))
        self.assertFalse(ConversationManager.has_tool_use(None))

    def test_extract_tool_calls(self):
        content = [
            FakeTextBlock(text="Running command"),
            FakeToolUseBlock(id="t1", name="bash", input={"command": "ls"}),
            FakeToolUseBlock(id="t2", name="str_replace_editor", input={"command": "view", "path": "/tmp"}),
        ]
        calls = ConversationManager.extract_tool_calls(content)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].name, "bash")
        self.assertEqual(calls[0].tool_use_id, "t1")
        self.assertEqual(calls[0].input, {"command": "ls"})
        self.assertEqual(calls[1].name, "str_replace_editor")

    def test_extract_tool_calls_empty(self):
        self.assertEqual(ConversationManager.extract_tool_calls(None), [])
        self.assertEqual(ConversationManager.extract_tool_calls([]), [])

    def test_extract_text(self):
        content = [
            FakeTextBlock(text="Hello"),
            FakeToolUseBlock(id="t1", name="bash", input={}),
            FakeTextBlock(text="World"),
        ]
        text = ConversationManager.extract_text(content)
        self.assertEqual(text, "Hello\nWorld")

    def test_extract_text_empty(self):
        self.assertEqual(ConversationManager.extract_text(None), "")
        self.assertEqual(ConversationManager.extract_text([]), "")

    def test_extract_text_no_text_blocks(self):
        content = [FakeToolUseBlock(id="t1", name="bash", input={})]
        self.assertEqual(ConversationManager.extract_text(content), "")


class TestBuildToolResult(unittest.TestCase):
    """Tests for building tool result messages."""

    def test_build_normal_result(self):
        result = ConversationManager.build_tool_result("t1", "output text")
        self.assertEqual(result["type"], "tool_result")
        self.assertEqual(result["tool_use_id"], "t1")
        self.assertEqual(result["content"], "output text")
        self.assertNotIn("is_error", result)

    def test_build_error_result(self):
        result = ConversationManager.build_tool_result("t1", "failed", is_error=True)
        self.assertEqual(result["type"], "tool_result")
        self.assertEqual(result["tool_use_id"], "t1")
        self.assertTrue(result["is_error"])


class TestConversationStats(unittest.TestCase):
    """Tests for ConversationStats tracking."""

    def test_initial_stats(self):
        stats = ConversationStats()
        self.assertEqual(stats.total_turns, 0)
        self.assertEqual(stats.tool_calls, 0)
        self.assertEqual(stats.api_calls, 0)
        self.assertEqual(stats.input_tokens, 0)
        self.assertEqual(stats.output_tokens, 0)

    def test_record_api_call_with_usage(self):
        stats = ConversationStats()
        usage = MagicMock()
        usage.input_tokens = 100
        usage.output_tokens = 50
        stats.record_api_call(usage)
        self.assertEqual(stats.api_calls, 1)
        self.assertEqual(stats.input_tokens, 100)
        self.assertEqual(stats.output_tokens, 50)

    def test_record_api_call_without_usage(self):
        stats = ConversationStats()
        stats.record_api_call(None)
        self.assertEqual(stats.api_calls, 1)
        self.assertEqual(stats.input_tokens, 0)

    def test_record_tool_call(self):
        stats = ConversationStats()
        stats.record_tool_call()
        stats.record_tool_call()
        self.assertEqual(stats.tool_calls, 2)

    def test_cumulative_token_tracking(self):
        stats = ConversationStats()
        for i in range(3):
            usage = MagicMock()
            usage.input_tokens = 100
            usage.output_tokens = 50
            stats.record_api_call(usage)
        self.assertEqual(stats.api_calls, 3)
        self.assertEqual(stats.input_tokens, 300)
        self.assertEqual(stats.output_tokens, 150)


class TestConversationSerialization(unittest.TestCase):
    """Tests for conversation checkpoint serialization."""

    def test_round_trip_serialization(self):
        conv = ConversationManager()
        conv.add_user_message("Hello")
        conv.stats.record_api_call(None)
        conv.stats.record_tool_call()

        data = conv.to_dict()
        restored = ConversationManager.from_dict(data)

        self.assertEqual(len(restored.messages), 1)
        self.assertEqual(restored.messages[0]["content"], "Hello")
        self.assertEqual(restored.stats.api_calls, 1)
        self.assertEqual(restored.stats.tool_calls, 1)

    def test_from_dict_empty(self):
        conv = ConversationManager.from_dict({})
        self.assertEqual(len(conv.messages), 0)
        self.assertEqual(conv.stats.total_turns, 0)

    def test_serialization_with_plain_messages(self):
        conv = ConversationManager()
        conv.add_user_message("Hi")
        conv.add_tool_results([
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
        ])
        data = conv.to_dict()
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual(data["messages"][0]["content"], "Hi")


if __name__ == "__main__":
    unittest.main()
