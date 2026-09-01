"""Deterministic contract tests for the per-client OpenAlex rate limiter."""

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import final

from zotero_arxiv_daily.retriever.openalex_client import (
    OpenAlexClientTiming,
    SlidingWindowRateLimiter,
)


@final
class _FakeTime:
    def __init__(self) -> None:
        self._now = 0.0
        self._sleeps: list[float] = []
        self._lock = Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._sleeps.append(seconds)
            self._now += seconds

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds

    @property
    def sleeps(self) -> tuple[float, ...]:
        with self._lock:
            return tuple(self._sleeps)


def _timing(fake_time: _FakeTime) -> OpenAlexClientTiming:
    return OpenAlexClientTiming(fake_time.monotonic, fake_time.sleep)


def test_limiter_uses_a_true_sliding_one_second_window() -> None:
    fake_time = _FakeTime()
    limiter = SlidingWindowRateLimiter(_timing(fake_time))
    for _ in range(50):
        assert limiter.acquire() == 0.0
    fake_time.advance(0.5)
    for _ in range(50):
        assert limiter.acquire() == 0.5

    assert limiter.acquire() == 1.0
    for _ in range(49):
        assert limiter.acquire() == 1.0
    assert limiter.acquire() == 1.5
    assert fake_time.sleeps == (0.5, 0.5)


def test_limiter_is_thread_safe_at_one_hundred_starts_per_second() -> None:
    fake_time = _FakeTime()
    limiter = SlidingWindowRateLimiter(_timing(fake_time))

    def acquire(_: int) -> float:
        return limiter.acquire()

    with ThreadPoolExecutor(max_workers=32) as pool:
        starts = sorted(pool.map(acquire, range(250)))

    assert len(starts) == 250
    assert all(starts[index] - starts[index - 100] >= 1.0 for index in range(100, 250))
    assert fake_time.sleeps == (1.0, 1.0)
