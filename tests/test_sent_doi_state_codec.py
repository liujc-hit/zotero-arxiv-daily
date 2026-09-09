"""Plaintext codec and schema-migration contracts for sent-paper state."""

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Final

from cryptography.fernet import Fernet
import pytest

from zotero_arxiv_daily.sent_doi_state import (
    FernetSentDoiStateStore,
    InvalidSentDoiStateError,
    SentDoiStateWriteError,
)
from zotero_arxiv_daily.sent_doi_state_codec import JsonValue, decode_sent_state


_DOI_A: Final = "10.1000/a"
_DOI_B: Final = "10.1000/b"
_DOI_IDENTITY_A: Final = f"doi:{_DOI_A}"
_DOI_IDENTITY_B: Final = f"doi:{_DOI_B}"
_ARXIV_IDENTITY: Final = "arxiv:2401.12345"
_SENSITIVE_MARKER: Final = "sensitive-key-or-state-marker"


@pytest.mark.parametrize(
    "invalid_identity",
    [
        pytest.param("not-an-identity", id="invalid-bare-value"),
        pytest.param("unknown:private-paper", id="unknown-namespace"),
        pytest.param(
            "doi:HTTPS://DOI.ORG/10.1000/C",
            id="noncanonical-namespaced-doi",
        ),
    ],
)
def test_invalid_save_is_all_or_nothing_before_encryption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_identity: str,
) -> None:
    # Given: existing good ciphertext and a mixed valid/invalid replacement.
    key = Fernet.generate_key()
    path = tmp_path / "state.bin"
    store = FernetSentDoiStateStore(path, key)
    store.save({_DOI_IDENTITY_A})
    previous_ciphertext = path.read_bytes()
    encrypt_called = False
    replace_called = False

    def unexpected_encrypt(_self: Fernet, _data: bytes) -> bytes:
        nonlocal encrypt_called
        encrypt_called = True
        raise AssertionError("invalid state reached encryption")

    def unexpected_replace(_source: str | Path, _destination: str | Path) -> None:
        nonlocal replace_called
        replace_called = True
        raise AssertionError("invalid state reached replacement")

    monkeypatch.setattr(Fernet, "encrypt", unexpected_encrypt)
    monkeypatch.setattr(os, "replace", unexpected_replace)

    # When: any replacement item is invalid.
    with pytest.raises(SentDoiStateWriteError) as caught:
        store.save({_DOI_IDENTITY_B, invalid_identity})

    # Then: the complete save is rejected before encryption or filesystem mutation.
    assert path.read_bytes() == previous_ciphertext
    assert encrypt_called is False
    assert replace_called is False
    assert str(caught.value) == "encrypted sent DOI state could not be saved"
    assert caught.value.__dict__ == {}
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_authenticated_legacy_v1_loads_namespaced_dois_without_rewriting(
    tmp_path: Path,
) -> None:
    # Given: authenticated deployed v1 plaintext with canonical sorted DOIs.
    key = Fernet.generate_key()
    path = tmp_path / "state.bin"
    ciphertext = Fernet(key).encrypt(
        json.dumps({"version": 1, "dois": [_DOI_A, _DOI_B]}).encode()
    )
    _ = path.write_bytes(ciphertext)
    store = FernetSentDoiStateStore(path, key)

    # When: the legacy snapshot is loaded.
    loaded = store.load()

    # Then: every DOI becomes its lossless identity and load does not migrate on disk.
    assert loaded == frozenset({_DOI_IDENTITY_A, _DOI_IDENTITY_B})
    assert path.read_bytes() == ciphertext


def test_legacy_v1_union_with_arxiv_saves_complete_v2_snapshot(tmp_path: Path) -> None:
    # Given: authenticated v1 state containing multiple legacy DOI values.
    key = Fernet.generate_key()
    path = tmp_path / "state.bin"
    _ = path.write_bytes(
        Fernet(key).encrypt(
            json.dumps({"version": 1, "dois": [_DOI_A, _DOI_B]}).encode()
        )
    )
    store = FernetSentDoiStateStore(path, key)

    # When: loaded identities are unioned with arXiv state and explicitly saved.
    store.save(store.load() | {_ARXIV_IDENTITY})

    # Then: v2 preserves both migrated DOIs and the new namespaced identity.
    expected = frozenset({_ARXIV_IDENTITY, _DOI_IDENTITY_A, _DOI_IDENTITY_B})
    plaintext = Fernet(key).decrypt(path.read_bytes()).decode()
    assert json.loads(plaintext) == {
        "version": 2,
        "identities": sorted(expected),
    }
    assert FernetSentDoiStateStore(path, key).load() == expected


@pytest.mark.parametrize(
    "plaintext",
    [
        pytest.param(b"\xff", id="invalid-utf8"),
        pytest.param(b"{", id="invalid-json"),
        pytest.param(b"[]", id="root-schema"),
        pytest.param(b'{"version":3,"identities":[]}', id="unknown-version"),
        pytest.param(b'{"version":true,"dois":[]}', id="version-type"),
        pytest.param(b'{"version":1}', id="missing-field"),
        pytest.param(b'{"version":1,"dois":[],"extra":true}', id="extra-field"),
        pytest.param(
            b'{"version":1,"dois":[],"identities":[]}',
            id="v1-mixed-fields",
        ),
        pytest.param(
            b'{"version":1,"version":1,"dois":[]}',
            id="duplicate-field",
        ),
        pytest.param(b'{"version":1,"dois":"invalid"}', id="dois-schema"),
        pytest.param(b'{"version":1,"dois":[7]}', id="doi-type"),
        pytest.param(
            b'{"version":1,"dois":["HTTPS://DOI.ORG/10.1000/A"]}',
            id="noncanonical-doi",
        ),
        pytest.param(
            b'{"version":1,"dois":["10.1000/b","10.1000/a"]}',
            id="unsorted-dois",
        ),
        pytest.param(
            b'{"version":1,"dois":["10.1000/a","10.1000/a"]}',
            id="duplicate-dois",
        ),
        pytest.param(b'{"version":2}', id="v2-missing-field"),
        pytest.param(
            b'{"version":2,"identities":[],"extra":true}',
            id="v2-extra-field",
        ),
        pytest.param(
            b'{"version":2,"identities":[],"dois":[]}',
            id="v2-mixed-fields",
        ),
        pytest.param(
            b'{"version":2,"identities":[],"identities":[]}',
            id="v2-duplicate-field",
        ),
        pytest.param(
            b'{"version":2,"identities":"invalid"}',
            id="identities-schema",
        ),
        pytest.param(b'{"version":2,"identities":[7]}', id="identity-type"),
        pytest.param(
            b'{"version":2,"identities":["doi:HTTPS://DOI.ORG/10.1000/A"]}',
            id="noncanonical-identity",
        ),
        pytest.param(
            b'{"version":2,"identities":["unknown:private-paper"]}',
            id="unknown-identity-namespace",
        ),
        pytest.param(
            b'{"version":2,"identities":["doi:10.1000/b","arxiv:2401.12345"]}',
            id="unsorted-identities",
        ),
        pytest.param(
            b'{"version":2,"identities":["doi:10.1000/a","doi:10.1000/a"]}',
            id="duplicate-identities",
        ),
    ],
)
def test_authenticated_malformed_plaintext_raises_one_redacted_error(
    tmp_path: Path,
    plaintext: bytes,
) -> None:
    # Given: authenticated ciphertext containing malformed state plaintext.
    key = Fernet.generate_key()
    path = tmp_path / "state.bin"
    _ = path.write_bytes(Fernet(key).encrypt(plaintext))
    store = FernetSentDoiStateStore(path, key)

    # When: strict state parsing reaches the malformed value.
    with pytest.raises(InvalidSentDoiStateError) as caught:
        _ = store.load()

    # Then: every format failure has the same static observable surface.
    assert str(caught.value) == "encrypted sent DOI state is invalid"
    assert caught.value.__dict__ == {}
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert _DOI_A not in repr(caught.value)


@pytest.mark.parametrize("failure_type", [ValueError, RecursionError])
def test_json_loader_failure_has_no_exception_context(
    failure_type: type[ValueError] | type[RecursionError],
) -> None:
    # Given: a JSON boundary that fails with sensitive exception content.
    def failing_loader(
        s: str,
        *,
        object_pairs_hook: Callable[
            [list[tuple[str, JsonValue]]],
            dict[str, JsonValue],
        ],
    ) -> JsonValue:
        del s, object_pairs_hook
        raise failure_type(_SENSITIVE_MARKER)

    # When: authenticated plaintext reaches that JSON boundary.
    with pytest.raises(InvalidSentDoiStateError) as caught:
        _ = decode_sent_state(b"{}", failing_loader)

    # Then: no source exception or sensitive value remains attached.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert _SENSITIVE_MARKER not in repr(caught.value)
