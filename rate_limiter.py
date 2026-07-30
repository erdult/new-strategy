"""Sliding-window rate limiter decorator with exponential backoff on 429."""

import time
import logging
from functools import wraps

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, max_calls: int = 3, window_seconds: float = 1.0):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._timestamps: list[float] = []

    def _slide_window(self):
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self._timestamps = [t for t in self._timestamps if t > cutoff]

    def acquire(self) -> float:
        while True:
            self._slide_window()
            if len(self._timestamps) < self.max_calls:
                self._timestamps.append(time.monotonic())
                return 0.0
            sleep_for = self._timestamps[0] + self.window_seconds - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            continue

def retry_with_backoff(max_retries: int = 3, base_delay: float = 1.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "Attempt %d/%d failed: %s. Retrying in %.1fs",
                            attempt + 1, max_retries + 1, e, delay
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            "All %d attempts failed for %s: %s",
                            max_retries + 1, func.__name__, e
                        )
                        raise
            return None
        return wrapper
    return decorator
