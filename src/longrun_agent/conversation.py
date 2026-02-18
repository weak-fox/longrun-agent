"""Conversation management for the agent loop.

This module extracts conversation-related logic from agent.py into a standalone,
testable component. It handles:
- Message history management (add/get/clear)
- Tool call detection and text extraction from API responses
- Conversation statistics (turn count, tool call count)
- Serialization for checkpointing
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Represents a single tool call from the assistant."""

    tool_use_id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    """Represents the result of a tool execution."""

    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class ConversationStats:
    """Accumulated statistics for a conversation run."""

    total_turns: int = 0
    tool_calls: int = 0
    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def record_api_call(self, usage: Optional[Any] = None) -> None:
        """Record an API call and its token usage.

        Args:
            usage: The usage object from the API response (has input_tokens, output_tokens).
        """
        self.api_calls += 1
        if usage:
            self.input_tokens += getattr(usage, "input_tokens", 0)
            self.output_tokens += getattr(usage, "output_tokens", 0)

    def record_tool_call(self) -> None:
        """Record a tool call."""
        self.tool_calls += 1

    def record_turn(self) -> None:
        """Record a conversation turn."""
        self.total_turns += 1


class ConversationManager:
    """Manages conversation message history and provides utility methods.

    This class is responsible for:
    - Maintaining the ordered list of messages
    - Extracting tool calls from assistant responses
    - Extracting text from assistant responses
    - Building tool result messages
    - Tracking conversation statistics
    - Serializing/deserializing for checkpointing

    Usage:
        conv = ConversationManager()
        conv.add_user_message("Hello")
        conv.add_assistant_message(response.content)
        tool_calls = conv.extract_tool_calls(response.content)
        conv.add_tool_results(results)
    """

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.stats = ConversationStats()

    def add_user_message(self, content: str) -> None:
        """Add a user message to the conversation.

        Args:
            content: The text content of the user message.
        """
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: Any) -> None:
        """Add an assistant response to the conversation.

        Args:
            content: The content blocks from the API response.
        """
        self.messages.append({"role": "assistant", "content": content})
        self.stats.record_turn()

    def add_tool_results(self, results: list[dict[str, Any]]) -> None:
        """Add tool results as a user message.

        Args:
            results: List of tool_result dicts with type, tool_use_id, content.
        """
        if results:
            self.messages.append({"role": "user", "content": results})

    @staticmethod
    def has_tool_use(content: Any) -> bool:
        """Check if assistant response content contains tool_use blocks.

        Args:
            content: The content blocks from the API response.

        Returns:
            True if any block has type "tool_use".
        """
        if not content:
            return False
        for block in content:
            if hasattr(block, "type") and block.type == "tool_use":
                return True
        return False

    @staticmethod
    def extract_tool_calls(content: Any) -> list[ToolCall]:
        """Extract tool calls from assistant response content.

        Args:
            content: The content blocks from the API response.

        Returns:
            List of ToolCall objects found in the response.
        """
        calls = []
        if not content:
            return calls
        for block in content:
            if hasattr(block, "type") and block.type == "tool_use":
                calls.append(
                    ToolCall(
                        tool_use_id=block.id,
                        name=block.name,
                        input=block.input,
                    )
                )
        return calls

    @staticmethod
    def extract_text(content: Any) -> str:
        """Extract text from assistant response content blocks.

        Args:
            content: The content blocks from the API response.

        Returns:
            Concatenated text from all text blocks, separated by newlines.
        """
        texts = []
        if not content:
            return ""
        for block in content:
            if hasattr(block, "text"):
                texts.append(block.text)
        return "\n".join(texts) if texts else ""

    @staticmethod
    def build_tool_result(tool_use_id: str, result: str, is_error: bool = False) -> dict[str, Any]:
        """Build a tool_result dict for the API.

        Args:
            tool_use_id: The ID of the tool_use block this result corresponds to.
            result: The string result of the tool execution.
            is_error: Whether this result represents an error.

        Returns:
            Dict formatted as an API tool_result message.
        """
        entry: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": str(result),
        }
        if is_error:
            entry["is_error"] = True
        return entry

    def get_messages(self) -> list[dict[str, Any]]:
        """Get a copy of all conversation messages.

        Returns:
            A copy of the message list.
        """
        return list(self.messages)

    def clear(self) -> None:
        """Clear all messages and reset statistics."""
        self.messages = []
        self.stats = ConversationStats()

    @property
    def turn_count(self) -> int:
        """Number of complete turns (assistant responses)."""
        return self.stats.total_turns

    def to_dict(self) -> dict[str, Any]:
        """Serialize conversation state to a dict for checkpointing.

        Returns:
            Dict containing messages and stats, suitable for JSON serialization.
        """
        # We need to handle content blocks that aren't plain dicts
        serializable_messages = []
        for msg in self.messages:
            content = msg["content"]
            if isinstance(content, (str, list)):
                serializable_messages.append(msg)
            else:
                # Try to convert content blocks to dicts
                try:
                    serialized_content = []
                    for block in content:
                        if hasattr(block, "model_dump"):
                            serialized_content.append(block.model_dump())
                        elif hasattr(block, "__dict__"):
                            serialized_content.append(block.__dict__)
                        else:
                            serialized_content.append(str(block))
                    serializable_messages.append(
                        {"role": msg["role"], "content": serialized_content}
                    )
                except (TypeError, AttributeError):
                    serializable_messages.append(
                        {"role": msg["role"], "content": str(content)}
                    )

        return {
            "messages": serializable_messages,
            "stats": {
                "total_turns": self.stats.total_turns,
                "tool_calls": self.stats.tool_calls,
                "api_calls": self.stats.api_calls,
                "input_tokens": self.stats.input_tokens,
                "output_tokens": self.stats.output_tokens,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationManager":
        """Restore conversation state from a serialized dict.

        Args:
            data: Dict as produced by to_dict().

        Returns:
            A new ConversationManager with restored state.
        """
        conv = cls()
        conv.messages = data.get("messages", [])
        stats_data = data.get("stats", {})
        conv.stats = ConversationStats(
            total_turns=stats_data.get("total_turns", 0),
            tool_calls=stats_data.get("tool_calls", 0),
            api_calls=stats_data.get("api_calls", 0),
            input_tokens=stats_data.get("input_tokens", 0),
            output_tokens=stats_data.get("output_tokens", 0),
        )
        return conv
