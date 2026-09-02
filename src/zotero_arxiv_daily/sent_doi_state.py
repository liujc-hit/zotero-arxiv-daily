"""Authenticated local persistence for DOI delivery state."""

import json
import os
import tempfile
from collections.abc import Callable, Collection, Iterable
from contextlib import suppress
from pathlib import Path
from typing import ClassVar, Final, Never, Protocol, TypeVar, TypedDict, final

from cryptography.fernet import Fernet, InvalidToken
from omegaconf import DictConfig, ListConfig, OmegaConf
from omegaconf.errors import OmegaConfBaseException

from .identifiers import normalize_doi


_STATE_VERSION: Final = 1
_STATE_FILE_MODE: Final = 0o600
_CONFIG_FIELDS: Final = frozenset({"enabled", "path", "key"})

type _ConfigValue = str | int | float | bool | None | DictConfig | ListConfig
type _JsonValue = (
    str | int | float | bool | None | list[_JsonValue] | dict[str, _JsonValue]
)

_DoiPaperT = TypeVar("_DoiPaperT", bound="_DoiPaper")


class _StatePayload(TypedDict):
    version: int
    dois: list[str]


class _DoiPaper(Protocol):
    @property
    def doi(self) -> str | None: ...


class _ConfigSelector(Protocol):
    def __call__(
        self,
        cfg: DictConfig,
        key: str,
        *,
        default: _ConfigValue = None,
        throw_on_resolution_failure: bool = True,
    ) -> _ConfigValue: ...


class _JsonLoader(Protocol):
    def __call__(
        self,
        s: str,
        *,
        object_pairs_hook: Callable[
            [list[tuple[str, _JsonValue]]],
            dict[str, _JsonValue],
        ],
    ) -> _JsonValue: ...


class SentDoiStateStore(Protocol):
    """Load and replace the complete set of sent canonical DOIs."""

    def load(self) -> frozenset[str]: ...

    def save(self, dois: Collection[str]) -> None: ...


class InvalidSentDoiStateConfigurationError(ValueError):
    """Report malformed enabled settings without retaining supplied values."""

    message: ClassVar[str] = "enabled sent DOI state configuration is invalid"

    def __init__(self) -> None:
        super().__init__(self.message)


class InvalidSentDoiStateError(ValueError):
    """Report unauthentic or malformed state without retaining its contents."""

    message: ClassVar[str] = "encrypted sent DOI state is invalid"

    def __init__(self) -> None:
        super().__init__(self.message)


class SentDoiStateWriteError(RuntimeError):
    """Report a failed atomic save without retaining the OS failure."""

    message: ClassVar[str] = "encrypted sent DOI state could not be saved"

    def __init__(self) -> None:
        super().__init__(self.message)


class _DuplicateJsonKeyError(ValueError):
    """Mark a JSON object that cannot satisfy the strict state schema."""


def _invalid_configuration() -> Never:
    raise InvalidSentDoiStateConfigurationError from None


def _invalid_state() -> Never:
    raise InvalidSentDoiStateError from None


def _select_soft(
    selector: _ConfigSelector,
    section: DictConfig,
    key: str,
) -> _ConfigValue:
    return selector(
        section,
        key,
        default=None,
        throw_on_resolution_failure=False,
    )


def _read_required(
    selector: _ConfigSelector,
    section: DictConfig,
    key: str,
) -> _ConfigValue:
    if key not in section:
        _invalid_configuration()
    try:
        return selector(section, key, throw_on_resolution_failure=True)
    except OmegaConfBaseException:
        _invalid_configuration()


def _normalized_dois(values: Iterable[str | None]) -> frozenset[str]:
    return frozenset(
        normalized
        for value in values
        if (normalized := normalize_doi(value)) is not None
    )


def _reject_duplicate_keys(
    pairs: list[tuple[str, _JsonValue]],
) -> dict[str, _JsonValue]:
    parsed: dict[str, _JsonValue] = {}
    for key, value in pairs:
        if key in parsed:
            raise _DuplicateJsonKeyError from None
        parsed[key] = value
    return parsed


def _parse_plaintext(plaintext: bytes, loader: _JsonLoader) -> frozenset[str]:
    try:
        decoded = plaintext.decode("utf-8")
    except UnicodeDecodeError:
        _invalid_state()

    try:
        payload = loader(decoded, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, _DuplicateJsonKeyError):
        _invalid_state()

    if not isinstance(payload, dict) or set(payload) != {"version", "dois"}:
        _invalid_state()
    version = payload["version"]
    raw_dois = payload["dois"]
    if type(version) is not int or version != _STATE_VERSION:
        _invalid_state()
    if not isinstance(raw_dois, list):
        _invalid_state()

    canonical_dois: list[str] = []
    for raw_doi in raw_dois:
        if not isinstance(raw_doi, str) or normalize_doi(raw_doi) != raw_doi:
            _invalid_state()
        canonical_dois.append(raw_doi)
    if canonical_dois != sorted(set(canonical_dois)):
        _invalid_state()
    return frozenset(canonical_dois)


@final
class DisabledSentDoiStateStore:
    """No-op store used unless authenticated persistence is exactly enabled."""

    __slots__ = ()

    def load(self) -> frozenset[str]:
        return frozenset()

    def save(self, dois: Collection[str]) -> None:
        del dois


@final
class FernetSentDoiStateStore:
    """Persist one authenticated encrypted snapshot with atomic replacement."""

    __slots__ = ("_fernet", "_path")

    _fernet: Fernet
    _path: Path

    def __init__(self, path: str | Path, key: str | bytes) -> None:
        path_value = str(path).strip()
        if not path_value:
            _invalid_configuration()
        try:
            fernet = Fernet(key)
        except (TypeError, ValueError):
            _invalid_configuration()
        self._path = Path(path_value)
        self._fernet = fernet

    def load(self) -> frozenset[str]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise SentDoiStateWriteError from None
        try:
            ciphertext = self._path.read_bytes()
        except FileNotFoundError:
            return frozenset()
        except OSError:
            _invalid_state()
        try:
            plaintext = self._fernet.decrypt(ciphertext)
        except InvalidToken:
            _invalid_state()
        return _parse_plaintext(plaintext, json.loads)

    def save(self, dois: Collection[str]) -> None:
        payload: _StatePayload = {
            "version": _STATE_VERSION,
            "dois": sorted(_normalized_dois(dois)),
        }
        plaintext = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        ciphertext = self._fernet.encrypt(plaintext)

        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=self._path.parent,
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(file_descriptor, "wb") as temporary_file:
                    _ = temporary_file.write(ciphertext)
                    temporary_file.flush()
                    os.fsync(temporary_file.fileno())
                with suppress(NotImplementedError):
                    os.chmod(temporary_path, _STATE_FILE_MODE)
                os.replace(temporary_path, self._path)
            finally:
                with suppress(FileNotFoundError):
                    temporary_path.unlink()
        except OSError:
            raise SentDoiStateWriteError from None


def build_sent_doi_state_store(config: DictConfig) -> SentDoiStateStore:
    """Build encrypted state only for an exact, strictly valid opt-in."""
    section = _select_soft(OmegaConf.select, config, "sent_doi_state")
    if not isinstance(section, DictConfig):
        return DisabledSentDoiStateStore()
    enabled = _select_soft(OmegaConf.select, section, "enabled")
    if enabled is not True:
        return DisabledSentDoiStateStore()
    if any(not isinstance(name, str) or name not in _CONFIG_FIELDS for name in section):
        _invalid_configuration()

    path = _read_required(OmegaConf.select, section, "path")
    key = _read_required(OmegaConf.select, section, "key")
    if not isinstance(path, str) or not path.strip():
        _invalid_configuration()
    if not isinstance(key, str) or not key.strip():
        _invalid_configuration()
    return FernetSentDoiStateStore(path.strip(), key.strip())


def filter_sent_doi_candidates(
    papers: Iterable[_DoiPaperT],
    sent_dois: Collection[str],
) -> list[_DoiPaperT]:
    """Keep papers without a valid DOI already present in sent state."""
    normalized_sent_dois = _normalized_dois(sent_dois)
    return [
        paper
        for paper in papers
        if (doi := normalize_doi(paper.doi)) is None or doi not in normalized_sent_dois
    ]


def normalized_paper_dois(papers: Iterable[_DoiPaper]) -> frozenset[str]:
    """Extract unique canonical valid DOI values from papers."""
    return _normalized_dois(paper.doi for paper in papers)


__all__: Final[tuple[str, ...]] = (
    "DisabledSentDoiStateStore",
    "FernetSentDoiStateStore",
    "InvalidSentDoiStateConfigurationError",
    "InvalidSentDoiStateError",
    "SentDoiStateStore",
    "SentDoiStateWriteError",
    "build_sent_doi_state_store",
    "filter_sent_doi_candidates",
    "normalized_paper_dois",
)
