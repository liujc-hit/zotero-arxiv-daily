"""Regression tests: bilingual title rows never duplicate the original heading.

Pins the digest fix for the reported bug where an English paper's EN
"translation" came back equal to the original title and was repeated in small
text beneath the bold heading, and where a missing translation echoed the
original title after the translation notice. The HTML block and the
plain-text mirror must obey the same contract.
"""

import pytest

from zotero_arxiv_daily.construct_email import render_email_bundle
from zotero_arxiv_daily.email_presentation import TRANSLATION_NOTICE
from zotero_arxiv_daily.paper_presentation import BilingualText, PaperPresentation
from tests.canned_responses import make_sample_paper


def make_titled_paper(title: str = "Sample Paper Title", en: str | None = None, zh: str | None = None):
    """Paper whose structured title translations are set per test."""
    return make_sample_paper(
        title=title,
        presentation=PaperPresentation(title=BilingualText(en=en, zh=zh)),
    )


# ---------------------------------------------------------------------------
# a translation equal to the heading drops its row (the reported duplicate)
# ---------------------------------------------------------------------------


def test_en_title_equal_to_heading_is_omitted_from_html_and_plain():
    bundle = render_email_bundle([make_titled_paper(en="Sample Paper Title", zh="示例论文标题")])
    assert bundle.html.count("Sample Paper Title") == 1
    assert bundle.plain_text.count("Sample Paper Title") == 1
    assert "示例论文标题" in bundle.html
    assert "中文: 示例论文标题" in bundle.plain_text


def test_zh_title_equal_to_heading_is_omitted_from_html_and_plain():
    bundle = render_email_bundle(
        [make_titled_paper(title="中文论文标题", en="An English Title", zh="中文论文标题")]
    )
    assert bundle.html.count("中文论文标题") == 1
    assert bundle.plain_text.count("中文论文标题") == 1
    assert "An English Title" in bundle.html


def test_translation_equal_after_outer_trim_is_omitted():
    bundle = render_email_bundle([make_titled_paper(en="  Sample Paper Title  ", zh="示例论文标题")])
    assert bundle.html.count("Sample Paper Title") == 1
    assert bundle.plain_text.count("Sample Paper Title") == 1


def test_both_translations_equal_to_heading_drop_every_title_row():
    bundle = render_email_bundle(
        [make_titled_paper(en="Sample Paper Title", zh="Sample Paper Title")]
    )
    assert bundle.html.count("Sample Paper Title") == 1
    assert "EN: Sample Paper Title" not in bundle.plain_text
    assert "中文: Sample Paper Title" not in bundle.plain_text
    assert "Relevance:" in bundle.html  # the card structure stays intact


# ---------------------------------------------------------------------------
# near-misses stay visible: case and internal whitespace are significant
# ---------------------------------------------------------------------------


def test_case_difference_from_heading_is_preserved():
    bundle = render_email_bundle([make_titled_paper(en="sample paper title", zh="示例论文标题")])
    assert "<strong>EN:</strong> sample paper title" in bundle.html
    assert "EN: sample paper title" in bundle.plain_text


def test_internal_whitespace_difference_is_preserved_verbatim():
    bundle = render_email_bundle([make_titled_paper(en="Sample  Paper Title", zh="示例论文标题")])
    assert "<strong>EN:</strong> Sample  Paper Title" in bundle.html
    assert "EN: Sample  Paper Title" in bundle.plain_text


# ---------------------------------------------------------------------------
# distinct translations render once under their labels, HTML and plain
# ---------------------------------------------------------------------------


def test_distinct_translations_render_once_under_their_labels():
    bundle = render_email_bundle([make_titled_paper(en="A Different English Title", zh="不同的中文标题")])
    heading = bundle.html.index("Sample Paper Title")
    en_row = bundle.html.index("<strong>EN:</strong> A Different English Title")
    zh_row = bundle.html.index("<strong>中文:</strong> 不同的中文标题")
    assert heading < en_row < zh_row
    assert "EN: A Different English Title" in bundle.plain_text
    assert "中文: 不同的中文标题" in bundle.plain_text


def test_distinct_title_translation_is_html_escaped():
    bundle = render_email_bundle([make_titled_paper(en="<script>alert(1)</script>", zh="示例论文标题")])
    assert "<script>" not in bundle.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in bundle.html


# ---------------------------------------------------------------------------
# missing or blank translations: notice only, never the original echo
# ---------------------------------------------------------------------------


def test_missing_en_title_shows_notice_only_without_echo():
    bundle = render_email_bundle([make_titled_paper(en=None, zh="示例论文标题")])
    assert bundle.html.count("Sample Paper Title") == 1
    assert f"<strong>EN:</strong> {TRANSLATION_NOTICE}" in bundle.html
    assert f"EN: {TRANSLATION_NOTICE}" in bundle.plain_text
    assert bundle.plain_text.count("Sample Paper Title") == 1


def test_missing_zh_title_shows_notice_only_without_echo():
    bundle = render_email_bundle([make_titled_paper(en="An English Title", zh=None)])
    assert bundle.html.count("Sample Paper Title") == 1
    assert f"<strong>中文:</strong> {TRANSLATION_NOTICE}" in bundle.html
    assert f"中文: {TRANSLATION_NOTICE}" in bundle.plain_text


def test_both_translations_missing_show_notice_rows_without_echo():
    bundle = render_email_bundle([make_titled_paper(en=None, zh=None)])
    assert bundle.html.count("Sample Paper Title") == 1
    assert f"<strong>EN:</strong> {TRANSLATION_NOTICE}" in bundle.html
    assert f"<strong>中文:</strong> {TRANSLATION_NOTICE}" in bundle.html
    assert bundle.plain_text.count("Sample Paper Title") == 1


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_translations_are_treated_as_missing(blank: str):
    bundle = render_email_bundle([make_titled_paper(en=blank, zh=blank)])
    assert bundle.html.count("Sample Paper Title") == 1
    assert f"<strong>EN:</strong> {TRANSLATION_NOTICE}" in bundle.html
    assert f"中文: {TRANSLATION_NOTICE}" in bundle.plain_text


# ---------------------------------------------------------------------------
# plain-text mirror keeps one line per surviving row, in block order
# ---------------------------------------------------------------------------


def test_plain_digest_block_structure_with_dropped_en_row():
    bundle = render_email_bundle([make_titled_paper(en="Sample Paper Title", zh="示例论文标题")])
    paper_section = bundle.plain_text.split("\n\n")[0]
    assert paper_section.splitlines() == [
        "Sample Paper Title",
        "Author A, Author B, Author C",
        "Relevance: Unknown",
        "中文: 示例论文标题",
        "TLDR / 一句话总结: (TLDR unavailable / 总结暂缺)",
        "PDF: https://arxiv.org/pdf/2026.00001",
    ]
