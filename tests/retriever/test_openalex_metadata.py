"""Focused publication-metadata tests for the OpenAlex retriever."""

import pytest
from omegaconf import DictConfig

from zotero_arxiv_daily.retriever.openalex_client import JsonObject
from zotero_arxiv_daily.retriever.openalex_retriever import OpenAlexRetriever


@pytest.fixture()
def openalex_config(config: DictConfig) -> DictConfig:
    config.source.openalex.api_keys = ["test-openalex-key"]
    config.source.openalex.allow_anonymous = False
    return config


def _work(work_type: str) -> JsonObject:
    return {
        "id": "https://openalex.org/W1",
        "doi": None,
        "title": "Paper",
        "type": work_type,
        "authorships": [],
        "primary_location": {},
        "best_oa_location": None,
    }


@pytest.mark.parametrize(
    ("work_type", "expected"),
    [
        pytest.param("posted-content", True, id="posted-content"),
        pytest.param("preprint", True, id="preprint"),
        pytest.param("article", False, id="article"),
        pytest.param("journal-article", False, id="journal-article"),
        pytest.param("proceedings-article", False, id="proceedings-article"),
        pytest.param("book", None, id="unknown"),
    ],
)
def test_openalex_maps_known_work_types_to_preprint_status(
    openalex_config: DictConfig, work_type: str, expected: bool | None
) -> None:
    # Given: an OpenAlex work with an explicit publication type.
    raw = _work(work_type)

    # When: the work is converted at the API boundary.
    paper = OpenAlexRetriever(openalex_config).convert_to_paper(raw)

    # Then: only known preprint or published types receive a boolean status.
    assert paper is not None
    assert paper.is_preprint is expected


def test_openalex_uses_crossref_work_type_when_primary_type_is_unknown(
    openalex_config: DictConfig,
) -> None:
    # Given: only the Crossref work type identifies a posted preprint.
    raw = _work("other")
    raw["type_crossref"] = "posted-content"

    # When: the work is converted at the API boundary.
    paper = OpenAlexRetriever(openalex_config).convert_to_paper(raw)

    # Then: the known Crossref type supplies the preprint status.
    assert paper is not None
    assert paper.is_preprint is True


def test_openalex_does_not_infer_doi_from_landing_page(
    openalex_config: DictConfig,
) -> None:
    # Given: a DOI-shaped landing URL without a DOI in the work metadata.
    raw = _work("article")
    raw["primary_location"] = {
        "landing_page_url": "https://doi.org/10.9999/LandingOnly"
    }

    # When: the work is converted at the API boundary.
    paper = OpenAlexRetriever(openalex_config).convert_to_paper(raw)

    # Then: no DOI is inferred from an arbitrary URL field.
    assert paper is not None
    assert paper.doi is None
