# DESIGN.md — Digest Email Contract

The email is the only visual surface of this project. Its style is fixed by the
legacy template in `src/zotero_arxiv_daily/construct_email.py`; presentation
blocks (`src/zotero_arxiv_daily/email_presentation.py`) extend it with the same
tokens. This document records that contract; it does not introduce a new one.

## Tokens

| Token | Value | Used for |
|---|---|---|
| font | `Arial, sans-serif` | all blocks |
| block surface | `border:1px solid #ddd; border-radius:8px; padding:16px; background:#f9f9f9` | paper table |
| title | `20px bold #333` | paper title heading |
| body row | `14px #333` | relevance, TLDR, abstract rows |
| subtle row | `14px #666` | authors/affiliations |
| bilingual row | `13px #555` | `EN:` / `中文:` rows |
| caption | `12px #777` | figure caption |
| accent (button) | `#d9534f` bg, white `14px bold` text, radius 4px | PDF button |
| pin badge | `12px/600 #b8860b` on `#fff8e1`, border `#ffe082`, radius 4px | pinned papers |
| section header | `16px bold #444`, `2px solid #eee` bottom border | "Pinned by keywords" / "Recommended for you" |
| link accent | `#4a90d9`, no underline | figure `Source` link |

## Block anatomy (presentation papers, in order)

1. Title row — pin badge + original title, then labeled `EN:` and `中文:` rows.
2. Authors/affiliations row — appears exactly once, author truncation preserved.
3. `Relevance:` row — unchanged from legacy.
4. `TLDR / 一句话总结:` row — original text + `EN:`/`中文:` rows.
5. `Abstract / 摘要:` row — original text + `EN:`/`中文:` rows.
6. Figure row — one `<img>` per paper max, `src="cid:figure-{paper-index}"`,
   `max-width:100%; height:auto`, alt = original caption, caption + `Source`
   link (http/https only) below.
7. PDF button row — unchanged from legacy.

## Rules

- Missing translation ⇒ `(translation unavailable / 翻译暂缺)` notice + the
  original text in that language's row. Never label untranslated text as 中文.
- Every paper-derived string is HTML-escaped; links must be plain http(s).
- Figure bytes are never altered or translated; images travel as MIME inline
  parts (`Content-ID: <figure-N>`, `Content-Disposition: inline`) built in
  `email_message.py`. Raw images ≤ 8 MiB total (later figures dropped
  deterministically, reference removed); serialized email ≤ 25 MiB, else
  `EmailTooLargeError`.
- No animations; static email only.
- Preview harnesses may swap `cid:` for `data:` URLs — production HTML must
  reference images only via `cid:`.
