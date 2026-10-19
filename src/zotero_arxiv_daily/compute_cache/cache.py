"""Optional fail-soft compute-cache API."""
from collections.abc import Callable
from dataclasses import asdict
import binascii
import json
import math
import re
import time
from typing import Final, final
import zlib

from cryptography.fernet import InvalidToken
from loguru import logger
import numpy as np
from pydantic import ValidationError

from .codec import InvalidCache, decode_record, make_private_key, valid_scores, vector_record
from .files import atomic_write, safe_path
from .ledger import Ledger
from .models import CacheSettings, CacheStats, CacheValue, FloatArray, ScoreRecord, ScoreSnapshot

CACHE_ERRORS: Final = (OSError, ValueError, TypeError, OverflowError, RecursionError, InvalidToken, ValidationError, zlib.error, binascii.Error)


@final
class ComputeCache:
    """Mutable counters and disk ledger scoped to one private cache instance."""

    def __init__(self, settings: CacheSettings, *, clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self.clock = clock
        self._ledger: Ledger | None = None
        self._warned = False
        self._vector_hits = 0
        self._vector_misses = 0
        self._score_hits = 0
        self._score_misses = 0
        self._ready = False
        if settings.enabled:
            try:
                if (not settings.model_revision or not settings.key
                        or type(settings.budget_bytes) is not int
                        or not 0 < settings.budget_bytes <= 8_000_000_000
                        or not 0 < settings.shard_bytes <= 16_777_216 or settings.ttl_days <= 0
                        or not math.isfinite(clock()) or clock() < 0):
                    raise InvalidCache()
                self._ledger = Ledger(settings)
                self._ready = True
            except CACHE_ERRORS:
                self._fail()
        self._status()

    def _fail(self) -> None:
        self._ready = False
        if not self._warned:
            logger.warning("Compute cache unavailable; continuing without cached results")
            self._warned = True

    def _status(self) -> None:
        path = self.settings.path
        status = path.parent / (path.name + "-status.json")
        try:
            safe_path(status)
            status.parent.mkdir(parents=True, exist_ok=True)
            changed = self._ledger.changed if self._ledger else False
            published = self._ledger is None or not self._ledger.dirty
            counts = asdict(self.stats())
            data = {"ready": self._ready and published, "changed": changed, "bytes": counts.pop("bytes_used"), **counts}
            atomic_write(status, json.dumps(data, separators=(",", ":")).encode())
        except CACHE_ERRORS:
            self._fail()

    def make_key(self, kind: str, parts: CacheValue) -> str:
        if self._ledger is None:
            raise InvalidCache()
        return make_private_key(self._ledger.secret, kind, parts)

    def _read(self, key: str) -> FloatArray | ScoreSnapshot | None:
        if not self._ready or self._ledger is None or not re.fullmatch(r"[0-9a-f]{64}", key):
            return None
        try:
            now = self.clock()
            if not math.isfinite(now) or now < 0:
                raise InvalidCache()
            record = self._ledger.read(key, now)
            return None if record is None else decode_record(record)
        except CACHE_ERRORS:
            self._fail()
            return None

    def get_vector(self, key: str) -> FloatArray | None:
        result = self._read(key)
        if isinstance(result, np.ndarray):
            self._vector_hits += 1
            return result
        self._vector_misses += 1
        return None

    def put_vector(self, key: str, array: FloatArray) -> None:
        if not self._ready or self._ledger is None:
            return
        try:
            record = vector_record(key, array, float(self.clock()))
            was_dirty = self._ledger.dirty
            self._ledger.put(record)
            if self._ledger.dirty and not was_dirty:
                self._status()
        except CACHE_ERRORS:
            self._fail()

    def get_scores(self, key: str, candidate_count: int) -> ScoreSnapshot | None:
        result = self._read(key)
        if isinstance(result, ScoreSnapshot) and valid_scores(result, candidate_count):
            self._score_hits += 1
            return result
        self._score_misses += 1
        return None

    def put_scores(self, key: str, snapshot: ScoreSnapshot) -> None:
        if not self._ready or self._ledger is None or not valid_scores(snapshot, len(snapshot.scores)):
            return
        try:
            record = ScoreRecord(key=key, created=float(self.clock()), scores=snapshot.scores, order=snapshot.order)
            was_dirty = self._ledger.dirty
            self._ledger.put(record)
            if self._ledger.dirty and not was_dirty:
                self._status()
        except CACHE_ERRORS:
            self._fail()

    @property
    def is_ready(self) -> bool:
        """Report cache availability independently of ciphertext changes."""
        return self._ready

    def flush(self) -> bool:
        """Publish pending manifest/status and report cumulative cipher changes, if ready."""
        if self._ready and self._ledger is not None:
            try:
                self._ledger.publish_manifest()
            except CACHE_ERRORS:
                self._fail()
        self._status()
        return self._ready and self._ledger is not None and self._ledger.changed

    def stats(self) -> CacheStats:
        ledger = self._ledger
        return CacheStats(self._vector_hits, self._vector_misses, self._score_hits, self._score_misses,
                          ledger.evictions if ledger else 0, ledger.bytes_used if ledger else 0,
                          ledger.writes if ledger else 0)
