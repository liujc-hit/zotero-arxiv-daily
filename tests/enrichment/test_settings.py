"""Immutable configuration contracts for abstract enrichment."""

from dataclasses import FrozenInstanceError

import pytest

from zotero_arxiv_daily.enrichment.settings import (
    ElsevierSettings,
    EnrichmentSettings,
    IeeeSettings,
    PubMedSettings,
    SpringerSettings,
)


def test_vendor_settings_require_both_enablement_and_a_nonblank_key() -> None:
    # Given enabled and disabled vendor settings with missing or present keys
    settings = (
        IeeeSettings(enabled=False, api_key="ieee-secret"),
        IeeeSettings(enabled=True),
        ElsevierSettings(enabled=True, api_key=" \t"),
        SpringerSettings(enabled=True, api_key="springer-secret"),
    )

    # When effective availability is resolved
    availability = tuple(setting.available for setting in settings)

    # Then only the explicitly enabled, keyed provider is available
    assert availability == (False, False, False, True)


def test_pubmed_requires_explicit_enablement_and_contact() -> None:
    # Given anonymous PubMed combinations and one valid contact
    settings = (
        PubMedSettings(contact_email="curator@example.test"),
        PubMedSettings(enabled=True),
        PubMedSettings(enabled=True, contact_email=" \t", api_key="nih-secret"),
        PubMedSettings(enabled=True, contact_email="curator@example.test"),
    )

    # When effective availability is resolved
    availability = tuple(setting.available for setting in settings)

    # Then anonymous access is enabled only by the explicit contact-bearing case
    assert availability == (False, False, False, True)


@pytest.mark.parametrize(
    ("settings", "expected_rate"),
    [
        (PubMedSettings(enabled=True, contact_email="c@example.test"), 3.0),
        (
            PubMedSettings(
                enabled=True,
                contact_email="c@example.test",
                api_key="nih-secret",
            ),
            10.0,
        ),
        (
            PubMedSettings(
                enabled=True,
                contact_email="c@example.test",
                api_key="nih-secret",
                request_rate=2.5,
            ),
            2.5,
        ),
        (ElsevierSettings(enabled=True, api_key="els-secret", request_rate=20), 9.0),
    ],
)
def test_provider_rate_caps_are_applied(
    settings: PubMedSettings | ElsevierSettings, expected_rate: float
) -> None:
    # Given a provider request rate at, below, or above its official limit
    # When the effective rate is requested
    rate = settings.effective_request_rate

    # Then the official limit is never exceeded
    assert rate == expected_rate


def test_defaults_keep_all_providers_disabled_and_define_springer_prefixes() -> None:
    # Given the standalone settings before later config wiring
    settings = EnrichmentSettings()

    # When defaults are inspected
    availability = (
        settings.pubmed.available,
        settings.ieee.available,
        settings.elsevier.available,
        settings.springer.available,
    )

    # Then enrichment is opt-in and Springer routing has the expected prefixes
    assert availability == (False, False, False, False)
    assert settings.springer.doi_prefixes == (
        "10.1007",
        "10.1038",
        "10.1057",
        "10.1186",
    )
    assert (settings.workers, settings.max_papers) == (3, 50)


def test_settings_are_immutable_and_redact_contact_and_keys_from_repr() -> None:
    # Given settings carrying contact and secret material
    secret = "provider-secret"
    contact = "private-contact@example.test"
    settings = EnrichmentSettings(
        pubmed=PubMedSettings(
            enabled=True,
            contact_email=contact,
            api_key=secret,
        ),
        ieee=IeeeSettings(enabled=True, api_key=secret),
    )

    # When mutation and rendering are attempted
    with pytest.raises(FrozenInstanceError):
        setattr(settings, "workers", 8)
    rendered = repr(settings)

    # Then values cannot change and sensitive configuration is absent
    assert secret not in rendered
    assert contact not in rendered
