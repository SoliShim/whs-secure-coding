"""Bounded, thread-safe in-process fixed-window rate limiting."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RateLimiter:
    """A fail-closed fixed-window limiter for a single application process.

    It is appropriate for this one-worker local assignment.  A multi-worker
    deployment must replace it with a shared atomic store such as Redis.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = 50_000,
    ) -> None:
        if max_entries < 100:
            raise ValueError("max_entries는 100 이상이어야 합니다.")
        self._clock = clock
        self._max_entries = max_entries
        self._buckets: dict[tuple[str, int], tuple[int, int]] = {}
        self._lock = threading.Lock()
        self._operations = 0

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Consume one request allowance and return whether it is permitted."""

        if not isinstance(key, str) or not key or len(key) > 512:
            return False
        if not isinstance(limit, int) or not isinstance(window_seconds, int):
            raise ValueError("limit과 window_seconds는 정수여야 합니다.")
        if limit < 1 or window_seconds < 1:
            raise ValueError("limit과 window_seconds는 1 이상이어야 합니다.")

        now = self._clock()
        window_id = int(now // window_seconds)
        bucket_key = (key, window_seconds)

        with self._lock:
            self._operations += 1
            if self._operations % 256 == 0 or len(self._buckets) >= self._max_entries:
                self._prune(now)

            current = self._buckets.get(bucket_key)
            if current is None or current[0] != window_id:
                if current is None and len(self._buckets) >= self._max_entries:
                    # Memory exhaustion must not turn into a rate-limit bypass.
                    return False
                self._buckets[bucket_key] = (window_id, 1)
                return True

            _, count = current
            if count >= limit:
                return False
            self._buckets[bucket_key] = (window_id, count + 1)
            return True

    def _prune(self, now: float) -> None:
        expired = [
            bucket_key
            for bucket_key, (window_id, _) in self._buckets.items()
            if window_id != int(now // bucket_key[1])
        ]
        for bucket_key in expired:
            self._buckets.pop(bucket_key, None)

    def clear(self) -> None:
        """Clear all buckets; intended for test isolation and controlled maintenance."""

        with self._lock:
            self._buckets.clear()
            self._operations = 0
