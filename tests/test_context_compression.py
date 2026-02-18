"""Tests for context compression."""

import unittest
from dataclasses import dataclass

from longrun_agent.context_compression import (
    CompressionConfig,
    ContextCompressor,
    build_summary_message,
    estimate_message_tokens,
    truncate_tool_results,
)


@dataclass
class FakeToolUseBlock:
    type: str = "tool_use"
    name: str = "bash"

@dataclass
class FakeTextBlock:
    type: str = "text"
    text: str = ""


def make_messages(n: int) -> list[dict]:
    """Generate n dummy messages for testing."""
    messages = []
    for i in range(n):
        if i % 3 == 0:
            messages.append({"role": "user", "content": f"User message {i}"})
        elif i % 3 == 1:
            messages.append({"role": "assistant", "content": [FakeTextBlock(text=f"Assistant response {i}")]})
        else:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}", "content": f"Result {i}" * 10}
                ],
            })
    return messages


class TestEstimateTokens(unittest.TestCase):
    """Tests for token estimation."""

    def test_string_content(self):
        tokens = estimate_message_tokens({"role": "user", "content": "Hello world"})
        self.assertGreater(tokens, 0)
        self.assertEqual(tokens, len("Hello world") // 4)

    def test_list_content(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "content": "x" * 400}
            ],
        }
        tokens = estimate_message_tokens(msg)
        self.assertEqual(tokens, 100)

    def test_empty_content(self):
        tokens = estimate_message_tokens({"role": "user", "content": ""})
        self.assertEqual(tokens, 0)


class TestTruncateToolResults(unittest.TestCase):
    """Tests for tool result truncation."""

    def test_no_truncation_needed(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "short"}
                ],
            }
        ]
        result, count = truncate_tool_results(messages, max_length=100)
        self.assertEqual(count, 0)
        self.assertEqual(result[0]["content"][0]["content"], "short")

    def test_truncates_long_result(self):
        long_content = "x" * 5000
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": long_content}
                ],
            }
        ]
        result, count = truncate_tool_results(messages, max_length=100)
        self.assertEqual(count, 1)
        self.assertLess(len(result[0]["content"][0]["content"]), 200)
        self.assertIn("truncated", result[0]["content"][0]["content"])

    def test_non_tool_messages_unchanged(self):
        messages = [{"role": "user", "content": "Hello"}]
        result, count = truncate_tool_results(messages, max_length=10)
        self.assertEqual(count, 0)
        self.assertEqual(result[0]["content"], "Hello")


class TestBuildSummaryMessage(unittest.TestCase):
    """Tests for summary message construction."""

    def test_empty_messages(self):
        summary = build_summary_message([])
        self.assertIn("0 messages", summary)

    def test_with_user_messages(self):
        messages = [
            {"role": "user", "content": "Please implement the authentication module"},
        ]
        summary = build_summary_message(messages)
        self.assertIn("authentication", summary)

    def test_with_tool_calls(self):
        messages = [
            {"role": "assistant", "content": [FakeToolUseBlock(name="bash")]},
            {"role": "assistant", "content": [FakeToolUseBlock(name="str_replace_editor")]},
        ]
        summary = build_summary_message(messages)
        self.assertIn("bash", summary)
        self.assertIn("str_replace_editor", summary)

    def test_custom_prefix(self):
        summary = build_summary_message([], prefix="[CUSTOM]")
        self.assertIn("[CUSTOM]", summary)


class TestContextCompressor(unittest.TestCase):
    """Tests for ContextCompressor."""

    def test_should_compress_below_threshold(self):
        compressor = ContextCompressor(CompressionConfig(max_messages=50))
        messages = make_messages(30)
        self.assertFalse(compressor.should_compress(messages))

    def test_should_compress_above_threshold(self):
        compressor = ContextCompressor(CompressionConfig(max_messages=20))
        messages = make_messages(30)
        self.assertTrue(compressor.should_compress(messages))

    def test_should_compress_disabled(self):
        compressor = ContextCompressor(CompressionConfig(enabled=False))
        messages = make_messages(100)
        self.assertFalse(compressor.should_compress(messages))

    def test_compress_reduces_count(self):
        config = CompressionConfig(
            max_messages=10,
            target_messages=8,
            preserve_first_n=2,
            preserve_last_n=5,
        )
        compressor = ContextCompressor(config)
        messages = make_messages(30)
        compressed, result = compressor.compress(messages)

        self.assertLess(len(compressed), len(messages))
        self.assertEqual(result.original_count, 30)
        self.assertTrue(result.summary_added)
        self.assertGreater(result.removed_count, 0)

    def test_compress_preserves_head_and_tail(self):
        config = CompressionConfig(
            max_messages=10,
            target_messages=8,
            preserve_first_n=2,
            preserve_last_n=3,
        )
        compressor = ContextCompressor(config)
        messages = make_messages(20)
        compressed, _ = compressor.compress(messages)

        # First 2 messages should be preserved
        self.assertEqual(compressed[0], messages[0])
        self.assertEqual(compressed[1], messages[1])

        # Last 3 messages should be preserved
        self.assertEqual(compressed[-1], messages[-1])
        self.assertEqual(compressed[-2], messages[-2])
        self.assertEqual(compressed[-3], messages[-3])

    def test_compress_adds_summary(self):
        config = CompressionConfig(
            max_messages=10,
            target_messages=8,
            preserve_first_n=2,
            preserve_last_n=3,
        )
        compressor = ContextCompressor(config)
        messages = make_messages(20)
        compressed, result = compressor.compress(messages)

        # Summary message should be after the head
        summary_msg = compressed[2]
        self.assertEqual(summary_msg["role"], "user")
        self.assertIn("Context Summary", summary_msg["content"])
        self.assertTrue(result.summary_added)

    def test_no_compress_when_below_target(self):
        config = CompressionConfig(target_messages=50)
        compressor = ContextCompressor(config)
        messages = make_messages(10)
        compressed, result = compressor.compress(messages)

        self.assertEqual(len(compressed), 10)
        self.assertFalse(result.summary_added)
        self.assertEqual(result.removed_count, 0)

    def test_compression_count_tracks(self):
        config = CompressionConfig(max_messages=5, target_messages=3, preserve_first_n=1, preserve_last_n=1)
        compressor = ContextCompressor(config)
        messages = make_messages(10)

        compressor.compress(messages)
        self.assertEqual(compressor.compression_count, 1)

        compressor.compress(messages)
        self.assertEqual(compressor.compression_count, 2)


if __name__ == "__main__":
    unittest.main()
