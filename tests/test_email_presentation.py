"""Tests for bilingual presentation email rendering (render_email_bundle)."""

import pytest

from zotero_arxiv_daily.construct_email import render_email, render_email_bundle
from zotero_arxiv_daily.email_presentation import TLDR_UNAVAILABLE_NOTICE
from zotero_arxiv_daily.paper_presentation import (
    BilingualText,
    InlineImage,
    PaperPresentation,
    RenderedEmail,
    ResearchFigure,
)
from tests.canned_responses import make_sample_paper

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-payload"
_TRANSLATION_NOTICE_MARKERS = ("translation unavailable", "翻译暂缺")


def make_presented_paper(**overrides):
    """Sample paper carrying a fully bilingual presentation with one figure."""
    first_figure = overrides.pop(
        "first_figure",
        ResearchFigure(PNG_BYTES, "image/png", "Figure 1: benchmark results", "https://example.com/fig1"),
    )
    presentation = PaperPresentation(
        title=BilingualText(en="Sample Paper Title", zh="示例论文标题"),
        abstract=BilingualText(
            en="This paper explores a novel approach to widget engineering.",
            zh="本文探索了一种新颖的组件工程方法。",
        ),
        tldr=BilingualText(en="A great paper.", zh="一篇很棒的论文。"),
        first_figure=first_figure,
    )
    defaults = dict(score=7.5, tldr="A great paper.", affiliations=["MIT"], presentation=presentation)
    defaults.update(overrides)
    return make_sample_paper(**defaults)


# ---------------------------------------------------------------------------
# bundle contract
# ---------------------------------------------------------------------------


def test_render_email_bundle_returns_rendered_email():
    bundle = render_email_bundle([make_presented_paper()])
    assert isinstance(bundle, RenderedEmail)
    assert "Sample Paper Title" in bundle.html
    assert bundle.inline_images[0].cid == "figure-0"


def test_render_email_empty_list_still_works():
    html = render_email([])
    assert "No Papers Today" in html


def test_render_email_matches_bundle_html_for_papers_without_figures():
    papers = [
        make_sample_paper(score=7.0, tldr="legacy tldr", affiliations=["MIT"]),
        make_presented_paper(first_figure=None),
    ]
    assert render_email(papers) == render_email_bundle(papers).html


def test_render_email_standalone_omits_figures_and_cids():
    legacy_html = render_email([make_presented_paper()])
    bundle = render_email_bundle([make_presented_paper()])
    assert "cid:" not in legacy_html
    assert "<img" not in legacy_html
    assert 'src="cid:figure-0"' in bundle.html
    assert legacy_html != bundle.html


def test_empty_papers_bundle_has_no_images_and_plain_notice():
    bundle = render_email_bundle([])
    assert "No Papers Today" in bundle.html
    assert bundle.inline_images == ()
    assert "No Papers Today" in bundle.plain_text


def test_bundle_is_deterministic_across_calls():
    papers = [make_presented_paper()]
    assert render_email_bundle(papers) == render_email_bundle(papers)


def test_plain_text_digest_lists_papers():
    bundle = render_email_bundle([make_presented_paper()])
    assert "Sample Paper Title" in bundle.plain_text
    assert "https://arxiv.org/pdf/2026.00001" in bundle.plain_text


# ---------------------------------------------------------------------------
# bilingual presentation content
# ---------------------------------------------------------------------------


def test_presentation_shows_original_title_then_labeled_en_and_zh():
    html = render_email_bundle([make_presented_paper()]).html
    heading_pos = html.index("Sample Paper Title")
    assert "示例论文标题" in html
    en_label = html.index("EN:", heading_pos)
    zh_label = html.index("中文:", en_label)
    assert html.index("示例论文标题", zh_label) > zh_label


def test_presentation_omits_abstract_rows_even_when_populated():
    html = render_email_bundle([make_presented_paper()]).html
    assert "Abstract" not in html
    assert "摘要" not in html
    assert "This paper explores a novel approach to widget engineering." not in html
    assert "本文探索了一种新颖的组件工程方法。" not in html


def test_presentation_shows_tldr_with_bilingual_labels():
    html = render_email_bundle([make_presented_paper()]).html
    assert "TLDR" in html
    assert "一句话总结" in html
    assert "一篇很棒的论文。" in html


def test_authors_appear_once_in_presentation_block():
    paper = make_presented_paper(authors=["Solo Author"], affiliations=["MIT"])
    html = render_email_bundle([paper]).html
    assert html.count("Solo Author") == 1


def test_presentation_preserves_relevance_author_truncation_and_pinned_sections():
    many_authors = make_presented_paper(authors=[f"Author {i}" for i in range(10)], score=7.5)
    many_authors.pinned = True
    other = make_presented_paper(title="Rec Paper")
    bundle = render_email_bundle([many_authors, other])
    html = bundle.html
    assert "Pinned by keywords" in html
    assert "Recommended for you" in html
    assert html.index("Pinned by keywords") < html.index("Recommended for you")
    assert "Relevance:" in html
    assert "7.5" in html
    assert "Author 5" not in html
    assert "..." in html


def test_legacy_paper_without_presentation_keeps_plain_html():
    paper = make_sample_paper(score=7.0, tldr="legacy tldr", affiliations=["MIT"])
    bundle = render_email_bundle([paper])
    assert "legacy tldr" in bundle.html
    assert "cid:" not in bundle.html
    assert bundle.inline_images == ()


def test_mixed_legacy_and_presentation_papers_render_together():
    legacy = make_sample_paper(title="Legacy Paper", score=6.0, tldr="old style")
    presented = make_presented_paper()
    bundle = render_email_bundle([legacy, presented])
    assert "Legacy Paper" in bundle.html
    assert "示例论文标题" in bundle.html
    assert 'src="cid:figure-1"' in bundle.html
    assert [image.cid for image in bundle.inline_images] == ["figure-1"]


# ---------------------------------------------------------------------------
# missing translations: explicit bilingual notice, never fake labels
# ---------------------------------------------------------------------------


def test_missing_zh_title_shows_bilingual_notice_with_original_text():
    presentation = PaperPresentation(title=BilingualText(en="Fallback original text", zh=None))
    paper = make_sample_paper(presentation=presentation)
    html = render_email_bundle([paper]).html
    for marker in _TRANSLATION_NOTICE_MARKERS:
        assert marker in html
    assert "Fallback original text" in html


def test_missing_zh_tldr_shows_notice_once_without_original_echo():
    presentation = PaperPresentation(tldr=BilingualText(en="Fallback original text", zh=None))
    paper = make_sample_paper(tldr="Fallback original text", presentation=presentation)
    bundle = render_email_bundle([paper])
    assert bundle.html.count("Fallback original text") == 1
    assert f"中文:</strong> {TLDR_UNAVAILABLE_NOTICE}" in bundle.html
    assert bundle.plain_text.count("Fallback original text") == 1
    assert f"中文: {TLDR_UNAVAILABLE_NOTICE}" in bundle.plain_text


def test_missing_zh_title_is_not_presented_as_chinese():
    presentation = PaperPresentation(title=BilingualText(en="English Only Title", zh=None))
    paper = make_sample_paper(title="English Only Title", presentation=presentation)
    html = render_email_bundle([paper]).html
    zh_row = html[html.index("中文"):]
    assert "English Only Title" in zh_row[:400]
    for marker in _TRANSLATION_NOTICE_MARKERS:
        assert marker in zh_row[:400]


def test_missing_en_tldr_shows_zh_once_plus_notice():
    presentation = PaperPresentation(tldr=BilingualText(en=None, zh="只有中文总结"))
    paper = make_sample_paper(tldr=None, presentation=presentation)
    bundle = render_email_bundle([paper])
    assert bundle.html.count("只有中文总结") == 1
    assert f"EN:</strong> {TLDR_UNAVAILABLE_NOTICE}" in bundle.html
    assert bundle.plain_text.count("只有中文总结") == 1


def test_bilingual_tldr_missing_both_shows_single_notice_not_abstract_twice():
    paper = make_presented_paper(
        tldr=None,
        presentation=PaperPresentation(
            title=BilingualText(en="Sample Paper Title", zh="示例论文标题"),
        ),
    )
    bundle = render_email_bundle([paper])
    assert bundle.html.count(TLDR_UNAVAILABLE_NOTICE) == 1
    assert bundle.plain_text.count(TLDR_UNAVAILABLE_NOTICE) == 1
    assert "This paper explores a novel approach to widget engineering." not in bundle.html


# ---------------------------------------------------------------------------
# figures: cid mapping, unaltered bytes, safe links
# ---------------------------------------------------------------------------


def test_first_figure_rendered_once_via_deterministic_cid():
    bundle = render_email_bundle([make_presented_paper()])
    assert bundle.html.count('src="cid:figure-0"') == 1
    assert bundle.inline_images == (InlineImage("figure-0", PNG_BYTES, "image/png"),)


def test_figure_bytes_are_unaltered_and_caption_not_translated():
    bundle = render_email_bundle([make_presented_paper()])
    assert bundle.inline_images[0].data == PNG_BYTES
    assert bundle.inline_images[0].media_type == "image/png"
    assert "Figure 1: benchmark results" in bundle.html
    assert "Translated figure" not in bundle.html


def test_figure_img_uses_max_width_and_alt_text():
    html = render_email_bundle([make_presented_paper()]).html
    img_tag = html[html.index("<img"):]
    assert 'src="cid:figure-0"' in img_tag[:200]
    assert "max-width:100%" in img_tag[:300]
    assert "height:auto" in img_tag[:300]
    assert "alt=" in img_tag[:300]
    assert "Figure 1: benchmark results" in img_tag[:300]


def test_figure_source_link_preserved_for_safe_url():
    html = render_email_bundle([make_presented_paper()]).html
    assert 'href="https://example.com/fig1"' in html


def test_figure_source_unsafe_url_is_neutralized():
    presentation = PaperPresentation(
        first_figure=ResearchFigure(PNG_BYTES, "image/png", "cap", "javascript:alert(1)")
    )
    paper = make_sample_paper(presentation=presentation)
    html = render_email_bundle([paper]).html
    assert "javascript:" not in html


def test_every_cid_reference_has_matching_inline_image():
    papers = [make_presented_paper(), make_sample_paper(score=6.0, tldr="x"), make_presented_paper()]
    bundle = render_email_bundle(papers)
    referenced = {part.split('"')[0] for part in bundle.html.split('cid:')[1:]}
    provided = {image.cid for image in bundle.inline_images}
    assert referenced == provided


# ---------------------------------------------------------------------------
# image size budget
# ---------------------------------------------------------------------------


def _figure_of_size(size: int) -> ResearchFigure:
    return ResearchFigure(b"\x00" * size, "image/png", "big figure", "https://example.com/big")


def test_figures_within_budget_are_all_kept():
    small = ResearchFigure(b"x" * 1024, "image/png", "small", "https://example.com/s")
    first = make_presented_paper(first_figure=small)
    second = make_presented_paper(first_figure=small)
    bundle = render_email_bundle([first, second])
    assert [image.cid for image in bundle.inline_images] == ["figure-0", "figure-1"]


def test_figure_exceeding_budget_is_omitted_without_broken_cid():
    paper = make_presented_paper(first_figure=_figure_of_size(8 * 1024 * 1024 + 1))
    bundle = render_email_bundle([paper])
    assert bundle.inline_images == ()
    assert "cid:" not in bundle.html


def test_later_figures_omitted_deterministically_when_budget_exhausted():
    five_mib = 5 * 1024 * 1024
    big = _figure_of_size(five_mib)
    first = make_presented_paper(first_figure=big)
    second = make_presented_paper(first_figure=big)
    bundle = render_email_bundle([first, second])
    assert [image.cid for image in bundle.inline_images] == ["figure-0"]
    assert 'src="cid:figure-0"' in bundle.html
    assert 'src="cid:figure-1"' not in bundle.html


def test_figure_exactly_at_limit_is_kept():
    paper = make_presented_paper(first_figure=_figure_of_size(8 * 1024 * 1024))
    bundle = render_email_bundle([paper])
    assert [image.cid for image in bundle.inline_images] == ["figure-0"]


# ---------------------------------------------------------------------------
# escaping and link safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "evil"),
    [
        ("title", "<script>alert(1)</script>"),
        ("tldr", "<b>bold & sneaky</b>"),
    ],
)
def test_presentation_text_is_escaped(field, evil):
    bilingual = BilingualText(en=evil, zh=evil)
    presentations = {
        "title": PaperPresentation(title=bilingual),
        "tldr": PaperPresentation(tldr=bilingual),
    }
    paper = make_sample_paper(presentation=presentations[field])
    html = render_email_bundle([paper]).html
    assert "<script>" not in html
    assert "<img src=x" not in html
    assert "<b>bold" not in html
    assert "&lt;script&gt;" in html or "&lt;b&gt;" in html or "&lt;img" in html


def test_malicious_abstract_never_renders_anywhere():
    evil = "<img src=x onerror=alert(2)>"
    paper = make_sample_paper(abstract=evil, tldr=evil)
    paper.presentation = PaperPresentation(abstract=BilingualText(en=evil, zh=evil))
    bundle = render_email_bundle([paper])
    assert "<img src=x" not in bundle.html
    assert evil not in bundle.plain_text


def test_legacy_paper_text_is_escaped_too():
    paper = make_sample_paper(
        title="Evil <script>alert(1)</script> Title", tldr="tldr <b>x</b>", score=7.0,
        affiliations=["<i>Uni</i>"],
    )
    html = render_email_bundle([paper]).html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<i>Uni</i>" not in html


def test_paper_url_unsafe_scheme_is_neutralized():
    paper = make_sample_paper(score=7.0, tldr="ok", pdf_url="javascript:alert(9)", url="javascript:alert(9)")
    html = render_email_bundle([paper]).html
    assert "javascript:" not in html
