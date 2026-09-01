"""Focused tests for runtime OpenAlex venue citation enrichment."""

from collections.abc import Iterator, Mapping
from typing import Final

import pytest

from zotero_arxiv_daily.enrichment.venue_citation import VenueCitationEnricher
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.retriever.openalex_client import JsonObject, JsonValue


_ISSN_A: Final = "0028-0836"
_ISSN_B: Final = "2041-1723"
_ISSN_C: Final = "1932-6203"


class _SourcesClient:
    """Record source requests while consuming deterministic JSON responses."""

    def __init__(self, responses: tuple[JsonValue, ...] = ()) -> None:
        self._responses: Iterator[JsonValue] = iter(responses)
        self.calls: list[Mapping[str, str | int]] = []

    def get_sources_json(self, params: Mapping[str, str | int]) -> JsonValue:
        self.calls.append(dict(params))
        return next(self._responses)


def _paper(
    status: bool | None,
    issns: tuple[str, ...] = (),
) -> Paper:
    return Paper(
        source="test",
        title="Paper",
        authors=[],
        abstract="Abstract",
        url="https://example.test/paper",
        issns=issns,
        is_preprint=status,
    )


def _valid_issn(index: int) -> str:
    first_seven = f"{1_000_000 + index:07d}"
    weighted_sum = sum(
        int(digit) * weight
        for digit, weight in zip(first_seven, range(8, 1, -1), strict=True)
    )
    check_value = (11 - weighted_sum % 11) % 11
    check_digit = "X" if check_value == 10 else str(check_value)
    compact = f"{first_seven}{check_digit}"
    return f"{compact[:4]}-{compact[4:]}"


@pytest.mark.parametrize(
    ("enabled", "papers"),
    [
        pytest.param(False, (_paper(False, (_ISSN_A,)),), id="disabled"),
        pytest.param(
            True,
            (
                _paper(False, ("invalid",)),
                _paper(True, (_ISSN_A,)),
                _paper(None, (_ISSN_B,)),
            ),
            id="no-published-issns",
        ),
    ],
)
def test_enrichment_makes_no_request_when_inactive_or_without_published_issns(
    enabled: bool,
    papers: tuple[Paper, ...],
) -> None:
    # Given a disabled service or no valid ISSN on a confirmed published paper.
    client = _SourcesClient()
    enricher = VenueCitationEnricher(client, enabled=enabled)

    # When candidate papers are offered for enrichment.
    enricher.enrich(list(papers))

    # Then no OpenAlex Sources request is made.
    assert client.calls == []


def test_enrichment_batches_unique_normalized_issns_at_one_hundred() -> None:
    # Given 201 unique published ISSNs plus duplicates and ineligible statuses.
    issns = tuple(_valid_issn(index) for index in range(201))
    papers = [_paper(False, (issn.replace("-", ""),)) for issn in issns]
    papers.extend(
        (
            _paper(False, (issns[0], "invalid")),
            _paper(True, (_valid_issn(500),)),
            _paper(None, (_valid_issn(501),)),
        )
    )
    client = _SourcesClient(tuple({"results": []} for _ in range(3)))

    # When venue citation values are requested.
    VenueCitationEnricher(client, enabled=True).enrich(papers)

    # Then each unique normalized ISSN appears once in exact OR batches of 100.
    expected = [
        {
            "filter": f"issn:{'|'.join(issns[offset : offset + 100])}",
            "per_page": 100,
        }
        for offset in range(0, len(issns), 100)
    ]
    assert client.calls == expected


@pytest.mark.parametrize(
    "metric",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(-0.1, id="negative"),
        pytest.param("4.5", id="string"),
        pytest.param(True, id="boolean"),
        pytest.param([4.5], id="list"),
    ],
)
def test_parser_rejects_non_finite_negative_and_wrong_metric_types(
    metric: JsonValue,
) -> None:
    # Given a matching source whose citation value is not a finite nonnegative number.
    payload: JsonObject = {
        "results": [
            {
                "issn": [_ISSN_A],
                "issn_l": _ISSN_A,
                "summary_stats": {"2yr_mean_citedness": metric},
            }
        ]
    }
    client = _SourcesClient((payload,))
    published = _paper(False, (_ISSN_A,))
    preprint = _paper(True)

    # When the response is parsed and matched.
    VenueCitationEnricher(client, enabled=True).enrich([published, preprint])

    # Then neither paper receives an invalid value or a derived sample.
    assert published.venue_citation_proxy is None
    assert preprint.venue_citation_proxy is None


def test_enrichment_matches_only_normalized_source_issns_and_issn_l() -> None:
    # Given published papers and source records addressable only by ISSN fields.
    payload: JsonObject = {
        "results": [
            {
                "id": "https://openalex.org/S1",
                "display_name": "Ignored source name",
                "issn": [_ISSN_A, "invalid", 42],
                "issn_l": _ISSN_A,
                "summary_stats": {"2yr_mean_citedness": 4.5},
            },
            {
                "issn": [],
                "issn_l": _ISSN_B,
                "summary_stats": {"2yr_mean_citedness": 7},
            },
        ]
    }
    client = _SourcesClient((payload,))
    print_issn = _paper(False, (_ISSN_A.replace("-", ""),))
    linked_issn = _paper(False, (_ISSN_B,))
    unmatched = _paper(False, (_ISSN_C,))
    unknown = _paper(None, (_ISSN_A,))

    # When source metrics are assigned by normalized ISSN intersection.
    VenueCitationEnricher(client, enabled=True).enrich(
        [print_issn, linked_issn, unmatched, unknown]
    )

    # Then only confirmed published papers with a source ISSN match are enriched.
    assert print_issn.venue_citation_proxy == 4.5
    assert linked_issn.venue_citation_proxy == 7.0
    assert unmatched.venue_citation_proxy is None
    assert unknown.venue_citation_proxy is None


def test_preprints_receive_the_median_of_available_source_samples() -> None:
    # Given two source samples, duplicate published papers, and preprints.
    payload: JsonObject = {
        "results": [
            {
                "issn": [_ISSN_A],
                "issn_l": _ISSN_A,
                "summary_stats": {"2yr_mean_citedness": 2.0},
            },
            {
                "issn": [_ISSN_B],
                "issn_l": _ISSN_B,
                "summary_stats": {"2yr_mean_citedness": 8.0},
            },
        ]
    }
    client = _SourcesClient((payload,))
    low = _paper(False, (_ISSN_A,))
    low_duplicate = _paper(False, (_ISSN_A,))
    high = _paper(False, (_ISSN_B,))
    first_preprint = _paper(True)
    second_preprint = _paper(True, (_ISSN_C,))
    unknown = _paper(None, (_ISSN_A,))

    # When enrichment collects source values before handling preprints.
    VenueCitationEnricher(client, enabled=True).enrich(
        [low, low_duplicate, high, first_preprint, second_preprint, unknown]
    )

    # Then each source is one sample and every confirmed preprint receives its median.
    assert (low.venue_citation_proxy, low_duplicate.venue_citation_proxy) == (2.0, 2.0)
    assert high.venue_citation_proxy == 8.0
    assert first_preprint.venue_citation_proxy == 5.0
    assert second_preprint.venue_citation_proxy == 5.0
    assert unknown.venue_citation_proxy is None


def test_preprints_remain_neutral_when_no_source_sample_is_available() -> None:
    # Given a published ISSN with no valid matching source metric.
    client = _SourcesClient(({"results": [{"issn": [_ISSN_B]}]},))
    published = _paper(False, (_ISSN_A,))
    preprint = _paper(True)

    # When enrichment completes without a usable source sample.
    VenueCitationEnricher(client, enabled=True).enrich([published, preprint])

    # Then both values remain unknown and therefore neutral to ranking.
    assert published.venue_citation_proxy is None
    assert preprint.venue_citation_proxy is None


def test_service_reuses_only_its_in_memory_source_cache() -> None:
    # Given one service instance and one successful source response.
    payload: JsonObject = {
        "results": [
            {
                "issn": [_ISSN_A],
                "summary_stats": {"2yr_mean_citedness": 3.0},
            }
        ]
    }
    client = _SourcesClient((payload,))
    enricher = VenueCitationEnricher(client, enabled=True)
    first = _paper(False, (_ISSN_A,))
    second = _paper(False, (_ISSN_A,))

    # When the same ISSN is enriched twice during one service run.
    enricher.enrich([first])
    enricher.enrich([second])

    # Then the second paper uses memory without another request.
    assert len(client.calls) == 1
    assert (first.venue_citation_proxy, second.venue_citation_proxy) == (3.0, 3.0)
