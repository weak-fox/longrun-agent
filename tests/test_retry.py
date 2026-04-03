"""Tests for the RetryHandler and error classification logic."""

import unittest
from unittest.mock import MagicMock

import anthropic

from longrun_agent.retry import (
    ErrorCategory,
    RetryAttempt,
    RetryHandler,
    RetryPolicy,
    classify_error,
)


class TestErrorClassification(unittest.TestCase):
    """Tests for classify_error()."""

    def test_classify_429_as_transient(self):
        error = anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )
        self.assertEqual(classify_error(error), ErrorCategory.TRANSIENT)

    def test_classify_529_as_transient(self):
        error = anthropic.APIStatusError(
            message="overloaded",
            response=MagicMock(status_code=529, headers={}),
            body=None,
        )
        self.assertEqual(classify_error(error), ErrorCategory.TRANSIENT)

    def test_classify_500_as_server(self):
        error = anthropic.InternalServerError(
            message="internal error",
            response=MagicMock(status_code=500, headers={}),
            body=None,
        )
        self.assertEqual(classify_error(error), ErrorCategory.SERVER)

    def test_classify_502_as_server(self):
        error = anthropic.APIStatusError(
            message="bad gateway",
            response=MagicMock(status_code=502, headers={}),
            body=None,
        )
        self.assertEqual(classify_error(error), ErrorCategory.SERVER)

    def test_classify_400_as_client(self):
        error = anthropic.BadRequestError(
            message="bad request",
            response=MagicMock(status_code=400, headers={}),
            body=None,
        )
        self.assertEqual(classify_error(error), ErrorCategory.CLIENT)

    def test_classify_401_as_client(self):
        error = anthropic.AuthenticationError(
            message="unauthorized",
            response=MagicMock(status_code=401, headers={}),
            body=None,
        )
        self.assertEqual(classify_error(error), ErrorCategory.CLIENT)

    def test_classify_connection_error_as_transient(self):
        error = anthropic.APIConnectionError(request=MagicMock())
        self.assertEqual(classify_error(error), ErrorCategory.TRANSIENT)

    def test_classify_timeout_error(self):
        error = anthropic.APITimeoutError(request=MagicMock())
        self.assertEqual(classify_error(error), ErrorCategory.TIMEOUT)

    def test_classify_unknown_exception(self):
        error = RuntimeError("something unexpected")
        self.assertEqual(classify_error(error), ErrorCategory.UNKNOWN)


class TestRetryPolicy(unittest.TestCase):
    """Tests for RetryPolicy configuration."""

    def test_default_policy(self):
        policy = RetryPolicy()
        self.assertEqual(policy.max_retries, 3)
        self.assertEqual(policy.base_delay, 5.0)
        self.assertEqual(policy.max_delay, 300.0)
        self.assertTrue(policy.jitter)

    def test_client_errors_never_retried(self):
        policy = RetryPolicy(max_retries=5)
        self.assertEqual(policy.max_retries_for(ErrorCategory.CLIENT), 0)

    def test_transient_uses_default(self):
        policy = RetryPolicy(max_retries=5)
        self.assertEqual(policy.max_retries_for(ErrorCategory.TRANSIENT), 5)

    def test_category_override(self):
        policy = RetryPolicy(
            max_retries=3,
            category_overrides={ErrorCategory.SERVER: 5},
        )
        self.assertEqual(policy.max_retries_for(ErrorCategory.SERVER), 5)
        self.assertEqual(policy.max_retries_for(ErrorCategory.TRANSIENT), 3)


class TestRetryHandler(unittest.TestCase):
    """Tests for RetryHandler execution logic."""

    def _make_handler(self, policy=None, on_retry=None):
        """Create a handler with a no-op sleep for testing."""
        return RetryHandler(
            policy=policy or RetryPolicy(max_retries=3, jitter=False),
            on_retry=on_retry,
            sleep_fn=lambda _: None,  # No actual sleeping in tests
        )

    def test_success_on_first_try(self):
        handler = self._make_handler()
        result = handler.execute(lambda: "ok")
        self.assertEqual(result, "ok")
        self.assertEqual(len(handler.attempts), 0)

    def test_retry_on_transient_error_then_succeed(self):
        call_count = 0

        def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise anthropic.APIConnectionError(request=MagicMock())
            return "recovered"

        handler = self._make_handler()
        result = handler.execute(flaky_fn)
        self.assertEqual(result, "recovered")
        self.assertEqual(call_count, 3)
        self.assertEqual(len(handler.attempts), 2)  # 2 retries before success

    def test_client_error_not_retried(self):
        def fail_fn():
            raise anthropic.BadRequestError(
                message="bad input",
                response=MagicMock(status_code=400, headers={}),
                body=None,
            )

        handler = self._make_handler()
        with self.assertRaises(anthropic.BadRequestError):
            handler.execute(fail_fn)
        self.assertEqual(len(handler.attempts), 0)  # No retries for client errors

    def test_exhausted_retries_raises(self):
        def always_fail():
            raise anthropic.APIConnectionError(request=MagicMock())

        policy = RetryPolicy(max_retries=2, jitter=False)
        handler = self._make_handler(policy=policy)
        with self.assertRaises(anthropic.APIConnectionError):
            handler.execute(always_fail)
        self.assertEqual(len(handler.attempts), 2)

    def test_exponential_backoff_delays(self):
        delays = []

        def capture_sleep(delay):
            delays.append(delay)

        handler = RetryHandler(
            policy=RetryPolicy(max_retries=3, base_delay=2.0, exponential_base=2.0, jitter=False),
            sleep_fn=capture_sleep,
        )

        call_count = 0

        def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise anthropic.APIConnectionError(request=MagicMock())
            return "done"

        handler.execute(fail_then_succeed)
        # Expected delays: 2.0, 4.0, 8.0 (base * 2^attempt)
        self.assertEqual(len(delays), 3)
        self.assertAlmostEqual(delays[0], 2.0)
        self.assertAlmostEqual(delays[1], 4.0)
        self.assertAlmostEqual(delays[2], 8.0)

    def test_server_error_gets_double_delay(self):
        delays = []

        def capture_sleep(delay):
            delays.append(delay)

        handler = RetryHandler(
            policy=RetryPolicy(max_retries=2, base_delay=5.0, exponential_base=2.0, jitter=False),
            sleep_fn=capture_sleep,
        )

        call_count = 0

        def fail_server():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise anthropic.InternalServerError(
                    message="server error",
                    response=MagicMock(status_code=500, headers={}),
                    body=None,
                )
            return "recovered"

        handler.execute(fail_server)
        # Server errors double the delay: 5.0 * 2^0 * 2.0 = 10.0
        self.assertAlmostEqual(delays[0], 10.0)

    def test_on_retry_callback_invoked(self):
        callback_calls = []

        def on_retry(attempt: RetryAttempt):
            callback_calls.append(attempt)

        handler = self._make_handler(on_retry=on_retry)
        call_count = 0

        def fail_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise anthropic.APIConnectionError(request=MagicMock())
            return "ok"

        handler.execute(fail_once)
        self.assertEqual(len(callback_calls), 1)
        self.assertEqual(callback_calls[0].attempt, 1)
        self.assertEqual(callback_calls[0].error_category, ErrorCategory.TRANSIENT)

    def test_max_delay_cap(self):
        delays = []

        handler = RetryHandler(
            policy=RetryPolicy(
                max_retries=5,
                base_delay=100.0,
                exponential_base=10.0,
                max_delay=200.0,
                jitter=False,
            ),
            sleep_fn=lambda d: delays.append(d),
        )

        call_count = 0

        def fail_many():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise anthropic.APIConnectionError(request=MagicMock())
            return "ok"

        handler.execute(fail_many)
        # All delays should be capped at 200.0
        for d in delays:
            self.assertLessEqual(d, 200.0)

    def test_attempts_reset_per_call(self):
        handler = self._make_handler()

        call_count = 0

        def fail_once():
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 1:
                raise anthropic.APIConnectionError(request=MagicMock())
            return "ok"

        handler.execute(fail_once)
        self.assertEqual(len(handler.attempts), 1)

        # Second call should reset attempts
        handler.execute(fail_once)
        self.assertEqual(len(handler.attempts), 1)  # Fresh count, not cumulative


if __name__ == "__main__":
    unittest.main()
