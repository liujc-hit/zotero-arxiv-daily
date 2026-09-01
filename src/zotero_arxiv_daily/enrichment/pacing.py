"""Thread-safe start pacing with injectable monotonic time."""

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import monotonic, sleep
from typing import Final, final


type _Clock = Callable[[], float]
type _Sleeper = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class ProviderTiming:
    clock: _Clock = monotonic
    sleeper: _Sleeper = sleep


@final
class StartPacer:
    """Serialize provider calls so their actual starts obey a fixed interval."""

    def __init__(self, starts_per_second: float, timing: ProviderTiming) -> None:
        self._lock = Lock()
        self._next_start = 0.0
        self._starts_per_second = starts_per_second
        self._timing = timing

    def call[ResultT](self, operation: Callable[[], ResultT]) -> ResultT:
        """Pace one operation's start, then invoke it outside the start-order lock."""
        with self._lock:
            interval_seconds = 1.0 / self._starts_per_second
            now = self._timing.clock()
            wait_seconds = self._next_start - now
            if wait_seconds > 0:
                self._timing.sleeper(wait_seconds)
            started_at = self._timing.clock()
            self._next_start = max(self._next_start, started_at) + interval_seconds
        return operation()


__all__: Final[tuple[str, ...]] = ("ProviderTiming", "StartPacer")
