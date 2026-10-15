"""Tests for the shared immutable presentation contract."""

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace

import pytest

from zotero_arxiv_daily.paper_presentation import (
    BilingualText,
    ImageMediaType,
    InlineImage,
    PaperPresentation,
    RenderedEmail,
    ResearchFigure,
)
from zotero_arxiv_daily.protocol import Paper


def test_paper_presentation_defaults_to_none_when_omitted() -> None:
    # Given / When
    paper = Paper("arxiv", "Title", ["Author"], "Abstract", "https://example.com")

    # Then
    assert paper.presentation is None


def test_bilingual_text_has_empty_languages_when_defaulted() -> None:
    # Given / When
    text = BilingualText()

    # Then
    assert (text.en, text.zh) == (None, None)


@pytest.mark.parametrize(
    ("en", "zh"),
    [(None, None), ("English", None), (None, "Chinese"), ("English", "Chinese")],
)
def test_bilingual_text_retains_optional_languages_when_constructed(
    en: str | None, zh: str | None
) -> None:
    # Given / When
    text = BilingualText(en=en, zh=zh)

    # Then
    assert (text.en, text.zh) == (en, zh)


def test_presentation_has_independent_empty_text_when_defaulted() -> None:
    # Given / When
    first = PaperPresentation()
    second = PaperPresentation()

    # Then
    assert first.title == first.abstract == first.tldr == BilingualText()
    assert first.first_figure is None
    assert first.title is not first.abstract
    assert first.title is not first.tldr
    assert first.abstract is not first.tldr
    assert first.title is not second.title
    assert first.abstract is not second.abstract
    assert first.tldr is not second.tldr


@pytest.mark.parametrize("media_type", ["image/png", "image/jpeg"])
def test_figure_retains_payload_when_constructed(media_type: ImageMediaType) -> None:
    # Given
    data = b"image bytes"
    source_url = "https://example.com/figure"

    # When
    figure = ResearchFigure(data, media_type, "Caption", source_url)

    # Then
    assert (figure.data, figure.media_type, figure.caption, figure.source_url) == (
        data, media_type, "Caption", source_url
    )


def test_presentation_retains_content_when_populated() -> None:
    # Given
    title = BilingualText(en="Title", zh="Translated title")
    abstract = BilingualText(en="Abstract")
    tldr = BilingualText(zh="Translated summary")
    figure = ResearchFigure(b"image", "image/png", "Caption", "https://example.com")

    # When
    presentation = PaperPresentation(title, abstract, tldr, figure)

    # Then
    assert (presentation.title, presentation.abstract, presentation.tldr,
            presentation.first_figure) == (title, abstract, tldr, figure)


def test_rendered_email_has_empty_optional_content_when_defaulted() -> None:
    # Given / When
    email = RenderedEmail("<p>Digest</p>")

    # Then
    assert (email.html, email.inline_images, email.plain_text) == (
        "<p>Digest</p>", (), ""
    )


@pytest.mark.parametrize("media_type", ["image/png", "image/jpeg"])
def test_rendered_email_retains_inline_image_when_populated(
    media_type: ImageMediaType,
) -> None:
    # Given
    image = InlineImage("figure-1", b"image", media_type)

    # When
    email = RenderedEmail('<img src="cid:figure-1">', (image,), "Digest")

    # Then
    assert (email.html, email.inline_images, email.plain_text) == (
        '<img src="cid:figure-1">', (image,), "Digest"
    )
    assert (image.cid, image.data, image.media_type) == ("figure-1", b"image", media_type)


@pytest.mark.parametrize(
    "record",
    [
        BilingualText(),
        ResearchFigure(b"image", "image/png", "Caption", "https://example.com"),
        PaperPresentation(),
        InlineImage("figure-1", b"image", "image/jpeg"),
        RenderedEmail("<p>Digest</p>"),
    ],
)
def test_contract_is_frozen_and_slotted_when_field_assignment_is_attempted(
    record: BilingualText | ResearchFigure | PaperPresentation | InlineImage | RenderedEmail,
) -> None:
    # Given
    field_name = fields(record)[0].name

    # When / Then
    with pytest.raises(FrozenInstanceError):
        setattr(record, field_name, None)
    assert is_dataclass(record)
    assert not hasattr(record, "__dict__")


def test_original_paper_fields_are_preserved_when_presentation_is_added() -> None:
    # Given
    paper = Paper(
        "arxiv", "Title", ["Author"], "Abstract", "https://example.com",
        pdf_url="https://example.com/paper.pdf", full_text="Full text", tldr="Summary",
        affiliations=["University"], score=0.75, pinned=True, doi="10.1234/example",
        publisher="Publisher", issns=("1234-5678",), is_preprint=True,
        venue_citation_proxy=12.5, journal="Journal",
    )
    presentation = PaperPresentation(title=BilingualText(zh="Translated title"))

    # When
    enriched = replace(paper, presentation=presentation)

    # Then
    assert enriched.presentation is presentation
    assert replace(enriched, presentation=None) == paper
    assert tuple(field.name for field in fields(Paper))[-2:] == ("journal", "presentation")


def test_paper_remains_mutable_when_enriched() -> None:
    # Given
    paper = Paper("arxiv", "Title", [], "Abstract", "https://example.com")
    presentation = PaperPresentation(tldr=BilingualText(en="Presentation summary"))

    # When
    paper.presentation = presentation
    paper.tldr = "Ranking summary"
    paper.score = 0.9

    # Then
    assert (paper.presentation, paper.tldr, paper.score) == (
        presentation, "Ranking summary", 0.9
    )
