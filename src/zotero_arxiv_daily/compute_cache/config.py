"""OmegaConf-only optional cache activation, without environment access."""
from pathlib import Path
from typing import Protocol

from loguru import logger
from omegaconf import DictConfig, ListConfig, OmegaConf
from omegaconf.errors import OmegaConfBaseException
from pydantic import Field

from .cache import CACHE_ERRORS, ComputeCache
from .models import CacheSettings, StrictModel


class SettingsInput(StrictModel):
    enabled: bool = False
    key: str | None = Field(default=None, repr=False)
    path: str | Path = ".cache/zad-compute"
    budget_bytes: int = Field(default=8_000_000_000, gt=0, le=8_000_000_000)
    shard_bytes: int = Field(default=16_777_216, gt=0, le=16_777_216)
    ttl_days: int = Field(default=7, gt=0)
    model_revision: str | None = None


class Selector(Protocol):
    def __call__(self, cfg: DictConfig, key: str) -> DictConfig | ListConfig | str | int | float | bool | None: ...


def _select(cfg: DictConfig, key: str, *, selector: Selector = OmegaConf.select) -> DictConfig | ListConfig | str | int | float | bool | None:
    return selector(cfg, key)


def open_compute_cache(config: DictConfig | None) -> ComputeCache | None:
    if config is None or "compute_cache" not in config:
        _ = ComputeCache(CacheSettings())
        return None
    status_path = CacheSettings().path
    try:
        section = _select(config, "compute_cache")
        if not isinstance(section, DictConfig):
            _ = ComputeCache(CacheSettings())
            return None
        configured_path = _select(section, "path")
        if isinstance(configured_path, str):
            status_path = Path(configured_path)
        parsed = SettingsInput.model_validate(OmegaConf.to_container(section, resolve=True))
        settings = CacheSettings(enabled=parsed.enabled, key=parsed.key, path=Path(parsed.path),
                                 budget_bytes=parsed.budget_bytes, shard_bytes=parsed.shard_bytes,
                                 ttl_days=parsed.ttl_days, model_revision=parsed.model_revision)
        cache = ComputeCache(settings)
        return cache if cache.is_ready else None
    except (*CACHE_ERRORS, OmegaConfBaseException):
        logger.warning("Compute cache configuration unavailable; continuing without cached results")
        # Do not retain a stale successful default status after failed activation.
        _ = ComputeCache(CacheSettings(path=status_path))
        return None
