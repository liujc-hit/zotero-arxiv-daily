"""Owned-file operations with encrypted-only atomic replacement."""
import os
from pathlib import Path
import re
import stat
from typing import Final
from uuid import uuid4

from .codec import InvalidCache, MAX_CIPHER

OWNED: Final = re.compile(r"^(manifest|shard-[0-9a-f]{32})\.enc$")


def safe_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise InvalidCache()


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o444:
        raise InvalidCache()
    return info


def inventory(path: Path) -> dict[str, int]:
    safe_path(path)
    if not path.is_dir() or not path.stat().st_mode & 0o222:
        raise InvalidCache()
    result: dict[str, int] = {}
    for item in path.iterdir():
        if not OWNED.fullmatch(item.name):
            raise InvalidCache()
        result[item.name] = regular(item).st_size
    return result


def read_cipher(path: Path) -> bytes:
    safe_path(path)
    if regular(path).st_size > MAX_CIPHER:
        raise InvalidCache()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        data = stream.read(MAX_CIPHER + 1)
    if len(data) > MAX_CIPHER:
        raise InvalidCache()
    return data


def atomic_write(path: Path, data: bytes) -> None:
    safe_path(path)
    safe_path(path.parent)
    if path.exists():
        _ = regular(path)
    temp = path.parent / ("shard-" + uuid4().hex + ".enc")
    try:
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
            _ = stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temp.exists():
            temp.unlink()


def remove_owned(path: Path) -> None:
    safe_path(path)
    if not OWNED.fullmatch(path.name):
        raise InvalidCache()
    _ = regular(path)
    path.unlink()
