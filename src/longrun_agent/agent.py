"""Core Agent class that orchestrates the LLM interaction loop.

Refactored to use:
- RetryHandler for robust API call retry with error classification
- ConversationManager for clean message history management
"""

import json
import logging
import os
import time
from typing import Any, Callable, List, Optional

import anthropic

from .conversation import ConversationManager
from .executor import ToolExecutor
from .prompt import build_system_prompt
from .retry import RetryHandler, RetryPolicy
from .tools import get_tool_definitions

logger = logging.getLogger(__name__)


class Agent:
    """Main agent that runs an agentic loop with tool use.

    The agent calls the Anthropic API, processes tool calls via the ToolExecutor,
    and feeds results back until the model produces a final text response or
    the iteration limit is reached.

    Args:
        model: The Anthropic model to use.
        max_tokens: Max tokens per API response.
        max_iterations: Safety limit on agentic loop iterations.
        api_key: Anthropic API key (falls back to ANTHROPIC_API_KEY env var).
        system_prompt: Custom system prompt (uses default if not provided).
        tools: Custom tool definitions (uses default if not provided).
        mcp_servers: MCP server configurations (reserved for future use).
        cwd: Working directory for tool execution.
        pre_commands: Commands to run before each bash command.
        interrupt_event: Threading event to signal interruption.
        retry_policy: Custom retry policy for API calls.
        on_retry: Callback invoked before each retry sleep.
        on_tool_call: Callback invoked after each tool execution.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 16384,
        max_iterations: int = 200,
        api_key: Optional[str] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[List[dict]] = None,
        mcp_servers: Optional[dict] = None,
        cwd: Optional[str] = None,
        pre_commands: Optional[List[str]] = None,
        interrupt_event: Any = None,
        retry_policy: Optional[RetryPolicy] = None,
        on_retry: Optional[Callable] = None,
        on_tool_call: Optional[Callable] = None,
    ):
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations
        self.system_prompt = system_prompt or build_system_prompt()
        self.tools = tools or get_tool_definitions()
        self.executor = ToolExecutor(
            cwd=cwd, pre_commands=pre_commands, interrupt_event=interrupt_event
        )
        self.mcp_servers = mcp_servers

        # New: pluggable retry handler
        self._retry_handler = RetryHandler(
            policy=retry_policy or RetryPolicy(),
            on_retry=on_retry,
        )

        # New: conversation manager for clean message tracking
        self.conversation = ConversationManager()

        # New: optional callback for tool call observability
        self._on_tool_call = on_tool_call

    def run(self, user_message: str) -> str:
        """Run the agentic loop for a user message.

        Args:
            user_message: The user's task or question.

        Returns:
            The agent's final text response.
        """
        self.conversation.clear()
        self.conversation.add_user_message(user_message)

        for iteration in range(self.max_iterations):
            logger.info(f"--- Iteration {iteration + 1} ---")

            response = self._call_api(self.conversation.get_messages())
            self.conversation.stats.record_api_call(getattr(response, "usage", None))
            self.conversation.add_assistant_message(response.content)

            if response.stop_reason == "end_turn":
                return ConversationManager.extract_text(response.content)

            # Process tool calls
            tool_calls = ConversationManager.extract_tool_calls(response.content)
            if not tool_calls:
                break

            tool_results = []
            for call in tool_calls:
                self.conversation.stats.record_tool_call()
                result = self.executor.execute(call.name, call.input)
                logger.info(
                    f"Tool {call.name} result (truncated): {str(result)[:200]}"
                )

                if self._on_tool_call:
                    self._on_tool_call(call.name, call.input, result)

                tool_results.append(
                    ConversationManager.build_tool_result(call.tool_use_id, result)
                )

            self.conversation.add_tool_results(tool_results)

        return ConversationManager.extract_text(response.content)

    def _call_api(self, messages: list[dict]) -> Any:
        """Call the Anthropic API with retry handling.

        Args:
            messages: The conversation messages to send.

        Returns:
            The API response object.
        """

        def _do_call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system_prompt,
                tools=self.tools,
                messages=messages,
            )

        return self._retry_handler.execute(_do_call)

    @staticmethod
    def _extract_text(content) -> str:
        """Extract text from API response content blocks.

        Deprecated: Use ConversationManager.extract_text() instead.
        Kept for backward compatibility.
        """
        return ConversationManager.extract_text(content)

    def get_stats(self) -> dict:
        """Get conversation statistics.

        Returns:
            Dict with total_turns, tool_calls, api_calls, input_tokens, output_tokens.
        """
        stats = self.conversation.stats
        return {
            "total_turns": stats.total_turns,
            "tool_calls": stats.tool_calls,
            "api_calls": stats.api_calls,
            "input_tokens": stats.input_tokens,
            "output_tokens": stats.output_tokens,
        }
