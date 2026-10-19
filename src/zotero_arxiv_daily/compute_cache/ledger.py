"""Metadata-only buffered LRU ledger; one bounded shard is decoded per read."""
from collections import OrderedDict
import hashlib
import hmac
from typing import Final, final

from cryptography.fernet import Fernet

from .bounds import manifest_cipher_bound, manifest_fits
from .codec import InvalidCache, MAX_PLAIN, RECORD_ADAPTER, decrypt, encrypt, raw_key
from .files import atomic_write, inventory, read_cipher, remove_owned, safe_path
from .models import CacheSettings, Entry, Manifest, Record

MAX_CLOCK_SKEW_SECONDS: Final = 300


@final
class Ledger:
    """Mutable local cache ledger, never a source of authoritative state."""

    def __init__(self, settings: CacheSettings) -> None:
        if settings.key is None:
            raise InvalidCache()
        self.settings = settings
        self.secret = raw_key(settings.key)
        self.cipher = Fernet(settings.key.encode("ascii"))
        self.manifest = Manifest()
        self.sizes: dict[str, int] = {}
        self.changed = False
        self.writes = 0
        self.evictions = 0
        self.dirty = False
        self._prepared = False
        safe_path(settings.path)
        if settings.path.exists():
            self.sizes = inventory(settings.path)
            if "manifest.enc" in self.sizes:
                self.manifest = Manifest.model_validate_json(decrypt(self.cipher, read_cipher(settings.path / "manifest.enc")))
            if len({e.name for e in self.manifest.entries.values()}) != len(self.manifest.entries):
                raise InvalidCache()
            if any(e.name not in self.sizes for e in self.manifest.entries.values()):
                raise InvalidCache()
        self._bytes_used = sum(self.sizes.values())
        self.lru: OrderedDict[str, None] = OrderedDict.fromkeys(sorted(self.manifest.entries, key=lambda k: self.manifest.entries[k].touched))
        self.orphans = set(self.sizes) - {e.name for e in self.manifest.entries.values()} - {"manifest.enc"}

    @property
    def bytes_used(self) -> int:
        return self._bytes_used

    def read(self, key: str, now: float) -> Record | None:
        """Allow bounded NTP skew without moving the creation-based TTL deadline."""
        entry = self.manifest.entries.get(key)
        if entry is None:
            return None
        if (entry.created - now > MAX_CLOCK_SKEW_SECONDS
                or entry.touched - now > MAX_CLOCK_SKEW_SECONDS or entry.touched < entry.created):
            return None
        if max(0., now - entry.created) >= self.settings.ttl_days * 86400:
            return None
        record = RECORD_ADAPTER.validate_json(decrypt(self.cipher, read_cipher(self.settings.path / entry.name)))
        if record.key != key or record.kind != entry.kind or record.created != entry.created:
            raise InvalidCache()
        self.manifest.entries[key] = entry.model_copy(update={"touched": max(float(now), entry.created, entry.touched)})
        self.lru.move_to_end(key)
        return record

    def _manifest_bytes(self) -> bytes:
        return encrypt(self.cipher, self.manifest.model_dump_json().encode())

    def _drop(self, key: str) -> None:
        entry = self.manifest.entries.pop(key)
        del self.lru[key]
        if entry.name in self.sizes:
            # This is a derivable cache, not transactional state: a crash before
            # publishing the reduced manifest may leave a stale reference/miss.
            remove_owned(self.settings.path / entry.name)
            self._bytes_used -= self.sizes.pop(entry.name)
            self.changed = True
        self.evictions += 1
        self.dirty = True

    def _collect_orphans(self) -> None:
        for orphan in self.orphans:
            remove_owned(self.settings.path / orphan)
            self._bytes_used -= self.sizes.pop(orphan)
            self.changed = True
        self.orphans.clear()

    def put(self, record: Record) -> None:
        previous = self.manifest.entries.get(record.key)
        if previous is not None:
            existing = self.read(record.key, record.created)
            if existing is not None and existing == record.model_copy(update={"created": existing.created}):
                return
        plain = record.model_dump_json().encode()
        if len(plain) > MAX_PLAIN:
            return
        try:
            ciphertext = encrypt(self.cipher, plain)
        except InvalidCache:
            return
        if len(ciphertext) > self.settings.shard_bytes:
            return
        if not manifest_fits(1) or len(ciphertext) + manifest_cipher_bound(1) > self.settings.budget_bytes:
            return
        name = "shard-" + hmac.new(self.secret, plain, hashlib.sha256).hexdigest()[:32] + ".enc"
        path = self.settings.path
        safe_path(path)
        if not path.exists():
            path.mkdir(parents=True, mode=0o700)
        if not self._prepared:
            if inventory(path) != self.sizes:
                raise InvalidCache()
            self._collect_orphans()
            self._prepared = True
        entry = Entry(name=name, kind=record.kind, created=record.created, touched=record.created)
        while True:
            count = len(self.manifest.entries) + (record.key not in self.manifest.entries)
            needed = (0 if name in self.sizes else len(ciphertext)) + manifest_cipher_bound(count)
            if manifest_fits(count) and self.bytes_used + needed <= self.settings.budget_bytes:
                break
            if self.orphans:
                self._collect_orphans()
                continue
            if not self.lru:
                return
            oldest = next(iter(self.lru))
            if oldest == record.key and len(self.lru) > 1:
                self.lru.move_to_end(oldest)
                oldest = next(iter(self.lru))
            self._drop(oldest)
        if name not in self.sizes:
            atomic_write(path / name, ciphertext)
            self.sizes[name] = len(ciphertext)
            self._bytes_used += len(ciphertext)
            self.writes += 1
            self.changed = True
        elif decrypt(self.cipher, read_cipher(path / name)) != plain:
            raise InvalidCache()
        self.orphans.discard(name)
        if previous is not None and previous.name != name and previous.name in self.sizes:
            self.orphans.add(previous.name)
        self.manifest.entries[record.key] = entry
        self.lru[record.key] = None
        self.lru.move_to_end(record.key)
        self.dirty = True

    def publish_manifest(self) -> None:
        path = self.settings.path
        safe_path(path)
        if path.exists():
            if inventory(path) != self.sizes:
                raise InvalidCache()
        elif self.sizes:
            raise InvalidCache()
        if not self.dirty:
            return
        self._collect_orphans()
        # Admission already reserved a worst-case complete index and its temp.
        # Loaded manifests can exceed current writer limits after a budget change.
        while self.lru and (not manifest_fits(len(self.lru))
                            or self.bytes_used + manifest_cipher_bound(len(self.lru)) > self.settings.budget_bytes):
            self._drop(next(iter(self.lru)))
        data = self._manifest_bytes()
        if not manifest_fits(len(self.lru)) or self.bytes_used + len(data) > self.settings.budget_bytes:
            raise InvalidCache()
        atomic_write(self.settings.path / "manifest.enc", data)
        self._bytes_used += len(data) - self.sizes.get("manifest.enc", 0)
        self.sizes["manifest.enc"] = len(data)
        self.writes += 1
        self.changed = True
        self.dirty = False
