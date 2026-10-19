"""Conservative O(1) reservations for the fixed manifest wire format."""
from typing import Final

from .codec import MAX_CIPHER, MAX_PLAIN

MAX_ENTRIES: Final = 100_000
# A 64-hex key, fixed shard name/kind, JSON punctuation, and two finite
# nonnegative float64 timestamps (at most 24 characters each) fit in 256 bytes.
ENTRY_PLAIN_BYTES: Final = 256
MANIFEST_PLAIN_BYTES: Final = 32


def manifest_plain_bound(count: int) -> int:
    return MANIFEST_PLAIN_BYTES + ENTRY_PLAIN_BYTES * count


def manifest_cipher_bound(count: int) -> int:
    plain = manifest_plain_bound(count)
    # zlib compressBound for the default window/memory settings, then Fernet's
    # PKCS7 padding, 57-byte envelope, and base64 expansion. No compression gain
    # is assumed when reserving the old manifest plus its replacement temporary.
    compressed = plain + (plain >> 12) + (plain >> 14) + (plain >> 25) + 13
    envelope = 57 + 16 * (compressed // 16 + 1)
    return 4 * ((envelope + 2) // 3)


def manifest_fits(count: int) -> bool:
    return (count <= MAX_ENTRIES and manifest_plain_bound(count) <= MAX_PLAIN
            and manifest_cipher_bound(count) <= MAX_CIPHER)
