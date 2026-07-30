from .protocol import Paper
import math


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

_PIN_BADGE = '<span style="display:inline-block;font-size:12px;font-weight:600;color:#b8860b;background:#fff8e1;border:1px solid #ffe082;border-radius:4px;padding:2px 8px;margin-right:8px;vertical-align:middle;">&#128204; Pinned</span>'
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

def get_block_html(title:str, authors:str, rate:str, tldr:str, pdf_url:str, affiliations:str=None, pinned:bool=False):
    badge = _PIN_BADGE if pinned else ''
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

    <tr>
        <td style="padding: 8px 0;">
            <a href="{pdf_url}" style="display: inline-block; text-decoration: none; font-size: 14px; font-weight: bold; color: #fff; background-color: #d9534f; padding: 8px 16px; border-radius: 4px;">PDF</a>
        </td>
    </tr>
</table>
"""
    return block_template.format(badge=badge, title=title, authors=authors,rate=rate, tldr=tldr, pdf_url=pdf_url, affiliations=affiliations)

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


def _render_paper_block(p:Paper, pinned:bool=False) -> str:
    rate = round(p.score, 1) if p.score is not None else 'Unknown'
    author_list = [a for a in p.authors]
    num_authors = len(author_list)
    if num_authors <= 5:
        authors = ', '.join(author_list)
    else:
        authors = ', '.join(author_list[:3] + ['...'] + author_list[-2:])
    if p.affiliations is not None:
        affiliations = p.affiliations[:5]
        affiliations = ', '.join(affiliations)
        if len(p.affiliations) > 5:
            affiliations += ', ...'
    else:
        affiliations = 'Unknown Affiliation'
    return get_block_html(p.title, authors, rate, p.tldr, p.pdf_url, affiliations, pinned=pinned)


def render_email(papers:list[Paper]) -> str:
    if len(papers) == 0 :
        return framework.replace('__CONTENT__', get_empty_html())

    # Keyword-pinned papers are expected to come first in `papers` (the executor
    # prepends them). Render them under a distinct header so they're clearly
    # separated from the algorithm's recommendations.
    pinned = [p for p in papers if getattr(p, "pinned", False)]
    others = [p for p in papers if not getattr(p, "pinned", False)]

    parts = []
    if pinned:
        parts.append(_SECTION_HEADER.format(text="&#128204; Pinned by keywords"))
        parts.extend(_render_paper_block(p, pinned=True) for p in pinned)
        if others:
            parts.append(_SECTION_HEADER.format(text="Recommended for you"))
    parts.extend(_render_paper_block(p, pinned=False) for p in others)

    content = '<br>' + '</br><br>'.join(parts) + '</br>'
    return framework.replace('__CONTENT__', content)
