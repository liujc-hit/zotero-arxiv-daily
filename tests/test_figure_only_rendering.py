"""Regression tests for independently found rendering blockers.

1. Figures-only mode must render a single-language card plus optional figure —
   never bilingual labels or translation notices.
2. Legacy render_email() must return HTML with no unresolved cid references.
3. Malformed external URLs must never abort mail rendering.
"""

from dataclasses import replace

import pytest

from zotero_arxiv_daily import final_enrichment as enrichment
from zotero_arxiv_daily.construct_email import render_email, render_email_bundle
from zotero_arxiv_daily.email_presentation import TRANSLATION_NOTICE, safe_url
from zotero_arxiv_daily.final_enrichment import enrich_final_papers
from zotero_arxiv_daily.paper_presentation import (
    BilingualText,
    PaperPresentation,
    ResearchFigure,
)
from zotero_arxiv_daily.protocol import Paper
from tests.canned_responses import make_sample_paper
from tests.llm_fakes import make_recording_client

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"figure-payload"
_FIGURE = ResearchFigure(PNG_BYTES, "image/png", "Figure A: overview", "https://example.com/fig")


def make_figures_only_paper(with_figure: bool = True, **overrides) -> Paper:
    presentation = PaperPresentation(bilingual_enabled=False)
    if with_figure:
        presentation = replace(presentation, first_figure=_FIGURE)
    defaults = dict(score=7.0, tldr="single language tldr", affiliations=["MIT"], presentation=presentation)
    defaults.update(overrides)
    return make_sample_paper(**defaults)


# ---------------------------------------------------------------------------
# contract: additive bilingual_enabled flag
# ---------------------------------------------------------------------------


def test_paper_presentation_defaults_to_bilingual_enabled():
    assert PaperPresentation().bilingual_enabled is True


def test_paper_presentation_positional_constructors_keep_working():
    presentation = PaperPresentation(
        BilingualText(en="T", zh="译"), BilingualText(en="A", zh="摘"), BilingualText(en="S", zh="结"), _FIGURE
    )
    assert (presentation.title, presentation.abstract, presentation.tldr, presentation.first_figure) == (
        BilingualText(en="T", zh="译"), BilingualText(en="A", zh="摘"), BilingualText(en="S", zh="结"), _FIGURE
    )
    assert presentation.bilingual_enabled is True
    assert PaperPresentation(bilingual_enabled=False).bilingual_enabled is False


# ---------------------------------------------------------------------------
# final_enrichment -> renderer, all four flag combos
# ---------------------------------------------------------------------------


def _enrich(papers, monkeypatch, *, bilingual: bool, figures: bool, figure_bytes: bytes | None):
    def fake_figure(paper):
        if figure_bytes is None:
            raise RuntimeError("no figure source available")
        return ResearchFigure(figure_bytes, "image/png", "enriched figure", "https://example.com/fig")

    monkeypatch.setattr(enrichment, "fetch_first_figure", fake_figure)
    monkeypatch.setattr(
        enrichment, "generate_bilingual_digest",
        lambda *args: PaperPresentation(title=BilingualText(en="T", zh="译")),
    )
    return enrich_final_papers(
        papers, {}, make_recording_client()[0], {}, 1, bilingual=bilingual, figures=figures
    )


@pytest.mark.parametrize("figure_bytes", [PNG_BYTES, None], ids=["figure-ok", "figure-missing"])
@pytest.mark.parametrize("bilingual", [False, True], ids=["bilingual-off", "bilingual-on"])
def test_enrichment_to_renderer_all_flag_combos(monkeypatch, bilingual, figure_bytes):
    paper = make_sample_paper(score=7.0, tldr=None, affiliations=None)
    enriched = _enrich([paper], monkeypatch, bilingual=bilingual, figures=True, figure_bytes=figure_bytes)
    bundle = render_email_bundle(enriched)
    html, plain = bundle.html, bundle.plain_text

    presentation = enriched[0].presentation
    if bilingual:
        assert presentation is not None and presentation.bilingual_enabled is True
        assert "中文" in html and "译" in html
    else:
        # Figures-only mode: single-language card, never bilingual labels or notices.
        assert presentation is not None and presentation.bilingual_enabled is False
        assert "一句话总结" not in html
        assert "摘要" not in html
        assert TRANSLATION_NOTICE not in html
        assert TRANSLATION_NOTICE not in plain
        assert "<strong>TLDR:</strong>" in html
        assert enriched[0].tldr is not None
        assert enriched[0].tldr in html

    if figure_bytes is None:
        assert presentation.first_figure is None
        assert "cid:" not in html
        assert bundle.inline_images == ()
    else:
        assert presentation.first_figure is not None
        assert "cid:figure-0" in html
        assert [image.data for image in bundle.inline_images] == [figure_bytes]


def test_enrichment_bilingual_off_figures_off_leaves_presentation_none(monkeypatch):
    paper = make_sample_paper(score=7.0, tldr=None, affiliations=None)
    enriched = _enrich([paper], monkeypatch, bilingual=False, figures=False, figure_bytes=PNG_BYTES)
    assert enriched[0].presentation is None


def test_enrichment_bilingual_off_figures_off_with_existing_presentation_untouched(monkeypatch):
    paper = make_figures_only_paper(with_figure=False)
    enriched = _enrich([paper], monkeypatch, bilingual=False, figures=False, figure_bytes=PNG_BYTES)
    assert enriched[0].presentation is not None


# ---------------------------------------------------------------------------
# renderer: figures-only card content
# ---------------------------------------------------------------------------


def test_figures_only_paper_renders_single_language_card_with_figure():
    bundle = render_email_bundle([make_figures_only_paper(with_figure=True)])
    html = bundle.html
    assert "Sample Paper Title" in html
    assert "single language tldr" in html
    assert "Relevance:" in html
    assert ">PDF</a>" in html
    assert 'src="cid:figure-0"' in html
    assert "Figure A: overview" in html
    # No bilingual structure at all.
    for fragment in ("EN:", "中文:", "一句话总结", "摘要", TRANSLATION_NOTICE):
        assert fragment not in html


def test_figures_only_paper_without_figure_still_renders_card():
    bundle = render_email_bundle([make_figures_only_paper(with_figure=False)])
    html = bundle.html
    assert "Sample Paper Title" in html
    assert "cid:" not in html
    for fragment in ("EN:", "中文:", "一句话总结", "摘要", TRANSLATION_NOTICE):
        assert fragment not in html


def test_figures_only_plain_text_has_no_notices():
    bundle = render_email_bundle([make_figures_only_paper(with_figure=True)])
    assert "single language tldr" in bundle.plain_text
    for fragment in ("EN:", "中文:", TRANSLATION_NOTICE):
        assert fragment not in bundle.plain_text


def test_figures_only_pinned_paper_keeps_badge_and_sections():
    paper = make_figures_only_paper(with_figure=True)
    paper.pinned = True
    other = make_figures_only_paper(with_figure=False, title="Other Card")
    bundle = render_email_bundle([paper, other])
    html = bundle.html
    assert "Pinned by keywords" in html
    assert "Recommended for you" in html
    assert html.index("Pinned by keywords") < html.index("Recommended for you")
    assert TRANSLATION_NOTICE not in html


# ---------------------------------------------------------------------------
# legacy render_email: no unresolved cid, figures omitted
# ---------------------------------------------------------------------------


def test_legacy_render_email_contains_no_cid_or_img_for_figured_papers():
    papers = [make_figures_only_paper(with_figure=True), make_presented_with_figure()]
    html = render_email(papers)
    assert "cid:" not in html
    assert "<img" not in html
    # Text content of both cards is still rendered.
    assert "Sample Paper Title" in html


def make_presented_with_figure() -> Paper:
    from tests.test_email_presentation import make_presented_paper

    return make_presented_paper()


def test_legacy_render_email_matches_bundle_for_papers_without_figures():
    from tests.test_email_presentation import make_presented_paper

    papers = [
        make_figures_only_paper(with_figure=False),
        make_sample_paper(score=6.5, tldr="legacy tldr"),
        make_presented_paper(first_figure=None),
    ]
    assert render_email(papers) == render_email_bundle(papers).html


def test_bundle_keeps_attachments_and_cid_consistency_for_same_papers():
    papers = [make_presented_with_figure()]
    legacy_html = render_email(papers)
    bundle = render_email_bundle(papers)
    assert legacy_html != bundle.html
    assert "cid:" not in legacy_html
    assert 'src="cid:figure-0"' in bundle.html
    assert [image.cid for image in bundle.inline_images] == ["figure-0"]
    referenced = {part.split('"')[0] for part in bundle.html.split("cid:")[1:]}
    assert referenced == {image.cid for image in bundle.inline_images}


def test_figures_only_first_figure_bytes_survive_bundle_mime_path():
    bundle = render_email_bundle([make_figures_only_paper(with_figure=True)])
    assert bundle.inline_images[0].data == PNG_BYTES


# ---------------------------------------------------------------------------
# safe_url hardening
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://",
        "http://",
        "https://[",
        "https://[::1",
        "https://[zzzz]/path",
        "https://user:pass@example.com/x",
        "https://user@example.com/x",
        "https://ex\tample.com/x",
        "https://example.com/x\r\nInjected: 1",
        "javascript:alert(1)",
        "ftp://example.com/file",
        "/relative/path",
        "not a url",
    ],
)
def test_safe_url_rejects_malformed_and_unsafe(url):
    assert safe_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com/a?b=c#frag",
        "https://[::1]:8080/x",
        "https://arxiv.org/abs/2026.00001",
    ],
)
def test_safe_url_keeps_wellformed_http_urls(url):
    assert safe_url(url) == url


def test_safe_url_none_and_empty_pass_through():
    assert safe_url(None) is None
    assert safe_url("") is None


# ---------------------------------------------------------------------------
# malformed metadata must not abort mail; papers retained, links omitted
# ---------------------------------------------------------------------------


def test_malformed_pdf_url_legacy_card_renders_without_pdf_link():
    paper = make_sample_paper(pdf_url="https://[", url="https://[", score=7.0, tldr="still here")
    bundle = render_email_bundle([paper])
    html = bundle.html
    assert "Sample Paper Title" in html
    assert "still here" in html
    assert "https://[" not in html
    assert ">PDF</a>" not in html
    assert "cid:" not in html


def test_malformed_pdf_url_render_email_also_survives():
    paper = make_sample_paper(pdf_url="https://[", url="https://[", score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Sample Paper Title" in html
    assert ">PDF</a>" not in html


@pytest.mark.parametrize("bad_url", ["https://[", "https://[zz]", "https://u:p@h.com/x"])
def test_malformed_presentation_links_omit_links_but_keep_card_and_figure(bad_url):
    presentation = PaperPresentation(
        bilingual_enabled=False,
        first_figure=ResearchFigure(PNG_BYTES, "image/png", "cap", bad_url),
    )
    paper = make_sample_paper(
        pdf_url=bad_url, url=bad_url, score=7.0, tldr="t", presentation=presentation,
    )
    bundle = render_email_bundle([paper])
    html = bundle.html
    assert "Sample Paper Title" in html
    assert 'src="cid:figure-0"' in html
    assert "cap" in html
    assert bad_url not in html
    assert ">Source</a>" not in html
    assert ">PDF</a>" not in html


def test_malformed_url_bilingual_presentation_keeps_all_papers():
    presentation = PaperPresentation(
        title=BilingualText(en="T", zh="译"),
        first_figure=ResearchFigure(PNG_BYTES, "image/png", "cap", "https://["),
    )
    papers = [
        make_sample_paper(pdf_url="https://[", url="https://[", presentation=presentation, score=7.0, tldr=None),
        make_sample_paper(title="Healthy Paper", score=6.0, tldr="fine"),
    ]
    bundle = render_email_bundle(papers)
    assert "Sample Paper Title" in bundle.html
    assert "Healthy Paper" in bundle.html
    assert "https://[" not in bundle.html
