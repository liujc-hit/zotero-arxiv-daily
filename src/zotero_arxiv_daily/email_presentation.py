"""Bilingual presentation block rendering for digest emails.

Owns the presentation-mode paper block: the original title plus labeled
EN/中文 title rows, the structured EN/中文 TLDR rows, and the paper's original
first figure referenced by cid. All paper-derived text is HTML-escaped, links
are restricted to plain http(s), and figure bytes are referenced by cid — never
altered, OCR-ed or translated. Source and translated abstracts are never part
of the email; a TLDR that is merely the raw-abstract fallback renders as a
stable unavailable notice. A plain-text digest mirror lives here too so HTML
and plain fallback stay in sync.
"""

from html import escape
from urllib.parse import urlparse

from .paper_presentation import BilingualText, PaperPresentation, ResearchFigure
from .protocol import Paper

__all__ = (
    "MAX_TOTAL_IMAGE_BYTES",
    "TLDR_UNAVAILABLE_NOTICE",
    "TRANSLATION_NOTICE",
    "admit_figure_cids",
    "display_tldr",
    "esc",
    "figure_html_for",
    "format_authors",
    "plain_text_digest",
    "render_presentation_block",
    "safe_url",
)

TRANSLATION_NOTICE = "(translation unavailable / 翻译暂缺)"
TLDR_UNAVAILABLE_NOTICE = "(TLDR unavailable / 总结暂缺)"
MAX_TOTAL_IMAGE_BYTES = 8 * 1024 * 1024

# Shared email style tokens, mirroring the legacy digest block in
# construct_email.py so legacy and presentation blocks look identical.
PIN_BADGE = '<span style="display:inline-block;font-size:12px;font-weight:600;color:#b8860b;background:#fff8e1;border:1px solid #ffe082;border-radius:4px;padding:2px 8px;margin-right:8px;vertical-align:middle;">&#128204; Pinned</span>'
_TABLE_STYLE = "font-family: Arial, sans-serif; border: 1px solid #ddd; border-radius: 8px; padding: 16px; background-color: #f9f9f9;"
_TITLE_STYLE = "font-size: 20px; font-weight: bold; color: #333;"
_SUBTLE_ROW_STYLE = "font-size: 14px; color: #666; padding: 8px 0;"
_ROW_STYLE = "font-size: 14px; color: #333; padding: 8px 0;"
_BILINGUAL_ROW_STYLE = "font-size: 13px; color: #555; margin-top: 4px;"
_CAPTION_STYLE = "font-size: 12px; color: #777; padding: 4px 0;"
_LINK_STYLE = "font-size:12px;color:#4a90d9;text-decoration:none;"
_BUTTON_STYLE = "display: inline-block; text-decoration: none; font-size: 14px; font-weight: bold; color: #fff; background-color: #d9534f; padding: 8px 16px; border-radius: 4px;"


def esc(value: str | float | None) -> str:
    """HTML-escape any paper-derived value before template interpolation."""
    return escape(str(value), quote=True)


def safe_url(url: str | None) -> str | None:
    """Return url only for well-formed plain http(s) URLs; malformed input yields None, never raises."""
    if not url:
        return None
    if any(ord(character) < 0x20 or character == "\x7f" for character in url):
        return None
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return url


def admit_figure_cids(papers: list[Paper]) -> dict[int, str]:
    """Deterministically map paper index -> cid for figures fitting the raw budget.

    Figures are admitted strictly in paper order; a figure is dropped (with its
    cid reference never emitted) as soon as it would push the running total
    past MAX_TOTAL_IMAGE_BYTES.
    """
    admitted: dict[int, str] = {}
    remaining = MAX_TOTAL_IMAGE_BYTES
    for index, paper in enumerate(papers):
        figure = paper.presentation.first_figure if paper.presentation is not None else None
        if figure is None:
            continue
        if len(figure.data) <= remaining:
            admitted[index] = f"figure-{index}"
            remaining -= len(figure.data)
    return admitted


def format_authors(authors: list[str]) -> str:
    """Join authors, truncating long lists to first 3 / last 2 around an ellipsis."""
    if len(authors) <= 5:
        return ", ".join(authors)
    return ", ".join(authors[:3] + ["..."] + authors[-2:])


def figure_html_for(presentation: PaperPresentation | None, cid: str | None) -> str:
    """Figure markup for an admitted cid, or empty string when nothing is admitted."""
    if presentation is None or cid is None or presentation.first_figure is None:
        return ""
    return _figure_html(cid, presentation.first_figure)


def _bilingual_rows(text: BilingualText, original: str) -> str:
    """Labeled EN/中文 rows; a missing language shows the bilingual notice plus the original text."""
    en_html = esc(text.en) if text.en is not None else f"{TRANSLATION_NOTICE} {esc(original)}"
    zh_html = esc(text.zh) if text.zh is not None else f"{TRANSLATION_NOTICE} {esc(original)}"
    return (
        f'<div style="{_BILINGUAL_ROW_STYLE}"><strong>EN:</strong> {en_html}</div>'
        f'<div style="{_BILINGUAL_ROW_STYLE}"><strong>中文:</strong> {zh_html}</div>'
    )


def _present(value: str | None) -> str | None:
    """Blank structured values count as missing, mirroring the failure path."""
    return value if value is not None and value.strip() else None


def _tldr_rows(text: BilingualText) -> str:
    """Structured TLDRs, each language exactly once; the primary payload is never echoed.

    Both languages missing collapse to a single unavailable notice instead of
    repeating the original (or the raw-abstract fallback) under both labels.
    """
    en = _present(text.en)
    zh = _present(text.zh)
    if en is None and zh is None:
        return f'<div style="{_BILINGUAL_ROW_STYLE}">{TLDR_UNAVAILABLE_NOTICE}</div>'
    en_html = esc(en) if en is not None else TLDR_UNAVAILABLE_NOTICE
    zh_html = esc(zh) if zh is not None else TLDR_UNAVAILABLE_NOTICE
    return (
        f'<div style="{_BILINGUAL_ROW_STYLE}"><strong>EN:</strong> {en_html}</div>'
        f'<div style="{_BILINGUAL_ROW_STYLE}"><strong>中文:</strong> {zh_html}</div>'
    )


def display_tldr(paper: Paper) -> str:
    """Single-language TLDR for legacy and figures-only cards.

    The legacy fallback path can leave ``paper.tldr`` holding the raw source
    abstract; that never renders, so it collapses to the stable notice. Empty
    source abstracts cannot trigger the guard, preserving real summaries.
    """
    if paper.tldr is None:
        return ""
    if paper.abstract.strip() and paper.tldr.strip() == paper.abstract.strip():
        return TLDR_UNAVAILABLE_NOTICE
    return paper.tldr


def _figure_html(cid: str, figure: ResearchFigure) -> str:
    """Inline cid reference plus the original caption and a safe source link."""
    source = safe_url(figure.source_url)
    source_link = (
        f' <a href="{esc(source)}" style="{_LINK_STYLE}">Source</a>'
        if source is not None
        else ""
    )
    return (
        f'<img src="cid:{esc(cid)}" alt="{esc(figure.caption)}" '
        f'style="max-width:100%;height:auto;border-radius:4px;">'
        f'<div style="{_CAPTION_STYLE}">{esc(figure.caption)}{source_link}</div>'
    )


def render_presentation_block(
    paper: Paper,
    presentation: PaperPresentation,
    *,
    authors: str,
    affiliations: str,
    rate: str | float,
    pinned: bool,
    cid: str | None,
) -> str:
    """Render one paper block with bilingual title/TLDR rows and its first figure.

    Structured ``presentation`` fields are authoritative; the abstract is never
    rendered in any language.
    """
    badge = PIN_BADGE if pinned else ""
    figure_html = figure_html_for(presentation, cid)
    figure_row = f'<tr><td style="padding: 8px 0;">{figure_html}</td></tr>' if figure_html else ""
    pdf_href = safe_url(paper.pdf_url or paper.url)
    pdf_button = (
        f'<a href="{esc(pdf_href)}" style="{_BUTTON_STYLE}">PDF</a>' if pdf_href is not None else ""
    )
    return f"""
    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="{_TABLE_STYLE}">
    <tr>
        <td style="{_TITLE_STYLE}">
            {badge}{esc(paper.title)}
            {_bilingual_rows(presentation.title, paper.title)}
        </td>
    </tr>
    <tr>
        <td style="{_SUBTLE_ROW_STYLE}">
            {esc(authors)}
            <br>
            <i>{esc(affiliations)}</i>
        </td>
    </tr>
    <tr>
        <td style="{_ROW_STYLE}">
            <strong>Relevance:</strong> {esc(rate)}
        </td>
    </tr>
    <tr>
        <td style="{_ROW_STYLE}">
            <strong>TLDR / 一句话总结:</strong>
            {_tldr_rows(presentation.tldr)}
        </td>
    </tr>
    {figure_row}
    <tr>
        <td style="padding: 8px 0;">
            {pdf_button}
        </td>
    </tr>
</table>
"""


def _plain_bilingual(text: BilingualText, original: str) -> str:
    en = text.en if text.en is not None else f"{TRANSLATION_NOTICE} {original}"
    zh = text.zh if text.zh is not None else f"{TRANSLATION_NOTICE} {original}"
    return f"EN: {en}\n中文: {zh}"


def _plain_tldr(text: BilingualText) -> str:
    """Plain-text mirror of the TLDR row, with the same unavailable-notice contract."""
    en = _present(text.en)
    zh = _present(text.zh)
    if en is None and zh is None:
        return f"TLDR / 一句话总结: {TLDR_UNAVAILABLE_NOTICE}"
    en_line = en if en is not None else TLDR_UNAVAILABLE_NOTICE
    zh_line = zh if zh is not None else TLDR_UNAVAILABLE_NOTICE
    return f"TLDR / 一句话总结:\nEN: {en_line}\n中文: {zh_line}"


def plain_text_digest(papers: list[Paper], figure_cids: dict[int, str]) -> str:
    """Plain-text mirror of the digest; no markup, no cid references, no abstracts."""
    sections: list[str] = []
    for index, paper in enumerate(papers):
        rate = round(paper.score, 1) if paper.score is not None else "Unknown"
        lines: list[str] = [paper.title, format_authors(list(paper.authors)), f"Relevance: {rate}"]
        presentation = paper.presentation
        if presentation is None or not presentation.bilingual_enabled:
            lines.append(f"TLDR: {display_tldr(paper)}")
        else:
            lines.append(_plain_bilingual(presentation.title, paper.title))
            lines.append(_plain_tldr(presentation.tldr))
        if presentation is not None and index in figure_cids and presentation.first_figure is not None:
            figure = presentation.first_figure
            lines.append(f"Figure: {figure.caption} ({figure.source_url})")
        lines.append(f"PDF: {paper.pdf_url or paper.url}")
        sections.append("\n".join(line for line in lines if line))
    return "\n\n".join(sections)
