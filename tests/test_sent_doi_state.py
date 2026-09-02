"""Focused contracts for authenticated sent-DOI state."""

import json
import os
import stat
from pathlib import Path
from typing import Final

from cryptography.fernet import Fernet
from omegaconf import DictConfig, OmegaConf
import pytest

from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.sent_doi_state import (
    DisabledSentDoiStateStore,
    FernetSentDoiStateStore,
    InvalidSentDoiStateConfigurationError,
    InvalidSentDoiStateError,
    SentDoiStateWriteError,
    build_sent_doi_state_store,
    filter_sent_doi_candidates,
    normalized_paper_dois,
)


_DOI_A: Final = "10.1000/a"
_DOI_B: Final = "10.1000/b"
_SENSITIVE_MARKER: Final = "sensitive-key-or-state-marker"

type ConfigValue = str | int | bool | None | dict[str, ConfigValue]


def _paper(title: str, doi: str | None) -> Paper:
    return Paper(
        source="fixture",
        title=title,
        authors=[],
        abstract="Abstract.",
        url=f"https://papers.example/{title}",
        doi=doi,
    )


def _enabled_config(path: Path, key: str) -> DictConfig:
    return OmegaConf.create(
        {
            "sent_doi_state": {
                "enabled": True,
                "path": str(path),
                "key": key,
            }
        }
    )


@pytest.mark.parametrize(
    "root",
    [
        pytest.param({}, id="absent"),
        pytest.param({"sent_doi_state": None}, id="null"),
        pytest.param({"sent_doi_state": "invalid"}, id="non-mapping"),
        pytest.param({"sent_doi_state": {}}, id="missing-enabled"),
        pytest.param(
            {"sent_doi_state": {"enabled": False}},
            id="false",
        ),
        pytest.param({"sent_doi_state": {"enabled": 1}}, id="integer"),
        pytest.param(
            {"sent_doi_state": {"enabled": "true"}},
            id="string",
        ),
        pytest.param(
            {
                "sent_doi_state": {
                    "enabled": False,
                    "path": "${missing.path}",
                    "key": "${missing.key}",
                }
            },
            id="disabled-unresolved-values",
        ),
    ],
)
def test_non_exact_opt_in_builds_disabled_no_op_store(
    root: dict[str, ConfigValue],
) -> None:
    # Given: state is absent or not enabled by the exact boolean opt-in.
    config = OmegaConf.create(root)

    # When: the optional store is built and used.
    store = build_sent_doi_state_store(config)
    store.save({_DOI_A})

    # Then: no subordinate values are required and no state is retained.
    assert isinstance(store, DisabledSentDoiStateStore)
    assert store.load() == frozenset()


def test_doi_helpers_preserve_candidate_identity_order_and_invalid_values() -> None:
    # Given: canonical, variant, unrelated, invalid, and missing DOI papers.
    sent = _paper("sent", " HTTPS://DOI.ORG/10.1000/A ")
    unrelated = _paper("unrelated", _DOI_B)
    invalid = _paper("invalid", "not-a-doi")
    missing = _paper("missing", None)
    papers = [sent, unrelated, invalid, missing]

    # When: sent candidates are filtered and valid DOI values are extracted.
    candidates = filter_sent_doi_candidates(papers, {_DOI_A})
    normalized = normalized_paper_dois(papers)

    # Then: only the normalized match is removed without mutation or reordering.
    assert candidates == [unrelated, invalid, missing]
    assert tuple(map(id, candidates)) == (id(unrelated), id(invalid), id(missing))
    assert sent.doi == " HTTPS://DOI.ORG/10.1000/A "
    assert normalized == frozenset({_DOI_A, _DOI_B})


@pytest.mark.parametrize(
    ("path_value", "key_value"),
    [
        pytest.param(None, Fernet.generate_key().decode(), id="missing-path"),
        pytest.param("   ", Fernet.generate_key().decode(), id="blank-path"),
        pytest.param(7, Fernet.generate_key().decode(), id="path-type"),
        pytest.param("state.bin", None, id="missing-key"),
        pytest.param("state.bin", "   ", id="blank-key"),
        pytest.param("state.bin", _SENSITIVE_MARKER, id="invalid-key"),
    ],
)
def test_enabled_invalid_configuration_raises_static_redacted_error(
    tmp_path: Path,
    path_value: str | int | None,
    key_value: str | None,
) -> None:
    # Given: exact opt-in with a malformed required value.
    selected_path: str | int | None = path_value
    if path_value == "state.bin":
        selected_path = str(tmp_path / "state.bin")
    config = OmegaConf.create(
        {
            "sent_doi_state": {
                "enabled": True,
                "path": selected_path,
                "key": key_value,
            }
        }
    )

    # When: enabled configuration is parsed.
    with pytest.raises(InvalidSentDoiStateConfigurationError) as caught:
        _ = build_sent_doi_state_store(config)

    # Then: the typed failure retains no supplied material or exception detail.
    observable = f"{caught.value!s}\n{caught.value!r}\n{caught.value.__dict__}"
    assert str(caught.value) == "enabled sent DOI state configuration is invalid"
    assert caught.value.__dict__ == {}
    assert _SENSITIVE_MARKER not in observable


def test_enabled_unknown_field_is_rejected(tmp_path: Path) -> None:
    # Given: valid enabled settings plus an unsupported field.
    config = _enabled_config(tmp_path / "state.bin", Fernet.generate_key().decode())
    OmegaConf.update(
        config,
        "sent_doi_state.unexpected",
        _SENSITIVE_MARKER,
        force_add=True,
    )

    # When / Then: strict parsing rejects the entire enabled subtree.
    with pytest.raises(InvalidSentDoiStateConfigurationError):
        _ = build_sent_doi_state_store(config)


def test_missing_ciphertext_loads_empty(tmp_path: Path) -> None:
    # Given: a valid store whose local state file does not exist.
    store = FernetSentDoiStateStore(tmp_path / "missing.bin", Fernet.generate_key())

    # When / Then: missing state is the one accepted empty-state case.
    assert store.load() == frozenset()


def test_save_writes_fsynced_owner_only_ciphertext_and_round_trips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: duplicate DOI variants, an invalid value, and an fsync recorder.
    key = Fernet.generate_key()
    path = tmp_path / "sent-dois.bin"
    store = build_sent_doi_state_store(_enabled_config(path, key.decode()))
    fsync_calls: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(file_descriptor: int) -> None:
        fsync_calls.append(file_descriptor)
        real_fsync(file_descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    # When: state is saved through the encrypted store.
    store.save({_DOI_B, "https://doi.org/10.1000/A", "10.1000/A", "invalid"})

    # Then: only sorted unique canonical values exist in authenticated ciphertext.
    assert isinstance(store, FernetSentDoiStateStore)
    assert key.decode() not in repr(store)
    ciphertext = path.read_bytes()
    assert json.loads(Fernet(key).decrypt(ciphertext).decode()) == {
        "version": 1,
        "dois": [_DOI_A, _DOI_B],
    }
    assert _DOI_A.encode() not in ciphertext
    assert _DOI_B.encode() not in ciphertext
    assert fsync_calls
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.load() == frozenset({_DOI_A, _DOI_B})


@pytest.mark.parametrize(
    "plaintext",
    [
        pytest.param(b"\xff", id="invalid-utf8"),
        pytest.param(b"{", id="invalid-json"),
        pytest.param(b"[]", id="root-schema"),
        pytest.param(b'{"version":2,"dois":[]}', id="version"),
        pytest.param(b'{"version":1}', id="missing-field"),
        pytest.param(b'{"version":1,"dois":[],"extra":true}', id="extra-field"),
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
    assert _DOI_A not in repr(caught.value)


def test_invalid_or_wrong_key_token_raises_redacted_state_error(tmp_path: Path) -> None:
    # Given: ciphertext authenticated by a different valid Fernet key.
    path = tmp_path / "state.bin"
    token = Fernet(Fernet.generate_key()).encrypt(_SENSITIVE_MARKER.encode())
    _ = path.write_bytes(token)
    store = FernetSentDoiStateStore(path, Fernet.generate_key())

    # When: authentication fails before plaintext parsing.
    with pytest.raises(InvalidSentDoiStateError) as caught:
        _ = store.load()

    # Then: neither token nor exception details reach the typed error surface.
    observable = f"{caught.value!s}\n{caught.value!r}\n{caught.value.__dict__}"
    assert token.decode() not in observable
    assert _SENSITIVE_MARKER not in observable


def test_replace_failure_preserves_previous_file_and_temp_is_ciphertext_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: existing encrypted state and a failing atomic replacement boundary.
    key = Fernet.generate_key()
    path = tmp_path / "state.bin"
    store = FernetSentDoiStateStore(path, key)
    store.save({_DOI_A})
    previous_ciphertext = path.read_bytes()
    replacement_tokens: list[bytes] = []

    def failing_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.parent == destination_path.parent == path.parent
        replacement_tokens.append(source_path.read_bytes())
        raise OSError(_SENSITIVE_MARKER)

    monkeypatch.setattr(os, "replace", failing_replace)

    # When: saving replacement state fails at the atomic swap.
    with pytest.raises(SentDoiStateWriteError) as caught:
        store.save({_DOI_B})

    # Then: the old file remains and the temp file never held plaintext.
    assert path.read_bytes() == previous_ciphertext
    assert len(replacement_tokens) == 1
    assert _DOI_B.encode() not in replacement_tokens[0]
    assert json.loads(Fernet(key).decrypt(replacement_tokens[0]).decode()) == {
        "version": 1,
        "dois": [_DOI_B],
    }
    observable = f"{caught.value!s}\n{caught.value!r}\n{caught.value.__dict__}"
    assert str(caught.value) == "encrypted sent DOI state could not be saved"
    assert _SENSITIVE_MARKER not in observable
