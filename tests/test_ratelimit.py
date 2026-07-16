"""Unit tests for the in-process sliding-window rate limiter.

The HTTP dependency is inert in the test environment (so the suite's many logins
don't trip it), so the window logic is verified directly here.
"""

import time
import unittest

from app.core.ratelimit import SlidingWindowLimiter


class SlidingWindowLimiterTest(unittest.TestCase):
    def test_allows_up_to_limit_then_blocks(self):
        limiter = SlidingWindowLimiter(max_hits=3, window_seconds=60)
        self.assertIsNone(limiter.hit("ip"))
        self.assertIsNone(limiter.hit("ip"))
        self.assertIsNone(limiter.hit("ip"))
        # Fourth attempt in the window is blocked and reports a retry delay.
        retry = limiter.hit("ip")
        self.assertIsNotNone(retry)
        self.assertGreater(retry, 0)

    def test_keys_are_independent(self):
        limiter = SlidingWindowLimiter(max_hits=1, window_seconds=60)
        self.assertIsNone(limiter.hit("a"))
        self.assertIsNone(limiter.hit("b"))
        self.assertIsNotNone(limiter.hit("a"))

    def test_window_slides(self):
        limiter = SlidingWindowLimiter(max_hits=1, window_seconds=0.2)
        self.assertIsNone(limiter.hit("ip"))
        self.assertIsNotNone(limiter.hit("ip"))
        time.sleep(0.25)
        # Old hit has aged out of the window.
        self.assertIsNone(limiter.hit("ip"))


if __name__ == "__main__":
    unittest.main()
