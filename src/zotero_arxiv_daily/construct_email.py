from .protocol import Paper
from .email_presentation import (
    PIN_BADGE as _PIN_BADGE,
    admit_figure_cids,
    display_tldr,
    esc,
    figure_html_for,
    format_authors,
    plain_text_digest,
    render_presentation_block,
    safe_url,
)
from .paper_presentation import InlineImage, RenderedEmail
import math

__all__ = ("render_email", "render_email_bundle", "get_stars", "get_block_html", "get_empty_html")


framework = """
<!DOCTYPE HTML>
<html>
<head>
  <style>
    .star-wrapper {
      font-size: 1.3em; /* 调整星星大小 */
      line-height: 1; /* 确保垂直对齐 */
      display: inline-flex;
      align-items: center; /* 保持对齐 */
    }
    .half-star {
      display: inline-block;
      width: 0.5em; /* 半颗星的宽度 */
      overflow: hidden;
      white-space: nowrap;
      vertical-align: middle;
    }
    .full-star {
      vertical-align: middle;
    }
  </style>
</head>
<body>

<div>
    __CONTENT__
</div>

<br><br>
<div>
To unsubscribe, remove your email in your Github Action setting.
</div>

</body>
</html>
"""

_SECTION_HEADER = '<div style="font-size:16px;font-weight:bold;color:#444;margin:12px 0 6px 0;padding-bottom:4px;border-bottom:2px solid #eee;">{text}</div>'

def get_empty_html():
  block_template = """
  <table border="0" cellpadding="0" cellspacing="0" width="100%" style="font-family: Arial, sans-serif; border: 1px solid #ddd; border-radius: 8px; padding: 16px; background-color: #f9f9f9;">
  <tr>
    <td style="font-size: 20px; font-weight: bold; color: #333;">
        No Papers Today. Take a Rest!
    </td>
  </tr>
  </table>
  """
  return block_template

def get_block_html(
    title: str,
    authors: str,
    rate: str | float,
    tldr: str | None,
    pdf_url: str,
    affiliations: str | None = None,
    pinned: bool = False,
    figure_html: str = "",
) -> str:
    badge = _PIN_BADGE if pinned else ''
    figure_row = f'<tr><td style="padding: 8px 0;">{figure_html}</td></tr>' if figure_html else ''
    pdf_href = safe_url(pdf_url)
    pdf_row = (
        """
    <tr>
        <td style="padding: 8px 0;">
            <a href="{href}" style="display: inline-block; text-decoration: none; font-size: 14px; font-weight: bold; color: #fff; background-color: #d9534f; padding: 8px 16px; border-radius: 4px;">PDF</a>
        </td>
    </tr>""".format(href=esc(pdf_href))
        if pdf_href is not None
        else ''
    )
    block_template = """
    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="font-family: Arial, sans-serif; border: 1px solid #ddd; border-radius: 8px; padding: 16px; background-color: #f9f9f9;">
    <tr>
        <td style="font-size: 20px; font-weight: bold; color: #333;">
            {badge}{title}
        </td>
    </tr>
    <tr>
        <td style="font-size: 14px; color: #666; padding: 8px 0;">
            {authors}
            <br>
            <i>{affiliations}</i>
        </td>
    </tr>
    <tr>
        <td style="font-size: 14px; color: #333; padding: 8px 0;">
            <strong>Relevance:</strong> {rate}
        </td>
    </tr>
    <tr>
        <td style="font-size: 14px; color: #333; padding: 8px 0;">
            <strong>TLDR:</strong> {tldr}
        </td>
    </tr>
    {figure_row}
    {pdf_row}
</table>
    """
    return block_template.format(badge=badge, title=esc(title), authors=esc(authors),rate=esc(rate), tldr=esc(tldr), affiliations=esc(affiliations), figure_row=figure_row, pdf_row=pdf_row)

def get_stars(score:float):
    full_star = '<span class="full-star">⭐</span>'
    half_star = '<span class="half-star">⭐</span>'
    low = 6
    high = 8
    if score <= low:
        return ''
    elif score >= high:
        return full_star * 5
    else:
        interval = (high-low) / 10
        star_num = math.ceil((score-low) / interval)
        full_star_num = int(star_num/2)
        half_star_num = star_num - full_star_num * 2
        return '<div class="star-wrapper">'+full_star * full_star_num + half_star * half_star_num + '</div>'


def _render_paper_block(p:Paper, pinned:bool=False, cid:str|None=None) -> str:
    rate = round(p.score, 1) if p.score is not None else 'Unknown'
    authors = format_authors(list(p.authors))
    if p.affiliations is not None:
        affiliations = p.affiliations[:5]
        affiliations = ', '.join(affiliations)
        if len(p.affiliations) > 5:
            affiliations += ', ...'
    else:
        affiliations = 'Unknown Affiliation'
    if p.presentation is None or not p.presentation.bilingual_enabled:
        # display_tldr guards the legacy raw-abstract fallback out of the card.
        return get_block_html(p.title, authors, rate, display_tldr(p), p.pdf_url or p.url, affiliations,
                              pinned=pinned, figure_html=figure_html_for(p.presentation, cid))
    return render_presentation_block(
        p, p.presentation,
        authors=authors, affiliations=affiliations, rate=rate, pinned=pinned, cid=cid,
    )


def render_email_bundle(papers:list[Paper], *, include_figures: bool = True) -> RenderedEmail:
    """Render the full digest: bilingual HTML, admitted inline figures and a plain-text mirror.

    With include_figures=False the standalone HTML contract applies: figure rows
    are never emitted, so the mail body carries no cid references at all.
    """
    if len(papers) == 0:
        return RenderedEmail(
            framework.replace('__CONTENT__', get_empty_html()),
            plain_text='No Papers Today. Take a Rest!',
        )

    figure_cids = admit_figure_cids(papers) if include_figures else {}

    # Keyword-pinned papers are expected to come first in `papers` (the executor
    # prepends them). Render them under a distinct header so they're clearly
    # separated from the algorithm's recommendations.
    pinned = [(i, p) for i, p in enumerate(papers) if getattr(p, "pinned", False)]
    others = [(i, p) for i, p in enumerate(papers) if not getattr(p, "pinned", False)]

    parts = []
    if pinned:
        parts.append(_SECTION_HEADER.format(text="&#128204; Pinned by keywords"))
        parts.extend(_render_paper_block(p, pinned=True, cid=figure_cids.get(i)) for i, p in pinned)
        if others:
            parts.append(_SECTION_HEADER.format(text="Recommended for you"))
    parts.extend(_render_paper_block(p, pinned=False, cid=figure_cids.get(i)) for i, p in others)

    content = '<br>' + '</br><br>'.join(parts) + '</br>'
    html = framework.replace('__CONTENT__', content)
    inline_images = []
    for index, cid in figure_cids.items():
        presentation = papers[index].presentation
        figure = presentation.first_figure if presentation is not None else None
        if figure is None:
            continue
        inline_images.append(InlineImage(cid, figure.data, figure.media_type))
    return RenderedEmail(html, tuple(inline_images), plain_text_digest(papers, figure_cids))


def render_email(papers:list[Paper]) -> str:
    """Standalone HTML: figures omitted, so the body never references a cid part."""
    return render_email_bundle(papers, include_figures=False).html
