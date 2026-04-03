"""Configurable API retry handler with error classification and exponential backoff.

This module extracts the retry logic from agent.py into a standalone, testable,
and configurable component. It supports:
- Error classification (transient vs permanent)
- Exponential backoff with jitter
- Configurable retry policies per error type
- Callback hooks for monitoring
"""

import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

import anthropic

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ErrorCategory(Enum):
    """Classification of API errors for retry decisions."""

    TRANSIENT = "transient"  # 429, 529, connection errors — always retry
    SERVER = "server"  # 500, 502, 503 — retry with longer backoff
    CLIENT = "client"  # 400, 401, 403, 404 — never retry
    TIMEOUT = "timeout"  # Request timeout — retry with same backoff
    UNKNOWN = "unknown"  # Unclassified — retry cautiously


@dataclass
class RetryPolicy:
    """Configuration for retry behavior."""

    max_retries: int = 3
    base_delay: float = 5.0  # seconds
    max_delay: float = 300.0  # seconds
    exponential_base: float = 2.0
    jitter: bool = True
    # Per-category overrides: category -> max_retries (None = use default)
    category_overrides: dict[ErrorCategory, int] = field(default_factory=dict)

    def max_retries_for(self, category: ErrorCategory) -> int:
        """Get max retries for a specific error category."""
        if category == ErrorCategory.CLIENT:
            return 0  # Never retry client errors
        return self.category_overrides.get(category, self.max_retries)


@dataclass
class RetryAttempt:
    """Record of a single retry attempt for observability."""

    attempt: int
    error_category: ErrorCategory
    error_message: str
    wait_seconds: float
    timestamp: float = field(default_factory=time.time)


def classify_error(error: Exception) -> ErrorCategory:
    """Classify an exception into an ErrorCategory for retry decisions.

    Args:
        error: The exception to classify.

    Returns:
        The ErrorCategory for the given error.
    """
    if isinstance(error, anthropic.APIStatusError):
        status = error.status_code
        if status in (429, 529):
            return ErrorCategory.TRANSIENT
        elif status in (500, 502, 503):
            return ErrorCategory.SERVER
        elif status in (400, 401, 403, 404, 409, 422):
            return ErrorCategory.CLIENT
        else:
            return ErrorCategory.UNKNOWN
    elif isinstance(error, anthropic.APITimeoutError):
        # Check APITimeoutError before APIConnectionError (subclass first)
        return ErrorCategory.TIMEOUT
    elif isinstance(error, anthropic.APIConnectionError):
        return ErrorCategory.TRANSIENT
    else:
        return ErrorCategory.UNKNOWN


class RetryHandler:
    """Handles API call retries with configurable policy and error classification.

    Usage:
        handler = RetryHandler(policy=RetryPolicy(max_retries=3))
        result = handler.execute(lambda: client.messages.create(...))

    The handler classifies errors, applies appropriate backoff, and provides
    observability through attempt history and optional callbacks.
    """

    def __init__(
        self,
        policy: Optional[RetryPolicy] = None,
        on_retry: Optional[Callable[[RetryAttempt], None]] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ):
        """Initialize the retry handler.

        Args:
            policy: Retry policy configuration. Uses defaults if not provided.
            on_retry: Optional callback invoked before each retry sleep.
            sleep_fn: Optional sleep function for testing. Defaults to time.sleep.
        """
        self.policy = policy or RetryPolicy()
        self.on_retry = on_retry
        self._sleep = sleep_fn or time.sleep
        self.attempts: list[RetryAttempt] = []

    def _compute_delay(self, attempt: int, category: ErrorCategory) -> float:
        """Compute backoff delay for a given attempt and error category.

        Uses exponential backoff: base_delay * (exponential_base ** attempt)
        with optional jitter and capped at max_delay.

        Args:
            attempt: Zero-based attempt index (0 = first retry).
            category: The error category, used to adjust delay.

        Returns:
            Delay in seconds before the next retry.
        """
        delay = self.policy.base_delay * (self.policy.exponential_base ** attempt)

        # Server errors get extra delay
        if category == ErrorCategory.SERVER:
            delay *= 2.0

        delay = min(delay, self.policy.max_delay)

        if self.policy.jitter:
            delay = delay * (0.5 + random.random() * 0.5)

        return delay

    def execute(self, fn: Callable[[], T]) -> T:
        """Execute a function with retry logic.

        Args:
            fn: A callable that performs the API call.

        Returns:
            The return value of fn() on success.

        Raises:
            The last exception if all retries are exhausted, or immediately
            for non-retryable errors.
        """
        self.attempts = []  # Reset per-call history
        last_error: Optional[Exception] = None

        for attempt in range(self.policy.max_retries + 1):
            try:
                return fn()
            except Exception as e:
                category = classify_error(e)
                max_for_category = self.policy.max_retries_for(category)

                if attempt >= max_for_category:
                    logger.error(
                        f"API call failed (category={category.value}, "
                        f"attempt={attempt + 1}/{max_for_category + 1}): {e}"
                    )
                    raise

                delay = self._compute_delay(attempt, category)

                retry_attempt = RetryAttempt(
                    attempt=attempt + 1,
                    error_category=category,
                    error_message=str(e),
                    wait_seconds=delay,
                )
                self.attempts.append(retry_attempt)

                logger.warning(
                    f"API call failed (category={category.value}, "
                    f"attempt={attempt + 1}/{max_for_category + 1}), "
                    f"retrying in {delay:.1f}s: {e}"
                )

                if self.on_retry:
                    self.on_retry(retry_attempt)

                self._sleep(delay)
                last_error = e

        # Should not reach here, but just in case
        if last_error:
            raise last_error
        raise RuntimeError("RetryHandler: unexpected state")
