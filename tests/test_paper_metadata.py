"""Compatibility tests for publication metadata on Paper."""

from zotero_arxiv_daily.protocol import Paper


def test_paper_metadata_defaults_preserve_existing_constructors() -> None:
    # Given: only the arguments required by the original Paper contract.
    # When: a Paper is constructed without publication metadata.
    paper = Paper(
        source="arxiv",
        title="A paper",
        authors=["Ada Lovelace"],
        abstract="An abstract.",
        url="https://arxiv.org/abs/2601.00001",
    )

    # Then: every new field has its backward-compatible unknown default.
    assert (
        paper.doi,
        paper.publisher,
        paper.journal,
        paper.issns,
        paper.is_preprint,
        paper.venue_citation_proxy,
    ) == (None, None, None, (), None, None)
