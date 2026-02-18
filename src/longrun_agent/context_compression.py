"""Context compression for managing long conversations.

When conversation history grows too large, this module provides strategies
to compress it while preserving essential context:
- Sliding window: Keep only the N most recent messages
- Summarization: Replace older messages with a summary
- Tool result truncation: Shorten verbose tool outputs
- Selective pruning: Remove less important messages
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class CompressionConfig:
    """Configuration for context compression.

    Attributes:
        enabled: Whether compression is enabled.
        max_messages: Trigger compression when message count exceeds this.
        target_messages: After compression, aim for this many messages.
        max_tool_result_length: Truncate tool results longer than this.
        preserve_first_n: Always keep the first N messages (task context).
        preserve_last_n: Always keep the last N messages (recent context).
        summary_prefix: Prefix for summary messages.
    """

    enabled: bool = True
    max_messages: int = 50
    target_messages: int = 25
    max_tool_result_length: int = 2000
    preserve_first_n: int = 2  # Keep initial user message + first assistant response
    preserve_last_n: int = 10  # Keep recent conversation
    summary_prefix: str = "[Context Summary]"


@dataclass
class CompressionResult:
    """Result of a compression operation."""

    original_count: int
    compressed_count: int
    removed_count: int
    summary_added: bool
    truncated_results: int


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Rough estimate of token count for a message.

    Uses a simple heuristic of ~4 characters per token.

    Args:
        message: A conversation message dict.

    Returns:
        Estimated token count.
    """
    content = message.get("content", "")
    if isinstance(content, str):
        return len(content) // 4
    elif isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict):
                total += len(str(item.get("content", ""))) // 4
            elif isinstance(item, str):
                total += len(item) // 4
            else:
                total += len(str(item)) // 4
        return total
    else:
        return len(str(content)) // 4


def truncate_tool_results(
    messages: list[dict[str, Any]],
    max_length: int = 2000,
) -> tuple[list[dict[str, Any]], int]:
    """Truncate verbose tool results in messages.

    Args:
        messages: List of conversation messages.
        max_length: Maximum length for tool result content.

    Returns:
        Tuple of (modified messages, count of truncated results).
    """
    truncated_count = 0
    result = []

    for msg in messages:
        content = msg.get("content", "")

        if isinstance(content, list):
            new_content = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    item_content = item.get("content", "")
                    if isinstance(item_content, str) and len(item_content) > max_length:
                        truncated = item_content[:max_length] + f"\n... [truncated, was {len(item_content)} chars]"
                        new_content.append({**item, "content": truncated})
                        truncated_count += 1
                    else:
                        new_content.append(item)
                else:
                    new_content.append(item)
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)

    return result, truncated_count


def build_summary_message(
    messages: list[dict[str, Any]],
    prefix: str = "[Context Summary]",
) -> str:
    """Build a summary of compressed messages.

    Creates a brief summary of the messages being removed.

    Args:
        messages: The messages to summarize.
        prefix: Prefix for the summary text.

    Returns:
        Summary string.
    """
    tool_calls = []
    key_points = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "assistant" and isinstance(content, list):
            for block in content:
                if hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append(block.name)
                elif hasattr(block, "text") and block.text:
                    # Take first 100 chars as a key point
                    text = block.text.strip()
                    if text:
                        key_points.append(text[:100])

        elif role == "user" and isinstance(content, str):
            if len(content) > 20:  # Skip short messages
                key_points.append(content[:100])

    lines = [prefix, ""]
    lines.append(f"Previous conversation ({len(messages)} messages compressed):")

    if tool_calls:
        unique_tools = sorted(set(tool_calls))
        lines.append(f"- Tools used: {', '.join(unique_tools)} ({len(tool_calls)} total calls)")

    if key_points:
        lines.append("- Key context:")
        for point in key_points[:5]:  # Max 5 key points
            lines.append(f"  • {point}...")

    return "\n".join(lines)


class ContextCompressor:
    """Manages conversation context compression.

    Usage:
        compressor = ContextCompressor()
        messages = conversation.get_messages()
        if compressor.should_compress(messages):
            messages, result = compressor.compress(messages)
            conversation.messages = messages
    """

    def __init__(self, config: Optional[CompressionConfig] = None):
        """Initialize the context compressor.

        Args:
            config: Compression configuration. Uses defaults if not provided.
        """
        self.config = config or CompressionConfig()
        self._compression_count = 0

    def should_compress(self, messages: list[dict[str, Any]]) -> bool:
        """Check whether compression should be triggered.

        Args:
            messages: Current conversation messages.

        Returns:
            True if the message count exceeds the threshold.
        """
        if not self.config.enabled:
            return False
        return len(messages) > self.config.max_messages

    def compress(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], CompressionResult]:
        """Compress conversation messages.

        Strategy:
        1. Preserve first N messages (task context)
        2. Preserve last N messages (recent context)
        3. Summarize the middle section
        4. Truncate verbose tool results

        Args:
            messages: Current conversation messages.

        Returns:
            Tuple of (compressed messages, compression result).
        """
        original_count = len(messages)

        if original_count <= self.config.target_messages:
            return messages, CompressionResult(
                original_count=original_count,
                compressed_count=original_count,
                removed_count=0,
                summary_added=False,
                truncated_results=0,
            )

        # Split into preserved and compressible sections
        first_n = min(self.config.preserve_first_n, len(messages))
        last_n = min(self.config.preserve_last_n, len(messages) - first_n)

        head = messages[:first_n]
        middle = messages[first_n : len(messages) - last_n] if last_n > 0 else messages[first_n:]
        tail = messages[-last_n:] if last_n > 0 else []

        # Build summary of middle section
        summary_text = build_summary_message(middle, self.config.summary_prefix)
        summary_msg = {"role": "user", "content": summary_text}

        # Reconstruct: head + summary + tail
        compressed = head + [summary_msg] + tail

        # Truncate tool results in the tail
        compressed, truncated_count = truncate_tool_results(
            compressed, self.config.max_tool_result_length
        )

        self._compression_count += 1

        result = CompressionResult(
            original_count=original_count,
            compressed_count=len(compressed),
            removed_count=original_count - len(compressed),
            summary_added=True,
            truncated_results=truncated_count,
        )

        logger.info(
            f"Context compressed: {original_count} → {len(compressed)} messages "
            f"(removed {result.removed_count}, truncated {truncated_count} results)"
        )

        return compressed, result

    @property
    def compression_count(self) -> int:
        """Number of times compression has been performed."""
        return self._compression_count
