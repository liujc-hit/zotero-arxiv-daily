"""Local first-run path contracts for the encrypted sent-DOI store.

These tests pin the on-disk path bootstrap for ``FernetSentDoiStateStore``: a
first-run ``load()`` must create the nested parent directory before the SMTP
step reads state, a subsequent ``save()``/``load()`` round-trips through the
newly created directory, an ``OSError`` raised while creating the directory is
converted to the static redacted ``SentDoiStateWriteError``, the missing-file
case still returns ``frozenset()``, other read failures remain fail-closed and
redacted, and the local ``.state/`` working directory is ignored by git.
"""

import errno
from pathlib import Path
from typing import Final

from cryptography.fernet import Fernet
import pytest

from zotero_arxiv_daily.sent_doi_state import (
    FernetSentDoiStateStore,
    InvalidSentDoiStateError,
    SentDoiStateWriteError,
)

_DOI_A: Final = "10.1000/a"
_DOI_B: Final = "10.1000/b"
_REPO_ROOT: Final = Path(__file__).resolve().parent.parent
_GITIGNORE_PATH: Final = _REPO_ROOT / ".gitignore"


def test_load_creates_missing_nested_parent_before_first_read(
    tmp_path: Path,
) -> None:
    # Given: an enabled store whose parent directory chain is absent.
    nested_path = tmp_path / ".state" / "nested" / "sent-dois.bin"
    assert not nested_path.parent.exists()
    store = FernetSentDoiStateStore(nested_path, Fernet.generate_key())

    # When: state is loaded for the first time, before any save.
    loaded = store.load()

    # Then: every missing directory in the chain now exists on disk.
    assert loaded == frozenset()
    assert nested_path.parent.is_dir()
    assert nested_path.parent.parent.is_dir()
    assert nested_path.parent.parent.parent.is_dir()


def test_save_then_load_round_trips_through_newly_created_parent(
    tmp_path: Path,
) -> None:
    # Given: an enabled store pointed at a nested path that does not yet exist.
    key = Fernet.generate_key()
    nested_path = tmp_path / ".state" / "nested" / "sent-dois.bin"
    store = FernetSentDoiStateStore(nested_path, key)
    assert store.load() == frozenset()

    # When: state is saved through the encrypted store after bootstrap.
    store.save({_DOI_A, _DOI_B})

    # Then: the file is written inside the freshly created parent and the
    # canonical DOIs round-trip through a fresh store reading the same path.
    assert nested_path.is_file()
    other_store = FernetSentDoiStateStore(nested_path, key)
    assert other_store.load() == frozenset({_DOI_A, _DOI_B})


def test_load_mkdir_failure_raises_static_redacted_write_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a path whose parent cannot be created and a real mkdir boundary.
    blocked_path = tmp_path / "blocked" / "sent-dois.bin"
    store = FernetSentDoiStateStore(blocked_path, Fernet.generate_key())
    sentinel = errno.EACCES

    def failing_mkdir(
        _self: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        del mode, parents, exist_ok
        raise OSError(sentinel, "denied for test")

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)

    # When: load() attempts to bootstrap the missing parent.
    with pytest.raises(SentDoiStateWriteError) as caught:
        _ = store.load()

    # Then: the typed failure retains no OS-level detail or path material.
    observable = f"{caught.value!s}\n{caught.value!r}\n{caught.value.__dict__}"
    assert str(caught.value) == "encrypted sent DOI state could not be saved"
    assert caught.value.__dict__ == {}
    assert str(sentinel) not in observable
    assert str(blocked_path) not in observable


def test_load_still_returns_empty_for_missing_file_when_parent_exists(
    tmp_path: Path,
) -> None:
    # Given: a store whose parent directory exists but state file does not.
    parent = tmp_path / ".state"
    parent.mkdir()
    store = FernetSentDoiStateStore(parent / "sent-dois.bin", Fernet.generate_key())

    # When / Then: the missing-file branch is unchanged and still empty.
    assert store.load() == frozenset()


def test_load_non_missing_read_failure_raises_static_redacted_state_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an existing parent whose state read fails for a reason other than ENOENT.
    parent = tmp_path / ".state"
    parent.mkdir()
    state_path = parent / "sent-dois.bin"
    store = FernetSentDoiStateStore(state_path, Fernet.generate_key())
    sensitive_detail = "private-read-failure-detail"

    def failing_read_bytes(_self: Path) -> bytes:
        raise OSError(errno.EIO, sensitive_detail)

    monkeypatch.setattr(Path, "read_bytes", failing_read_bytes)

    # When: load() reaches the failing local read boundary.
    with pytest.raises(InvalidSentDoiStateError) as caught:
        _ = store.load()

    # Then: the typed failure retains no path or OS-level detail.
    observable = f"{caught.value!s}\n{caught.value!r}\n{caught.value.__dict__}"
    assert str(caught.value) == "encrypted sent DOI state is invalid"
    assert caught.value.__cause__ is None
    assert caught.value.__dict__ == {}
    assert sensitive_detail not in observable
    assert str(state_path) not in observable


def test_gitignore_lists_state_directory() -> None:
    # Given: the repository's gitignore file at a fixed location.
    assert _GITIGNORE_PATH.is_file()

    # When: the file is read line by line ignoring comments and blanks.
    raw_lines = _GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    patterns = {
        line.strip()
        for line in raw_lines
        if line.strip() and not line.lstrip().startswith("#")
    }

    # Then: the exact ``.state/`` entry is present.
    assert ".state/" in patterns
