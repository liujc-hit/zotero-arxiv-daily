"""Tests for zotero_arxiv_daily.construct_email: render_email, get_stars, get_block_html."""

from zotero_arxiv_daily.construct_email import render_email, get_stars, get_block_html, get_empty_html
from tests.canned_responses import make_sample_paper


def test_render_email_with_papers():
    papers = [make_sample_paper(score=7.5, tldr="A great paper.", affiliations=["MIT"])]
    html = render_email(papers)
    assert "Sample Paper Title" in html
    assert "A great paper." in html
    assert "MIT" in html


def test_render_email_empty_list():
    html = render_email([])
    assert "No Papers Today" in html


def test_render_email_author_truncation():
    authors = [f"Author {i}" for i in range(10)]
    paper = make_sample_paper(authors=authors, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Author 0" in html
    assert "Author 1" in html
    assert "Author 2" in html
    assert "..." in html
    assert "Author 8" in html
    assert "Author 9" in html
    # Middle authors should be truncated
    assert "Author 5" not in html


def test_render_email_affiliation_truncation():
    affiliations = [f"Uni {i}" for i in range(8)]
    paper = make_sample_paper(affiliations=affiliations, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Uni 0" in html
    assert "Uni 4" in html
    assert "..." in html
    assert "Uni 7" not in html


def test_render_email_no_affiliations():
    paper = make_sample_paper(affiliations=None, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Unknown Affiliation" in html


def test_get_stars_low_score():
    assert get_stars(5.0) == ""
    assert get_stars(6.0) == ""


def test_get_stars_high_score():
    stars = get_stars(8.0)
    assert stars.count("full-star") == 5


def test_get_stars_mid_score():
    stars = get_stars(7.0)
    assert "star" in stars
    assert stars.count("full-star") + stars.count("half-star") > 0


def test_get_block_html_contains_all_fields():
    html = get_block_html("Title", "Auth", "3.5", "Summary", "http://pdf.url", "MIT")
    assert "Title" in html
    assert "Auth" in html
    assert "3.5" in html
    assert "Summary" in html
    assert "http://pdf.url" in html
    assert "MIT" in html


def test_get_empty_html():
    html = get_empty_html()
    assert "No Papers Today" in html


def test_render_email_pinned_section_appears_above_recommendations():
    pinned = make_sample_paper(title="Pinned Paper", score=5.0, tldr="pin tldr")
    pinned.pinned = True
    other = make_sample_paper(title="Other Paper", score=8.0, tldr="rec tldr")
    html = render_email([pinned, other])
    # section headers present and in the right order
    assert "Pinned by keywords" in html
    assert "Recommended for you" in html
    assert html.index("Pinned by keywords") < html.index("Recommended for you")
    # pinned block carries the pin badge, non-pinned does not
    pinned_block_start = html.index("Pinned Paper")
    rec_block_start = html.index("Other Paper")
    # the badge markup appears before the pinned title and not before the rec title
    assert "Pinned" in html[pinned_block_start - 200 : pinned_block_start]
    assert html[rec_block_start - 200 : rec_block_start].count("Pinned") == 0


def test_render_email_only_pinned_no_recommendations_header():
    pinned = make_sample_paper(title="Solo Pinned", score=3.0, tldr="t")
    pinned.pinned = True
    html = render_email([pinned])
    assert "Pinned by keywords" in html
    # no "Recommended for you" section when there are no other papers
    assert "Recommended for you" not in html
    assert "Solo Pinned" in html


def test_get_block_html_pinned_flag_adds_badge():
    plain = get_block_html("T", "A", "1.0", "s", "http://x", "MIT", pinned=False)
    pinned = get_block_html("T", "A", "1.0", "s", "http://x", "MIT", pinned=True)
    assert "Pinned" not in plain
    assert "Pinned" in pinned
