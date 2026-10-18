"""Digest rendering contracts: one TLDR per language, abstracts never displayed.

Pins the email content contract for the five-field structured digest
(title_en/title_zh/tldr_en/tldr_zh/affiliations):
- each bilingual paper shows one EN TLDR and one ZH TLDR, exactly once each,
  in both HTML and the plain-text mirror;
- the source or translated abstract never appears in any display mode,
  including the legacy raw-abstract TLDR fallback;
- a structured TLDR that legitimately equals the source abstract still displays;
- rendering never mutates ``Paper.abstract``.
"""

import pytest

from zotero_arxiv_daily.construct_email import render_email, render_email_bundle
from zotero_arxiv_daily.email_presentation import TLDR_UNAVAILABLE_NOTICE
from zotero_arxiv_daily.paper_presentation import (
    BilingualText,
    PaperPresentation,
    ResearchFigure,
)
from tests.canned_responses import make_sample_paper

ABSTRACT_SENTINEL = "RAW-ABSTRACT-SENTINEL must never reach the inbox."
EN_TLDR = "The robot learns a grasp policy from a single demonstration."
ZH_TLDR = "机器人从单次示范中学习抓取策略。"
ONE_LINE_ABSTRACT = "We show that scale alone predicts emergent capability."
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-preview-payload"


def _bilingual_paper(**overrides):
    presentation = overrides.pop(
        "presentation",
        PaperPresentation(
            title=BilingualText(en="Sample Paper Title", zh="示例论文标题"),
            tldr=BilingualText(en=EN_TLDR, zh=ZH_TLDR),
        ),
    )
    return make_sample_paper(tldr=EN_TLDR, presentation=presentation, **overrides)


# ---------------------------------------------------------------------------
# exactly-once TLDR per language, HTML and plain
# ---------------------------------------------------------------------------


def test_bilingual_tldr_each_language_exactly_once_in_html_and_plain():
    bundle = render_email_bundle([_bilingual_paper()])
    assert bundle.html.count(EN_TLDR) == 1
    assert bundle.html.count(ZH_TLDR) == 1
    assert bundle.plain_text.count(EN_TLDR) == 1
    assert bundle.plain_text.count(ZH_TLDR) == 1


def test_tldr_rows_keep_labeled_structure_and_figure():
    presentation = PaperPresentation(
        title=BilingualText(en="Sample Paper Title", zh="示例论文标题"),
        tldr=BilingualText(en=EN_TLDR, zh=ZH_TLDR),
        first_figure=ResearchFigure(PNG_BYTES, "image/png", "Figure 1: overview", "https://example.com/fig"),
    )
    bundle = render_email_bundle([make_sample_paper(tldr=EN_TLDR, presentation=presentation)])
    html = bundle.html
    assert "TLDR" in html and "一句话总结" in html
    assert 'src="cid:figure-0"' in html
    assert html.index(EN_TLDR) < html.index(ZH_TLDR)
    assert [image.cid for image in bundle.inline_images] == ["figure-0"]


# ---------------------------------------------------------------------------
# raw abstract never renders, in any mode
# ---------------------------------------------------------------------------


def _paper_for_mode(mode: str):
    if mode == "bilingual-ok":
        return _bilingual_paper(abstract=ABSTRACT_SENTINEL)
    if mode == "bilingual-fail":
        return make_sample_paper(
            abstract=ABSTRACT_SENTINEL, tldr=ABSTRACT_SENTINEL, presentation=PaperPresentation(),
        )
    if mode == "figures-only":
        return make_sample_paper(
            abstract=ABSTRACT_SENTINEL, tldr=ABSTRACT_SENTINEL,
            presentation=PaperPresentation(bilingual_enabled=False),
        )
    return make_sample_paper(abstract=ABSTRACT_SENTINEL, tldr=ABSTRACT_SENTINEL, presentation=None)


@pytest.mark.parametrize(
    "mode", ["bilingual-ok", "bilingual-fail", "figures-only", "legacy"], ids=str,
)
def test_abstract_sentinel_absent_from_every_display_mode(mode):
    paper = _paper_for_mode(mode)
    bundle = render_email_bundle([paper])
    for rendered in (bundle.html, bundle.plain_text, render_email([paper])):
        assert ABSTRACT_SENTINEL not in rendered
        assert "Abstract" not in rendered
    # The source record keeps its abstract for ranking/context; rendering never mutates it.
    assert paper.abstract == ABSTRACT_SENTINEL


def test_empty_digest_still_carries_no_abstract():
    bundle = render_email_bundle([])
    assert "No Papers Today" in bundle.html
    assert ABSTRACT_SENTINEL not in bundle.plain_text


def test_bilingual_failure_shows_single_unavailable_notice():
    paper = make_sample_paper(
        abstract=ABSTRACT_SENTINEL, tldr=ABSTRACT_SENTINEL, presentation=PaperPresentation(),
    )
    bundle = render_email_bundle([paper])
    assert bundle.html.count(TLDR_UNAVAILABLE_NOTICE) == 1
    assert bundle.plain_text.count(TLDR_UNAVAILABLE_NOTICE) == 1
    assert ABSTRACT_SENTINEL not in bundle.html


def test_missing_one_language_shows_available_once_and_notice_for_other():
    presentation = PaperPresentation(tldr=BilingualText(en=EN_TLDR, zh=None))
    paper = make_sample_paper(abstract=ABSTRACT_SENTINEL, tldr=EN_TLDR, presentation=presentation)
    bundle = render_email_bundle([paper])
    zh_notice = f"中文:</strong> {TLDR_UNAVAILABLE_NOTICE}"
    assert bundle.html.count(EN_TLDR) == 1
    assert zh_notice in bundle.html
    assert bundle.html.index(EN_TLDR) < bundle.html.index(zh_notice)
    assert bundle.plain_text.count(EN_TLDR) == 1
    assert f"中文: {TLDR_UNAVAILABLE_NOTICE}" in bundle.plain_text


def test_missing_en_shows_zh_once_and_notice_for_en():
    presentation = PaperPresentation(tldr=BilingualText(en=None, zh=ZH_TLDR))
    paper = make_sample_paper(abstract=ABSTRACT_SENTINEL, tldr=None, presentation=presentation)
    bundle = render_email_bundle([paper])
    en_notice = f"EN:</strong> {TLDR_UNAVAILABLE_NOTICE}"
    assert bundle.html.count(ZH_TLDR) == 1
    assert en_notice in bundle.html
    assert bundle.html.index(en_notice) < bundle.html.index(ZH_TLDR)
    assert bundle.plain_text.count(ZH_TLDR) == 1


# ---------------------------------------------------------------------------
# genuine summaries that happen to equal the abstract stay visible
# ---------------------------------------------------------------------------


def test_structured_tldr_equal_to_abstract_still_displays():
    presentation = PaperPresentation(
        tldr=BilingualText(en=ONE_LINE_ABSTRACT, zh="我们证明规模本身预示涌现能力。"),
    )
    paper = make_sample_paper(
        abstract=ONE_LINE_ABSTRACT, tldr=ONE_LINE_ABSTRACT, presentation=presentation,
    )
    bundle = render_email_bundle([paper])
    assert bundle.html.count(ONE_LINE_ABSTRACT) == 1
    assert bundle.plain_text.count(ONE_LINE_ABSTRACT) == 1


@pytest.mark.parametrize("mode", ["legacy", "figures-only"], ids=str)
def test_single_language_genuine_tldr_still_displays(mode):
    paper = make_sample_paper(abstract=ABSTRACT_SENTINEL, tldr=EN_TLDR)
    if mode == "figures-only":
        paper.presentation = PaperPresentation(bilingual_enabled=False)
    bundle = render_email_bundle([paper])
    assert EN_TLDR in bundle.html
    assert EN_TLDR in bundle.plain_text
    assert ABSTRACT_SENTINEL not in bundle.html


@pytest.mark.parametrize("mode", ["legacy", "figures-only"], ids=str)
def test_single_language_raw_abstract_fallback_replaced_by_notice(mode):
    paper = make_sample_paper(abstract=ABSTRACT_SENTINEL, tldr=ABSTRACT_SENTINEL)
    if mode == "figures-only":
        paper.presentation = PaperPresentation(bilingual_enabled=False)
    bundle = render_email_bundle([paper])
    assert ABSTRACT_SENTINEL not in bundle.html
    assert ABSTRACT_SENTINEL not in bundle.plain_text
    assert TLDR_UNAVAILABLE_NOTICE in bundle.html
    assert ">PDF</a>" in bundle.html  # the card itself stays intact
