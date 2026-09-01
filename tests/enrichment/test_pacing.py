"""Deterministic provider-start pacing contracts."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from typing import Final, final

from zotero_arxiv_daily.enrichment.pacing import ProviderTiming, StartPacer


CONCURRENCY_TIMEOUT_SECONDS: Final = 1.0
FUTURE_TIMEOUT_SECONDS: Final = 5.0


@final
class _FakeTime:
    """Advance injected monotonic time instead of blocking tests."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._now = 0.0
        self._sleeps: list[float] = []

    def monotonic(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._sleeps.append(seconds)
            self._now += seconds

    @property
    def sleeps(self) -> tuple[float, ...]:
        with self._lock:
            return tuple(self._sleeps)


def test_start_pacer_serializes_concurrent_starts_at_the_configured_rate() -> None:
    # Given four simultaneous workers and an injected four-starts-per-second clock
    fake_time = _FakeTime()
    pacer = StartPacer(4.0, ProviderTiming(fake_time.monotonic, fake_time.sleep))
    barrier = Barrier(5)
    starts: list[float] = []
    starts_lock = Lock()

    def invoke(index: int) -> int:
        _ = barrier.wait(timeout=5)

        def operation() -> int:
            with starts_lock:
                starts.append(fake_time.monotonic())
            return index

        return pacer.call(operation)

    # When every worker attempts to begin together
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = tuple(pool.submit(invoke, index) for index in range(4))
        _ = barrier.wait(timeout=5)
        results = tuple(future.result(timeout=5) for future in futures)

    # Then actual operation starts are serialized and exactly rate-spaced
    assert sorted(results) == [0, 1, 2, 3]
    assert sorted(starts) == [0.0, 0.25, 0.5, 0.75]
    assert fake_time.sleeps == (0.25, 0.25, 0.25)


def test_start_pacer_allows_operations_to_overlap_after_rate_spaced_starts() -> None:
    # Given a first operation held open after its paced start
    fake_time = _FakeTime()
    pacer = StartPacer(4.0, ProviderTiming(fake_time.monotonic, fake_time.sleep))
    first_started = Event()
    second_started = Event()
    release_first = Event()
    state_lock = Lock()
    starts: list[float] = []
    active = 0
    max_active = 0

    def operation(index: int) -> int:
        nonlocal active, max_active
        with state_lock:
            starts.append(fake_time.monotonic())
            active += 1
            max_active = max(max_active, active)
        try:
            if index == 0:
                first_started.set()
                assert release_first.wait(FUTURE_TIMEOUT_SECONDS)
            else:
                second_started.set()
            return index
        finally:
            with state_lock:
                active -= 1

    # When a second operation is admitted before the first completes
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(pacer.call, lambda: operation(0))
        assert first_started.wait(CONCURRENCY_TIMEOUT_SECONDS)
        second = pool.submit(pacer.call, lambda: operation(1))
        overlapped = second_started.wait(CONCURRENCY_TIMEOUT_SECONDS)
        release_first.set()
        results = (
            first.result(timeout=FUTURE_TIMEOUT_SECONDS),
            second.result(timeout=FUTURE_TIMEOUT_SECONDS),
        )

    # Then start times remain paced while network-duration work overlaps
    assert overlapped is True
    assert results == (0, 1)
    assert starts == [0.0, 0.25]
    assert fake_time.sleeps == (0.25,)
    assert max_active == 2
