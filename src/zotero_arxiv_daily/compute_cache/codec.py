"""Authenticated, bounded numeric serialization; never accepts pickle."""
import base64
import hashlib
import hmac
import json
import zlib
from typing import Final, assert_never

from cryptography.fernet import Fernet
import numpy as np
from pydantic import TypeAdapter

from .models import CacheValue, FloatArray, Record, ScoreRecord, ScoreSnapshot, VectorRecord

MAX_CIPHER: Final = 16_777_216
MAX_PLAIN: Final = 32_000_000
RECORD_ADAPTER: Final[TypeAdapter[Record]] = TypeAdapter(Record)


class InvalidCache(ValueError):
    """Opaque boundary error without input data."""

    def __init__(self) -> None:
        super().__init__("Invalid compute cache")


def raw_key(key: str) -> bytes:
    raw = base64.b64decode(key.encode("ascii"), altchars=b"-_", validate=True)
    if len(raw) != 32:
        raise InvalidCache()
    return raw


def key_id(key: str) -> str:
    return hmac.new(raw_key(key), b"zad-compute-cache-v1", hashlib.sha256).hexdigest()[:16]


def make_private_key(secret: bytes, kind: str, parts: CacheValue) -> str:
    if not kind or len(kind) > 64 or not kind.isascii() or not all(c.isalnum() or c in "_-" for c in kind):
        raise InvalidCache()
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return hmac.new(secret, b"zad-compute-cache-v1\0" + kind.encode("ascii") + b"\0" + canonical, hashlib.sha256).hexdigest()


def encrypt(cipher: Fernet, data: bytes) -> bytes:
    if len(data) > MAX_PLAIN:
        raise InvalidCache()
    result = cipher.encrypt(zlib.compress(data))
    if len(result) > MAX_CIPHER:
        raise InvalidCache()
    return result


def decrypt(cipher: Fernet, data: bytes) -> bytes:
    if len(data) > MAX_CIPHER:
        raise InvalidCache()
    compressed = cipher.decrypt(data)
    decoder = zlib.decompressobj()
    result = decoder.decompress(compressed, MAX_PLAIN + 1)
    if len(result) > MAX_PLAIN or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise InvalidCache()
    return result


def vector_record(key: str, array: FloatArray, created: float) -> VectorRecord:
    if array.ndim != 1 or array.size > 1_000_000 or not np.isfinite(array).all():
        raise InvalidCache()
    # Parse dtype at the boundary before retaining or allocating numeric data.
    return VectorRecord.model_validate({"key": key, "created": created, "dtype": array.dtype.str,
                                        "count": int(array.size), "data": base64.b64encode(array.tobytes()).decode("ascii")})


def valid_scores(snapshot: ScoreSnapshot, count: int) -> bool:
    return (0 <= count <= 1_000_000 and len(snapshot.scores) == count
            and len(snapshot.order) == count and all(type(i) is int for i in snapshot.order)
            and set(snapshot.order) == set(range(count)) and all(np.isfinite(s) for s in snapshot.scores))


def decode_record(record: Record) -> FloatArray | ScoreSnapshot:
    match record:
        case VectorRecord():
            dtype = np.dtype(record.dtype)
            if len(record.data) != 4 * ((record.count * dtype.itemsize + 2) // 3):
                raise InvalidCache()
            data = base64.b64decode(record.data, validate=True)
            if len(data) != record.count * dtype.itemsize:
                raise InvalidCache()
            result = np.frombuffer(data, dtype=dtype).copy()
            if not np.isfinite(result).all():
                raise InvalidCache()
            return result
        case ScoreRecord():
            snapshot = ScoreSnapshot(record.scores, record.order)
            if not valid_scores(snapshot, len(snapshot.scores)):
                raise InvalidCache()
            return snapshot
        case _:
            assert_never(record)
