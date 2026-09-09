"""Contracts for canonical paper identity derivation."""
# noqa: SIZE_OK - Required boundary matrices must remain in this single test file.

from dataclasses import dataclass

import pytest

from zotero_arxiv_daily.paper_identity import (
    PaperIdentity,
    filter_sent_paper_candidates,
    normalize_paper_identity,
    paper_identities,
    paper_identity,
)


@dataclass(frozen=True, slots=True)
class _PaperFixture:
    url: str
    doi: str | None = None
    pdf_url: str | None = None


@pytest.mark.parametrize(
    ("paper", "expected"),
    [
        pytest.param(
            _PaperFixture(
                doi="10.1000/Primary",
                url="https://doi.org/10.2000/from-url",
                pdf_url="https://arxiv.org/pdf/2609.02003.pdf",
            ),
            PaperIdentity("doi:10.1000/primary"),
            id="paper-doi-before-url-identities",
        ),
        pytest.param(
            _PaperFixture(
                doi="invalid",
                url="https://doi.org/10.2000/From-Resolver",
                pdf_url="https://arxiv.org/abs/2609.02003",
            ),
            PaperIdentity("doi:10.2000/from-resolver"),
            id="doi-resolver-before-pdf-arxiv",
        ),
        pytest.param(
            _PaperFixture(url="HTTP://DX.DOI.ORG/10.3000/Legacy-Resolver"),
            PaperIdentity("doi:10.3000/legacy-resolver"),
            id="dx-doi-resolver",
        ),
        pytest.param(
            _PaperFixture(
                url="https://arxiv.org/abs/2609.02003v2",
                pdf_url="https://arxiv.org/pdf/2401.01234.pdf",
            ),
            PaperIdentity("arxiv:2609.02003"),
            id="url-arxiv-before-pdf-arxiv",
        ),
        pytest.param(
            _PaperFixture(
                url="https://example.org/papers/42",
                pdf_url="https://arxiv.org/pdf/2609.02003v3.pdf",
            ),
            PaperIdentity("arxiv:2609.02003"),
            id="pdf-arxiv-before-generic-url",
        ),
        pytest.param(
            _PaperFixture(url="https://example.org/papers/42"),
            PaperIdentity("url:https://example.org/papers/42"),
            id="generic-url",
        ),
        pytest.param(_PaperFixture(url="relative/path"), None, id="unidentifiable"),
    ],
)
def test_paper_identity_uses_canonical_precedence(
    paper: _PaperFixture,
    expected: PaperIdentity | None,
) -> None:
    # Given: a structural paper input with potentially competing identifiers.
    # When: its canonical identity is derived.
    identity = paper_identity(paper)

    # Then: the highest-precedence valid identity is returned with a namespace.
    assert identity == expected


@pytest.mark.parametrize(
    ("direct_doi", "resolver_url", "expected"),
    [
        pytest.param(
            "10.1000/encoded",
            "https://doi.org/10.1000%2Fencoded",
            PaperIdentity("doi:10.1000/encoded"),
            id="encoded-slash",
        ),
        pytest.param(
            "10.1000/a#b",
            "https://doi.org/10.1000/a%23b",
            PaperIdentity("doi:10.1000/a#b"),
            id="encoded-hash",
        ),
        pytest.param(
            "10.1000/a%b",
            "https://doi.org/10.1000/a%25b",
            PaperIdentity("doi:10.1000/a%b"),
            id="encoded-percent",
        ),
        pytest.param(
            "10.1000/a%2Fb",
            "https://doi.org/10.1000/a%252Fb",
            PaperIdentity("doi:10.1000/a%2fb"),
            id="decode-exactly-once",
        ),
    ],
)
def test_doi_resolver_decodes_percent_encoded_path_once(
    direct_doi: str,
    resolver_url: str,
    expected: PaperIdentity,
) -> None:
    # Given: direct and resolver forms of the same DOI payload.
    direct = _PaperFixture(url="unused", doi=direct_doi)
    resolver = _PaperFixture(url=resolver_url)

    # When: identities are derived from both representations.
    identities = (paper_identity(direct), paper_identity(resolver))

    # Then: strict one-pass decoding produces the same canonical DOI identity.
    assert identities == (expected, expected)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("https://doi.org/10.1000/a%", None, id="bare-percent"),
        pytest.param("https://doi.org/10.1000/a%2", None, id="short-triplet"),
        pytest.param("https://doi.org/10.1000/a%GG", None, id="non-hex-triplet"),
        pytest.param(
            "https://doi.org/10.1000/a%FF",
            PaperIdentity("url:https://doi.org/10.1000/a%FF"),
            id="invalid-utf8-byte",
        ),
        pytest.param(
            "https://doi.org/10.1000/a%C3%28",
            PaperIdentity("url:https://doi.org/10.1000/a%C3%28"),
            id="invalid-utf8-sequence",
        ),
    ],
)
def test_doi_resolver_rejects_invalid_percent_encoding_as_doi(
    url: str,
    expected: PaperIdentity | None,
) -> None:
    # Given: a resolver path with malformed escapes or invalid UTF-8 bytes.
    # When: its paper identity is derived.
    identity = paper_identity(_PaperFixture(url=url))

    # Then: it never manufactures a DOI, while valid URL escapes may fall through.
    assert identity == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param(
            "https://doi.org:443/10.1000/default",
            PaperIdentity("doi:10.1000/default"),
            id="doi-https-default",
        ),
        pytest.param(
            "http://dx.doi.org:80/10.1000/default",
            PaperIdentity("doi:10.1000/default"),
            id="doi-http-default",
        ),
        pytest.param(
            "https://arxiv.org:443/abs/2609.02003",
            PaperIdentity("arxiv:2609.02003"),
            id="arxiv-https-default",
        ),
        pytest.param(
            "http://arxiv.org:80/pdf/2609.02003.pdf",
            PaperIdentity("arxiv:2609.02003"),
            id="arxiv-http-default",
        ),
        pytest.param(
            "https://doi.org:8443/10.1000/non-default",
            PaperIdentity("url:https://doi.org:8443/10.1000/non-default"),
            id="doi-non-default",
        ),
        pytest.param(
            "http://dx.doi.org:443/10.1000/non-default",
            PaperIdentity("url:http://dx.doi.org:443/10.1000/non-default"),
            id="dx-doi-non-default",
        ),
        pytest.param(
            "https://arxiv.org:8443/abs/2609.02003",
            PaperIdentity("url:https://arxiv.org:8443/abs/2609.02003"),
            id="arxiv-non-default",
        ),
    ],
)
def test_special_origins_require_their_scheme_default_port(
    url: str,
    expected: PaperIdentity,
) -> None:
    # Given: a DOI or arXiv origin with an explicit default or non-default port.
    # When: its paper identity is derived.
    identity = paper_identity(_PaperFixture(url=url))

    # Then: only default-port origins receive their special namespace.
    assert identity == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param(
            "https://arxiv.org/abs/0704.0001",
            PaperIdentity("arxiv:0704.0001"),
            id="modern-four-digit-first",
        ),
        pytest.param(
            "https://arxiv.org/pdf/1412.9999.pdf",
            PaperIdentity("arxiv:1412.9999"),
            id="modern-four-digit-last",
        ),
        pytest.param(
            "https://arxiv.org/abs/1501.00001",
            PaperIdentity("arxiv:1501.00001"),
            id="modern-five-digit-first",
        ),
        pytest.param(
            "https://arxiv.org/pdf/2609.02003v12.pdf?download=1#page=2",
            PaperIdentity("arxiv:2609.02003"),
            id="modern-pdf-five-digit-sequence",
        ),
        pytest.param(
            "http://arxiv.org/abs/hep-th/9901001v2",
            PaperIdentity("arxiv:hep-th/9901001"),
            id="legacy-abs",
        ),
        pytest.param(
            "https://arxiv.org/pdf/math.GT/0309136.pdf",
            PaperIdentity("arxiv:math.GT/0309136"),
            id="legacy-pdf",
        ),
        pytest.param(
            "https://arxiv.org/abs/hep-th/9107001",
            PaperIdentity("arxiv:hep-th/9107001"),
            id="legacy-first-month",
        ),
        pytest.param(
            "https://arxiv.org/pdf/q-bio.BM/0703999.pdf",
            PaperIdentity("arxiv:q-bio.BM/0703999"),
            id="legacy-last-month",
        ),
    ],
)
def test_paper_identity_recognizes_supported_arxiv_forms(
    url: str,
    expected: PaperIdentity,
) -> None:
    # Given: a supported modern or legacy arXiv abs/pdf URL.
    # When: the paper identity is derived.
    identity = paper_identity(_PaperFixture(url=url))

    # Then: URL-only decorations are removed from the arXiv namespace.
    assert identity == expected


def test_paper_identity_collapses_arxiv_versions_and_abs_pdf_variants() -> None:
    # Given: several URLs for versions and representations of one arXiv paper.
    papers = (
        _PaperFixture(url="https://arxiv.org/abs/2609.02003"),
        _PaperFixture(url="https://arxiv.org/abs/2609.02003v1"),
        _PaperFixture(url="https://arxiv.org/pdf/2609.02003v27.pdf"),
    )

    # When: each paper identity is derived.
    identities = tuple(paper_identity(paper) for paper in papers)

    # Then: all representations collapse to the versionless arXiv identity.
    assert identities == (PaperIdentity("arxiv:2609.02003"),) * 3


@pytest.mark.parametrize(
    "pdf_url",
    [
        pytest.param("https://export.arxiv.org/abs/2609.02003", id="subdomain"),
        pytest.param("ftp://arxiv.org/abs/2609.02003", id="non-http-scheme"),
        pytest.param("https://arxiv.org/abs/2609.123", id="short-modern-sequence"),
        pytest.param("https://arxiv.org/abs/2609.123456", id="long-modern-sequence"),
        pytest.param("https://arxiv.org/abs/9901001", id="legacy-without-archive"),
        pytest.param("https://arxiv.org/abs/2609.02003v0", id="zero-version"),
        pytest.param("https://arxiv.org/abs/2609.02003V2", id="uppercase-version"),
        pytest.param("https://arxiv.org/abs/2609.02003.pdf", id="pdf-on-abs-path"),
        pytest.param("https://arxiv.org/abs/0703.0001", id="modern-before-era"),
        pytest.param("https://arxiv.org/abs/1412.00001", id="early-five-digit"),
        pytest.param("https://arxiv.org/abs/1501.0001", id="late-four-digit"),
        pytest.param("https://arxiv.org/abs/0704.0000", id="four-digit-zero"),
        pytest.param("https://arxiv.org/abs/1501.00000", id="five-digit-zero"),
        pytest.param("https://arxiv.org/abs/hep-th/9106001", id="legacy-before-era"),
        pytest.param("https://arxiv.org/abs/hep-th/0704001", id="legacy-after-era"),
        pytest.param("https://arxiv.org/abs/hep-th/9913001", id="legacy-bad-month"),
        pytest.param("https://arxiv.org/abs/hep-th/9901000", id="legacy-zero"),
        pytest.param("https://arxiv.org/abs/hep-th/99010001", id="legacy-four-digit"),
        pytest.param("https://arxiv.org/abs/Hep-th/9901001", id="uppercase-archive"),
        pytest.param("https://arxiv.org/abs/hep--th/9901001", id="double-hyphen"),
        pytest.param("https://arxiv.org/abs/math.gt/0309136", id="lowercase-class"),
        pytest.param("https://arxiv.org/abs/math.G/0309136", id="short-class"),
        pytest.param("https://arxiv.org/abs/math.GTT/0309136", id="long-class"),
    ],
)
def test_paper_identity_rejects_unrecognized_arxiv_urls(pdf_url: str) -> None:
    # Given: a URL that resembles arXiv but violates its canonical URL grammar.
    paper = _PaperFixture(url="not-an-absolute-url", pdf_url=pdf_url)

    # When: the paper identity is derived.
    identity = paper_identity(paper)

    # Then: the lookalike is not assigned an arXiv identity.
    assert identity is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param(
            "HTTPS://Example.COM:443",
            PaperIdentity("url:https://example.com/"),
            id="https-default-port-and-empty-path",
        ),
        pytest.param(
            "HTTP://Example.COM:80/papers/42#abstract",
            PaperIdentity("url:http://example.com/papers/42"),
            id="http-and-fragment",
        ),
        pytest.param(
            "https://Example.COM:8443/A/../B/%2f/?b=2&a=1&a=3#ignored",
            PaperIdentity(
                "url:https://example.com:8443/A/../B/%2f/?b=2&a=1&a=3"
            ),
            id="stable-url-spelling-and-query",
        ),
        pytest.param(
            "https://Example.COM/papers/%2F%25?marker=%23&slash=%2f",
            PaperIdentity(
                "url:https://example.com/papers/%2F%25?marker=%23&slash=%2f"
            ),
            id="percent-escape-spelling",
        ),
    ],
)
def test_paper_identity_canonicalizes_stable_generic_urls(
    url: str,
    expected: PaperIdentity,
) -> None:
    # Given: an absolute generic HTTP URL with canonicalizable authority details.
    # When: the paper identity is derived.
    identity = paper_identity(_PaperFixture(url=url))

    # Then: only scheme, host, port, empty path, and fragment are canonicalized.
    assert identity == expected


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("", id="blank"),
        pytest.param("example.org/paper", id="relative"),
        pytest.param("ftp://example.org/paper", id="unsupported-scheme"),
        pytest.param("https:///paper", id="missing-host"),
        pytest.param("https://user:secret@example.org/paper", id="credentials"),
        pytest.param("https://example.org/a b", id="whitespace"),
        pytest.param("https://example.org/\x00paper", id="control-character"),
        pytest.param("https://example.org:not-a-port/paper", id="nonnumeric-port"),
        pytest.param("https://example.org:65536/paper", id="out-of-range-port"),
        pytest.param("https://example.org:/paper", id="empty-port"),
        pytest.param("https://example.org/paper%", id="bare-percent-in-path"),
        pytest.param("https://example.org/paper%2", id="short-percent-in-path"),
        pytest.param("https://example.org/paper?key=%GG", id="bad-percent-in-query"),
    ],
)
def test_paper_identity_rejects_unsafe_or_unstable_generic_urls(url: str) -> None:
    # Given: a malformed, unsafe, or non-HTTP URL.
    # When: the paper identity is derived.
    identity = paper_identity(_PaperFixture(url=url))

    # Then: no generic URL identity is produced.
    assert identity is None


@pytest.mark.parametrize(
    "value",
    [
        PaperIdentity("doi:10.1000/a"),
        PaperIdentity("arxiv:2609.02003"),
        PaperIdentity("arxiv:math.GT/0309136"),
        PaperIdentity("url:http://example.org/"),
        PaperIdentity("url:https://example.org/papers/42?edition=full"),
        PaperIdentity("url:https://doi.org:8443/10.1000/a"),
        PaperIdentity("url:https://arxiv.org:8443/abs/2609.02003"),
    ],
)
def test_normalize_paper_identity_accepts_only_canonical_namespaced_values(
    value: PaperIdentity,
) -> None:
    # Given: an already canonical identity in a supported namespace.
    # When: persisted identity text is normalized.
    normalized = normalize_paper_identity(value)

    # Then: the same branded canonical value is returned unchanged.
    assert normalized == value


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(None, id="none"),
        pytest.param("", id="blank"),
        pytest.param("10.1000/a", id="missing-namespace"),
        pytest.param("isbn:9780000000000", id="unknown-namespace"),
        pytest.param("doi:10.1000/A", id="noncanonical-doi-case"),
        pytest.param("arxiv:2609.02003v2", id="versioned-arxiv"),
        pytest.param("arxiv:9901001", id="legacy-without-archive"),
        pytest.param("arxiv:2401.1234", id="wrong-modern-sequence-width"),
        pytest.param("url:HTTPS://example.org/", id="noncanonical-scheme"),
        pytest.param("url:https://example.org", id="noncanonical-empty-path"),
        pytest.param("url:https://example.org:443/", id="default-port"),
        pytest.param("url:https://example.org/#fragment", id="fragment"),
        pytest.param("url:https://example.org/paper%", id="malformed-path-escape"),
        pytest.param("url:https://example.org/?key=%GG", id="malformed-query-escape"),
        pytest.param(
            "url:https://doi.org/10.1000/a",
            id="doi-in-url-namespace",
        ),
        pytest.param(
            "url:https://arxiv.org/abs/2609.02003",
            id="arxiv-in-url-namespace",
        ),
    ],
)
def test_normalize_paper_identity_rejects_noncanonical_values(
    value: str | None,
) -> None:
    # Given: state text that is noncanonical or uses an unknown namespace.
    # When: persisted identity text is normalized.
    normalized = normalize_paper_identity(value)

    # Then: invalid state identity text is rejected rather than rewritten.
    assert normalized is None


def test_paper_identities_excludes_unidentifiable_papers_and_namespaces_values() -> None:
    # Given: identifiable, duplicate, and unidentifiable structural paper inputs.
    papers = iter(
        (
            _PaperFixture(url="relative", doi="10.1000/A"),
            _PaperFixture(url="https://arxiv.org/abs/2609.02003v4"),
            _PaperFixture(url="HTTPS://EXAMPLE.ORG:443/paper"),
            _PaperFixture(url="missing-host"),
            _PaperFixture(url="unused", doi="https://doi.org/10.1000/a"),
        )
    )

    # When: identities are extracted from the iterable.
    identities = paper_identities(papers)

    # Then: only unique canonical identities remain, each in its own namespace.
    assert identities == frozenset(
        {
            PaperIdentity("doi:10.1000/a"),
            PaperIdentity("arxiv:2609.02003"),
            PaperIdentity("url:https://example.org/paper"),
        }
    )


def test_filter_sent_paper_candidates_preserves_unmatched_identity_and_order() -> None:
    # Given: sent papers, one new paper, and one unidentifiable paper in source order.
    sent_doi = _PaperFixture(url="unused", doi="HTTPS://DOI.ORG/10.1000/A")
    new_paper = _PaperFixture(url="https://example.org/new")
    sent_arxiv = _PaperFixture(url="https://arxiv.org/pdf/2609.02003v8.pdf")
    unidentifiable = _PaperFixture(url="relative/path")
    sent_url = _PaperFixture(url="HTTPS://EXAMPLE.ORG:443/sent#old-fragment")
    papers = [sent_doi, new_paper, sent_arxiv, unidentifiable, sent_url]
    sent_identities = {
        PaperIdentity("doi:10.1000/a"),
        PaperIdentity("arxiv:2609.02003"),
        PaperIdentity("url:https://example.org/sent"),
    }

    # When: candidates already represented in sent state are filtered.
    candidates = filter_sent_paper_candidates(papers, sent_identities)

    # Then: unmatched and unidentifiable objects remain unchanged and in order.
    assert candidates == [new_paper, unidentifiable]
    assert tuple(map(id, candidates)) == (id(new_paper), id(unidentifiable))
