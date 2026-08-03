import unittest
from unittest import mock

from pipeline import retry

from .support import PipelineTestCase


class WithBackoffTest(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.sleep = self.patch_sleep(retry)

    def test_returns_immediately_on_success(self):
        fn = mock.Mock(return_value="ok")
        self.assertEqual(retry.with_backoff(fn), "ok")
        self.assertEqual(fn.call_count, 1)
        self.sleep.assert_not_called()

    def test_retries_until_success(self):
        fn = mock.Mock(side_effect=[ValueError("a"), ValueError("b"), "ok"])
        self.assertEqual(retry.with_backoff(fn, attempts=3), "ok")
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_raises_the_last_exception_when_attempts_are_exhausted(self):
        fn = mock.Mock(side_effect=[ValueError("first"), ValueError("last")])
        with self.assertRaises(ValueError) as ctx:
            retry.with_backoff(fn, attempts=2)
        self.assertEqual(str(ctx.exception), "last")

    def test_does_not_sleep_after_the_final_failed_attempt(self):
        """Sleeping after the last attempt would add latency before an error
        the caller is going to see anyway."""
        fn = mock.Mock(side_effect=ValueError("nope"))
        with self.assertRaises(ValueError):
            retry.with_backoff(fn, attempts=3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_on_retry_is_called_only_when_another_attempt_follows(self):
        on_retry = mock.Mock()
        fn = mock.Mock(side_effect=ValueError("nope"))
        with self.assertRaises(ValueError):
            retry.with_backoff(fn, attempts=3, on_retry=on_retry)
        self.assertEqual(on_retry.call_count, 2)
        self.assertEqual([c.args[0] for c in on_retry.call_args_list], [1, 2])

    def test_backoff_is_exponential_with_jitter(self):
        fn = mock.Mock(side_effect=[ValueError(), ValueError(), "ok"])
        retry.with_backoff(fn, attempts=3, base_delay=2.0)
        first, second = [c.args[0] for c in self.sleep.call_args_list]
        self.assertTrue(2.0 <= first < 2.5, first)
        self.assertTrue(4.0 <= second < 4.5, second)

    def test_single_attempt_never_retries(self):
        fn = mock.Mock(side_effect=ValueError("nope"))
        with self.assertRaises(ValueError):
            retry.with_backoff(fn, attempts=1)
        self.assertEqual(fn.call_count, 1)
        self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
