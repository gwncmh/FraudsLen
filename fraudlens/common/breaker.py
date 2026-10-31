"""Circuit breaker đơn giản: N lỗi liên tiếp -> ngắt trong T giây, sau đó cho thử lại."""
from __future__ import annotations

import threading
import time


class CircuitBreaker:
    def __init__(self, fail_threshold: int, open_seconds: float):
        self.fail_threshold = fail_threshold
        self.open_seconds = open_seconds
        self._fails = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if time.monotonic() - self._opened_at >= self.open_seconds:
                self._opened_at = None  # half-open: cho một lượt thử
                self._fails = self.fail_threshold - 1
                return True
            return False

    def success(self) -> None:
        with self._lock:
            self._fails = 0
            self._opened_at = None

    def failure(self) -> None:
        with self._lock:
            self._fails += 1
            if self._fails >= self.fail_threshold:
                self._opened_at = time.monotonic()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._opened_at is not None
