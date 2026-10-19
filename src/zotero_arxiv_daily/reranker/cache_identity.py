"""Private, revision-scoped embedding identity (never persisted as metadata)."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from omegaconf import DictConfig, OmegaConf
from .cache_contracts import CacheStore, RankingBackend

if TYPE_CHECKING:
    from ..compute_cache import CacheValue


def encoder_identity(reranker: RankingBackend, cache: CacheStore) -> CacheValue:
    """Return only an explicit built-in identity; custom implementations bypass."""
    config = getattr(reranker, "config", None)
    if not isinstance(config, DictConfig):
        return None
    revision = OmegaConf.select(config, "compute_cache.model_revision")
    if not isinstance(revision, str) or not revision.strip():
        return None
    backend = reranker._cache_backend()
    if backend is None:
        return None
    local = backend == "local"
    cfg = config.reranker[backend]
    packages = ("numpy", "sentence-transformers", "transformers", "tokenizers", "torch") if local else ("numpy", "openai")
    versions: dict[str, CacheValue] = {}
    for package in packages:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    identity: dict[str, CacheValue] = {
        "backend": backend, "model": str(cfg.model),
        "revision": revision, "versions": versions,
    }
    if local:
        # Use the actual kwargs frozen by LocalReranker.__init__, not a stale
        # projection of config that the encoder no longer consumes.
        identity["encode_kwargs"] = reranker._cache_encode_options()
        identity["trust_remote_code"] = True
    else:
        endpoint = str(cfg.base_url)
        url = urlsplit(endpoint)
        identity["endpoint"] = urlunsplit((
            url.scheme.lower(), url.netloc.rsplit("@", 1)[-1].lower(),
            url.path, "", "",
        ))
        # Query/userinfo may select a different deployment. Scope them together
        # with the credential via the store HMAC, never a public hash or metadata.
        identity["scope"] = cache.make_key("api-scope", [endpoint, str(cfg.key)])
    return identity
