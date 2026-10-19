"""Private encrypted local compute cache; no global secret binding.

Use ``is_ready`` for availability. ``flush()`` publishes aggregate status and
reports cumulative ciphertext changes since opening, not availability.
"""
from .cache import ComputeCache
from .codec import key_id
from .config import open_compute_cache
from .models import CacheSettings, CacheStats, CacheValue, FloatArray, ScoreSnapshot

__all__ = ["CacheSettings", "CacheStats", "CacheValue", "ComputeCache", "FloatArray",
           "ScoreSnapshot", "key_id", "open_compute_cache"]
