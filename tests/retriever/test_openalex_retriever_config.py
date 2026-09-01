"""Credential/config-construction contract tests for the OpenAlex retriever."""

from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import requests
from omegaconf import OmegaConf

import zotero_arxiv_daily.retriever.openalex_retriever as openalex_module
from zotero_arxiv_daily.retriever.openalex_client import MissingOpenAlexCredentialsError
from zotero_arxiv_daily.retriever.openalex_retriever import OpenAlexRetriever
from zotero_arxiv_daily.retriever.openalex_venue_catalog import VenueSpec

API_URL = "https://api.openalex.org/works"
PRIMARY_KEY = "test-primary-openalex-key"
SECONDARY_KEY = "test-secondary-openalex-key"
INVALID_CREDENTIAL_CONFIG_MESSAGE = "source.openalex credential configuration is invalid"


def _response(results=(), next_cursor=None, status_code=200):
    payload = {"results": list(results), "meta": {"next_cursor": next_cursor}}
    response = SimpleNamespace(status_code=status_code, headers={})
    response.json = lambda: payload
    return response


def _set_venues(monkeypatch, journals, conferences):
    monkeypatch.setattr(openalex_module, "JOURNAL_VENUES", tuple(journals), raising=False)
    monkeypatch.setattr(openalex_module, "CONFERENCE_VENUES", tuple(conferences), raising=False)
    monkeypatch.setattr(openalex_module, "ALL_VENUES", tuple(journals) + tuple(conferences), raising=False)


@pytest.fixture()
def single_venue(monkeypatch):
    _set_venues(monkeypatch, (VenueSpec("journal", "Journal", "journal", (), ("1111-1111",)),), ())


def test_openalex_defaults_are_inherited_from_base_config(config, monkeypatch):
    for env_var in ("OPENALEX_API_KEY", "OPENALEX_API_KEY_2", "OPENALEX_ALLOW_ANONYMOUS"):
        monkeypatch.delenv(env_var, raising=False)
    openalex_cfg = OmegaConf.select(config, "source.openalex")
    assert OmegaConf.to_container(openalex_cfg, resolve=True) == {
        "api_keys": [None, None],
        "allow_anonymous": False,
        "lookback_days": 1,
    }


@pytest.mark.parametrize("plain_list", [False, True], ids=["list-config", "plain-list"])
def test_credential_entries_are_normalized_in_order_before_client_construction(
    config, monkeypatch, plain_list
):
    # Given null and blank entries around two ordered credentials
    configured_entries = [None, f"  {PRIMARY_KEY}  ", "", "  ", SECONDARY_KEY]
    captured = []

    class RecordingClient:
        def __init__(self, api_keys, *, anonymous_fallback=False):
            captured.append((api_keys, anonymous_fallback))

    monkeypatch.setattr(openalex_module, "OpenAlexClient", RecordingClient, raising=False)
    config.source.openalex.allow_anonymous = True
    if plain_list:
        original_select = OmegaConf.select

        def select(cfg, key):
            if key == "source.openalex.api_keys":
                return configured_entries
            return original_select(cfg, key)

        monkeypatch.setattr(OmegaConf, "select", select)
    else:
        config.source.openalex.api_keys = configured_entries

    # When the retriever parses its Hydra boundary
    OpenAlexRetriever(config)

    # Then only normalized strings reach one client, in declared order
    assert captured == [((PRIMARY_KEY, SECONDARY_KEY), True)]


@pytest.mark.parametrize(
    ("api_keys", "allow_anonymous"),
    [
        pytest.param(PRIMARY_KEY, False, id="keys-not-list"),
        pytest.param([PRIMARY_KEY, 7], False, id="non-string-key"),
        pytest.param([PRIMARY_KEY], None, id="anonymous-null"),
        pytest.param([PRIMARY_KEY], "false", id="anonymous-string"),
        pytest.param([PRIMARY_KEY], 1, id="anonymous-integer"),
    ],
)
def test_invalid_credential_config_is_typed_static_and_redacted(
    config, api_keys, allow_anonymous
):
    # Given a malformed credential boundary that also contains a secret
    config.source.openalex.api_keys = api_keys
    config.source.openalex.allow_anonymous = allow_anonymous

    # When retriever construction parses the boundary
    with pytest.raises(TypeError) as caught:
        OpenAlexRetriever(config)

    # Then the custom error is static and cannot disclose credential text
    assert type(caught.value) is not TypeError
    assert str(caught.value) == INVALID_CREDENTIAL_CONFIG_MESSAGE
    assert PRIMARY_KEY not in f"{caught.value!s}\n{caught.value!r}"


def test_missing_keys_fail_closed_when_anonymous_is_disabled(config):
    # Given the custom-config shape produced by missing key environment variables
    config.source.openalex.api_keys = [None, None]
    config.source.openalex.allow_anonymous = False

    # When the enabled retriever constructs its request client
    with pytest.raises(MissingOpenAlexCredentialsError):
        OpenAlexRetriever(config)


def test_configured_keys_flow_to_bearer_failover_on_openalex_only(
    config, monkeypatch, single_venue
):
    # Given two configured identities and a primary-key rate-limit response
    config.source.openalex.api_keys = [PRIMARY_KEY, SECONDARY_KEY]
    config.source.openalex.allow_anonymous = False
    responses = iter((_response(status_code=429), _response()))
    calls = []

    def get(url, **kwargs):
        calls.append((url, dict(kwargs["params"]), dict(kwargs["headers"])))
        return next(responses)

    monkeypatch.setattr(requests, "get", get)

    # When retrieval requests the first page through its configured client
    assert OpenAlexRetriever(config)._retrieve_raw_papers() == []

    # Then 429 permanently advances Bearer identity without query-string auth
    assert [headers["Authorization"] for _, _, headers in calls] == [
        f"Bearer {PRIMARY_KEY}",
        f"Bearer {SECONDARY_KEY}",
    ]
    assert all("api_key" not in params for _, params, _ in calls)
    assert {urlparse(url).hostname for url, _, _ in calls} == {"api.openalex.org"}
    assert {url for url, _, _ in calls} == {API_URL}
