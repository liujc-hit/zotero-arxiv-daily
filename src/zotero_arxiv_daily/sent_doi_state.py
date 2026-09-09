"""Encrypted local persistence for authenticated paper-identity state."""

import json
import os
import tempfile
from collections.abc import Collection, Iterable
from contextlib import suppress
from pathlib import Path
from typing import ClassVar, Final, Never, Protocol, TypeVar, final

from cryptography.fernet import Fernet, InvalidToken
from omegaconf import DictConfig, ListConfig, OmegaConf
from omegaconf.errors import OmegaConfBaseException

from .identifiers import normalize_doi
from .paper_identity import PaperIdentity
from .sent_doi_state_codec import (
    InvalidSentDoiStateError,
    SentDoiStateWriteError,
    decode_sent_state,
    encode_sent_state,
)


_STATE_FILE_MODE: Final = 0o600
_CONFIG_FIELDS: Final = frozenset({"enabled", "path", "key"})

type _ConfigValue = str | int | float | bool | None | DictConfig | ListConfig

_DoiPaperT = TypeVar("_DoiPaperT", bound="_DoiPaper")


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


class SentDoiStateStore(Protocol):
    """Load and replace the complete set of sent canonical paper identities."""

    def load(self) -> frozenset[PaperIdentity]:
        """Load canonical namespaced paper identities."""
        ...

    def save(self, identities: Collection[str]) -> None:
        """Replace state with canonical namespaced paper identities."""
        ...


class InvalidSentDoiStateConfigurationError(ValueError):
    """Report malformed enabled settings without retaining supplied values."""

    message: ClassVar[str] = "enabled sent DOI state configuration is invalid"

    def __init__(self) -> None:
        super().__init__(self.message)


def _invalid_configuration() -> Never:
    raise InvalidSentDoiStateConfigurationError from None


def _invalid_state() -> Never:
    raise InvalidSentDoiStateError from None


def _invalid_write() -> Never:
    raise SentDoiStateWriteError from None


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
    value: _ConfigValue = None
    resolution_failed = False
    try:
        value = selector(section, key, throw_on_resolution_failure=True)
    except OmegaConfBaseException:
        resolution_failed = True
    if resolution_failed:
        _invalid_configuration()
    return value


def _normalized_dois(values: Iterable[str | None]) -> frozenset[str]:
    return frozenset(
        normalized
        for value in values
        if (normalized := normalize_doi(value)) is not None
    )


@final
class DisabledSentDoiStateStore:
    """No-op store used unless authenticated persistence is exactly enabled."""

    __slots__ = ()

    def load(self) -> frozenset[PaperIdentity]:
        return frozenset()

    def save(self, identities: Collection[str]) -> None:
        del identities


@final
class FernetSentDoiStateStore:
    """Persist one authenticated encrypted paper-identity snapshot atomically."""

    __slots__ = ("_fernet", "_path")

    _fernet: Fernet
    _path: Path

    def __init__(self, path: str | Path, key: str | bytes) -> None:
        path_value = str(path).strip()
        if not path_value:
            _invalid_configuration()
        fernet: Fernet | None = None
        invalid_key = False
        try:
            fernet = Fernet(key)
        except (TypeError, ValueError):
            invalid_key = True
        if invalid_key or fernet is None:
            _invalid_configuration()
        self._path = Path(path_value)
        self._fernet = fernet

    def load(self) -> frozenset[PaperIdentity]:
        mkdir_failed = False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            mkdir_failed = True
        if mkdir_failed:
            _invalid_write()

        ciphertext: bytes | None = None
        missing = False
        read_failed = False
        try:
            ciphertext = self._path.read_bytes()
        except FileNotFoundError:
            missing = True
        except OSError:
            read_failed = True
        if missing:
            return frozenset()
        if read_failed or ciphertext is None:
            _invalid_state()

        plaintext: bytes | None = None
        decrypt_failed = False
        try:
            plaintext = self._fernet.decrypt(ciphertext)
        except InvalidToken:
            decrypt_failed = True
        if decrypt_failed or plaintext is None:
            _invalid_state()
        return decode_sent_state(plaintext, json.loads)

    def save(self, identities: Collection[str]) -> None:
        plaintext = encode_sent_state(identities)
        ciphertext = self._fernet.encrypt(plaintext)

        write_failed = False
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
            write_failed = True
        if write_failed:
            _invalid_write()


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
    """Deprecated: keep papers whose valid DOI is absent from DOI-only state."""
    normalized_sent_dois = _normalized_dois(sent_dois)
    return [
        paper
        for paper in papers
        if (doi := normalize_doi(paper.doi)) is None or doi not in normalized_sent_dois
    ]


def normalized_paper_dois(papers: Iterable[_DoiPaper]) -> frozenset[str]:
    """Deprecated: extract canonical DOI values; use paper_identities instead."""
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
