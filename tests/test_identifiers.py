"""Contract tests for publication identifier normalization."""

import pytest

from zotero_arxiv_daily.identifiers import normalize_doi, normalize_issn


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="none"),
        pytest.param("   ", None, id="blank"),
        pytest.param("10.1000/Mixed.Case", "10.1000/mixed.case", id="bare"),
        pytest.param(
            " HTTPS://DOI.ORG/10.5555/ABC.Def ",
            "10.5555/abc.def",
            id="doi-url",
        ),
        pytest.param(
            "http://dx.doi.org/10.12345/Example",
            "10.12345/example",
            id="dx-doi-url",
        ),
        pytest.param("doi.org/10.1000/ABC", "10.1000/abc", id="bare-host"),
        pytest.param("not a DOI", None, id="plain-text"),
        pytest.param(
            "https://example.com/10.1000/abc",
            None,
            id="untrusted-url",
        ),
        pytest.param("10.123/short-prefix", None, id="short-registrant"),
        pytest.param("10.1000/has space", None, id="embedded-space"),
    ],
)
def test_normalize_doi_returns_canonical_identifier_or_none(
    value: str | None, expected: str | None
) -> None:
    # Given: a raw identifier supplied by an external API.
    # When: the identifier crosses the normalization boundary.
    normalized = normalize_doi(value)

    # Then: only a canonical DOI is retained.
    assert normalized == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("0028-0836", "0028-0836", id="canonical"),
        pytest.param(" 00280836 ", "0028-0836", id="compact"),
        pytest.param("2434561x", "2434-561X", id="lowercase-check-digit"),
        pytest.param("0028-0837", None, id="bad-checksum"),
        pytest.param("12-34-567X", None, id="misplaced-hyphens"),
        pytest.param("", None, id="blank"),
    ],
)
def test_normalize_issn_returns_checksum_valid_canonical_identifier_or_none(
    value: str, expected: str | None
) -> None:
    # Given: an ISSN representation supplied by an external API.
    # When: the identifier crosses the normalization boundary.
    normalized = normalize_issn(value)

    # Then: only a checksum-valid canonical ISSN is retained.
    assert normalized == expected
