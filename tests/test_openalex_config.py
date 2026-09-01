"""Focused tests for the OpenAlex source configuration.

Composes only the ``source.openalex`` subtree from the Hydra configs and
resolves its environment interpolations. Verifies resolved types, defaults,
and key order. No production code is exercised.
"""

from pathlib import Path

from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import OmegaConf

_CONFIG_DIR = str(Path(__file__).resolve().parent.parent / "config")

_OPENALEX_ENV_VARS = ("OPENALEX_API_KEY", "OPENALEX_API_KEY_2", "OPENALEX_ALLOW_ANONYMOUS")


def _resolved_openalex(monkeypatch, config_name="default", **env):
    """Compose a Hydra config and return the resolved source.openalex dict.

    Only the openalex subtree is resolved, so unrelated ``???`` placeholders
    and env interpolations elsewhere in the config are irrelevant. Env vars
    not passed are removed so "missing" is deterministic.
    """
    for name in _OPENALEX_ENV_VARS:
        if name in env:
            monkeypatch.setenv(name, env[name])
        else:
            monkeypatch.delenv(name, raising=False)
    GlobalHydra.instance().clear()
    with initialize_config_dir(config_dir=_CONFIG_DIR, version_base=None):
        cfg = compose(config_name=config_name)
    subtree = OmegaConf.select(cfg, "source.openalex")
    container = OmegaConf.to_container(subtree, resolve=True)
    assert isinstance(container, dict)  # source.openalex is a mapping, never a list/null
    GlobalHydra.instance().clear()
    return container


def test_base_defaults_are_empty_keys_opt_out_anonymous_and_one_day(monkeypatch):
    container = _resolved_openalex(monkeypatch, config_name="base")

    # Exact dict: also proves no legacy singular api_key field lingers.
    assert container == {"api_keys": [], "allow_anonymous": False, "lookback_days": 1}
    assert isinstance(container["api_keys"], list)


def test_custom_resolves_keys_in_declared_order_as_strings(monkeypatch):
    container = _resolved_openalex(
        monkeypatch, OPENALEX_API_KEY="first-key", OPENALEX_API_KEY_2="second-key"
    )

    assert container["api_keys"] == ["first-key", "second-key"]
    assert all(isinstance(key, str) for key in container["api_keys"])


def test_custom_decodes_allow_anonymous_string_into_bool(monkeypatch):
    true_container = _resolved_openalex(monkeypatch, OPENALEX_ALLOW_ANONYMOUS="true")
    false_container = _resolved_openalex(monkeypatch, OPENALEX_ALLOW_ANONYMOUS="false")

    assert true_container["allow_anonymous"] is True
    assert false_container["allow_anonymous"] is False


def test_missing_env_vars_stay_null_and_default_anonymous_to_false(monkeypatch):
    container = _resolved_openalex(monkeypatch)

    assert container["api_keys"] == [None, None]
    assert container["allow_anonymous"] is False
