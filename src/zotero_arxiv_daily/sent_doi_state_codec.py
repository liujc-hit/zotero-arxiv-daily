"""Strict plaintext codec for authenticated sent paper-identity state."""

import json
from collections.abc import Callable, Collection
from typing import ClassVar, Final, Literal, Never, Protocol, TypedDict

from .identifiers import normalize_doi
from .paper_identity import PaperIdentity, normalize_paper_identity


_LEGACY_STATE_VERSION: Final = 1
_STATE_VERSION: Final = 2

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)


class _LegacyStatePayload(TypedDict):
    version: Literal[1]
    dois: list[str]


class _StatePayload(TypedDict):
    version: Literal[2]
    identities: list[PaperIdentity]


class JsonLoader(Protocol):
    def __call__(
        self,
        s: str,
        *,
        object_pairs_hook: Callable[
            [list[tuple[str, JsonValue]]],
            dict[str, JsonValue],
        ],
    ) -> JsonValue: ...


class InvalidSentDoiStateError(ValueError):
    """Report unauthentic or malformed state without retaining its contents."""

    message: ClassVar[str] = "encrypted sent DOI state is invalid"

    def __init__(self) -> None:
        super().__init__(self.message)


class SentDoiStateWriteError(RuntimeError):
    """Report an invalid or failed save without retaining supplied values."""

    message: ClassVar[str] = "encrypted sent DOI state could not be saved"

    def __init__(self) -> None:
        super().__init__(self.message)


class _DuplicateJsonKeyError(ValueError):
    """Mark a JSON object that cannot satisfy the strict state schema."""


def _invalid_state() -> Never:
    raise InvalidSentDoiStateError from None


def _invalid_write() -> Never:
    raise SentDoiStateWriteError from None


def _reject_duplicate_keys(
    pairs: list[tuple[str, JsonValue]],
) -> dict[str, JsonValue]:
    parsed: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in parsed:
            raise _DuplicateJsonKeyError from None
        parsed[key] = value
    return parsed


def _sorted_unique_strings(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        _invalid_state()
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _invalid_state()
        strings.append(item)
    if strings != sorted(set(strings)):
        _invalid_state()
    return strings


def _parse_legacy_payload(
    payload: dict[str, JsonValue],
) -> frozenset[PaperIdentity]:
    if set(payload) != {"version", "dois"}:
        _invalid_state()
    canonical_dois = _sorted_unique_strings(payload["dois"])
    if any(normalize_doi(doi) != doi for doi in canonical_dois):
        _invalid_state()
    legacy_payload: _LegacyStatePayload = {
        "version": _LEGACY_STATE_VERSION,
        "dois": canonical_dois,
    }
    return frozenset(
        PaperIdentity(f"doi:{doi}") for doi in legacy_payload["dois"]
    )


def _parse_current_payload(
    payload: dict[str, JsonValue],
) -> frozenset[PaperIdentity]:
    if set(payload) != {"version", "identities"}:
        _invalid_state()
    raw_identities = _sorted_unique_strings(payload["identities"])
    canonical_identities: list[PaperIdentity] = []
    for raw_identity in raw_identities:
        identity = normalize_paper_identity(raw_identity)
        if identity is None or identity != raw_identity:
            _invalid_state()
        canonical_identities.append(identity)
    state_payload: _StatePayload = {
        "version": _STATE_VERSION,
        "identities": canonical_identities,
    }
    return frozenset(state_payload["identities"])


def decode_sent_state(
    plaintext: bytes,
    loader: JsonLoader,
) -> frozenset[PaperIdentity]:
    """Decode one authenticated plaintext using the strict versioned schema."""
    decoded = ""
    decode_failed = False
    try:
        decoded = plaintext.decode("utf-8")
    except UnicodeDecodeError:
        decode_failed = True
    if decode_failed:
        _invalid_state()

    payload: JsonValue = None
    load_failed = False
    try:
        payload = loader(decoded, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, RecursionError):
        load_failed = True
    if load_failed:
        _invalid_state()

    if not isinstance(payload, dict) or "version" not in payload:
        _invalid_state()
    version = payload["version"]
    if type(version) is not int:
        _invalid_state()
    if version == _LEGACY_STATE_VERSION:
        return _parse_legacy_payload(payload)
    if version == _STATE_VERSION:
        return _parse_current_payload(payload)
    _invalid_state()


def encode_sent_state(identities: Collection[str]) -> bytes:
    """Encode a complete valid identity snapshot as deterministic v2 plaintext."""
    canonical_identities: set[PaperIdentity] = set()
    for value in identities:
        identity = normalize_paper_identity(value)
        if identity is None:
            if value.startswith(("doi:", "arxiv:", "url:")):
                _invalid_write()
            legacy_doi = normalize_doi(value)
            if legacy_doi is None:
                _invalid_write()
            identity = PaperIdentity(f"doi:{legacy_doi}")
        canonical_identities.add(identity)
    payload: _StatePayload = {
        "version": _STATE_VERSION,
        "identities": sorted(canonical_identities),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
