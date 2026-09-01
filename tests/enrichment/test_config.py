"""Strict runtime configuration contracts for optional enrichment."""

from dataclasses import FrozenInstanceError

import pytest
from omegaconf import OmegaConf

from zotero_arxiv_daily.enrichment.config import (
    InvalidEnrichmentConfigurationError,
    resolve_enrichment_settings,
)
from zotero_arxiv_daily.enrichment.settings import (
    ElsevierSettings,
    EnrichmentSettings,
    IeeeSettings,
    PubMedSettings,
    SpringerSettings,
)
from zotero_arxiv_daily.retriever.crossref_client import JsonObject


_SECRET = "runtime-enrichment-secret"
_CONTACT = "private-curator@example.test"


@pytest.mark.parametrize(
    "root",
    [
        pytest.param({}, id="absent"),
        pytest.param({"enrichment": None}, id="null"),
        pytest.param({"enrichment": "invalid"}, id="non-mapping"),
        pytest.param({"enrichment": {}}, id="missing-enabled"),
        pytest.param({"enrichment": {"enabled": False}}, id="false"),
        pytest.param({"enrichment": {"enabled": 1}}, id="integer"),
        pytest.param({"enrichment": {"enabled": "true"}}, id="string"),
    ],
)
def test_absent_or_not_exactly_enabled_resolves_to_disabled(root: JsonObject) -> None:
    # Given an absent, malformed, or non-boolean opt-in boundary.
    config = OmegaConf.create(root)

    # When optional enrichment settings are resolved.
    settings = resolve_enrichment_settings(config)

    # Then the feature is disabled without parsing subordinate values.
    assert settings is None


def test_enabled_defaults_convert_to_the_existing_immutable_settings() -> None:
    # Given only the exact global opt-in.
    config = OmegaConf.create({"enrichment": {"enabled": True}})

    # When runtime settings are resolved.
    settings = resolve_enrichment_settings(config)

    # Then all existing immutable defaults are retained.
    assert settings == EnrichmentSettings()


def test_enabled_config_converts_every_field_and_redacts_secrets() -> None:
    # Given every supported provider field with non-default values.
    config = OmegaConf.create(
        {
            "enrichment": {
                "enabled": True,
                "workers": 4,
                "max_papers": 17,
                "pubmed": {
                    "enabled": True,
                    "contact_email": f"  {_CONTACT}  ",
                    "api_key": f"  {_SECRET}-pubmed  ",
                    "request_rate": 2.5,
                    "issns": ["0028-0836"],
                },
                "ieee": {
                    "enabled": True,
                    "api_key": f"{_SECRET}-ieee",
                    "request_rate": 0.75,
                    "issns": ["0018-9219"],
                    "doi_prefixes": ["10.1109"],
                },
                "elsevier": {
                    "enabled": True,
                    "api_key": f"{_SECRET}-elsevier",
                    "request_rate": 4,
                    "issns": ["0001-6918"],
                    "doi_prefixes": ["10.1016"],
                },
                "springer": {
                    "enabled": True,
                    "api_key": f"{_SECRET}-springer",
                    "request_rate": 0.5,
                    "issns": ["1432-0541"],
                    "doi_prefixes": ["10.1007", "10.1038"],
                },
            }
        }
    )

    # When the boundary is parsed.
    settings = resolve_enrichment_settings(config)

    # Then conversion is exact, immutable, and secret-safe when rendered.
    assert settings == EnrichmentSettings(
        pubmed=PubMedSettings(
            enabled=True,
            contact_email=_CONTACT,
            api_key=f"{_SECRET}-pubmed",
            request_rate=2.5,
            issns=("0028-0836",),
        ),
        ieee=IeeeSettings(
            enabled=True,
            api_key=f"{_SECRET}-ieee",
            request_rate=0.75,
            issns=("0018-9219",),
            doi_prefixes=("10.1109",),
        ),
        elsevier=ElsevierSettings(
            enabled=True,
            api_key=f"{_SECRET}-elsevier",
            request_rate=4.0,
            issns=("0001-6918",),
            doi_prefixes=("10.1016",),
        ),
        springer=SpringerSettings(
            enabled=True,
            api_key=f"{_SECRET}-springer",
            request_rate=0.5,
            issns=("1432-0541",),
            doi_prefixes=("10.1007", "10.1038"),
        ),
        workers=4,
        max_papers=17,
    )
    assert settings is not None
    with pytest.raises(FrozenInstanceError):
        setattr(settings, "workers", 8)
    rendered = repr(settings)
    assert _SECRET not in rendered
    assert _CONTACT not in rendered


@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param({"workers": True}, id="boolean-workers"),
        pytest.param({"max_papers": 0}, id="empty-paper-bound"),
        pytest.param({"pubmed": "invalid"}, id="provider-not-mapping"),
        pytest.param({"pubmed": {"enabled": "true"}}, id="provider-enabled-type"),
        pytest.param(
            {"pubmed": {"enabled": True, "contact_email": None}},
            id="missing-pubmed-contact",
        ),
        pytest.param(
            {"ieee": {"enabled": True, "api_key": "  "}},
            id="missing-required-key",
        ),
        pytest.param(
            {"elsevier": {"request_rate": float("inf")}},
            id="non-finite-rate",
        ),
        pytest.param(
            {"springer": {"issns": ["not-an-issn"]}},
            id="malformed-issn",
        ),
        pytest.param(
            {"ieee": {"doi_prefixes": ["https://doi.org/10.1109"]}},
            id="malformed-doi-prefix",
        ),
        pytest.param({"ieee": {"api_key": 7}}, id="credential-type"),
        pytest.param({"unexpected": True}, id="unknown-field"),
    ],
)
def test_malformed_enabled_config_raises_one_static_redacted_error(
    malformed: JsonObject,
) -> None:
    # Given a globally enabled subtree with one malformed supplied value.
    config = OmegaConf.create(
        {
            "source": {"crossref": {"mailto": _SECRET}},
            "enrichment": {"enabled": True, **malformed},
        }
    )

    # When strict runtime parsing reaches that value.
    with pytest.raises(InvalidEnrichmentConfigurationError) as caught:
        _ = resolve_enrichment_settings(config)

    # Then the error category is stable and retains no supplied material.
    observable = f"{caught.value!s}\n{caught.value!r}\n{caught.value.__dict__}"
    assert str(caught.value) == "enabled enrichment configuration is invalid"
    assert caught.value.__dict__ == {}
    assert _SECRET not in observable
