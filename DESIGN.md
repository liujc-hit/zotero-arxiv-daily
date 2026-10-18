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
| body row | `14px #333` | relevance, TLDR rows |
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
4. `TLDR / 一句话总结:` row — labeled `EN:` and `中文:` rows carrying the
   structured TLDRs, each language exactly once; no primary/echo line before
   them.
5. Figure row — one `<img>` per paper max, `src="cid:figure-{paper-index}"`,
   `max-width:100%; height:auto`, alt = original caption, caption + `Source`
   link (http/https only) below.
6. PDF button row — unchanged from legacy.

There is no abstract row in any block, in any mode.

## Rules

- TLDR rows show the structured `tldr_en`/`tldr_zh` fields only; they are
  authoritative even when a TLDR legitimately equals the source abstract.
- Missing one TLDR language ⇒ that label's row shows the stable
  `(TLDR unavailable / 总结暂缺)` notice; the available language renders once.
  Missing both ⇒ a single notice line, never the original text or abstract
  repeated under both labels.
- Source and translated abstracts never render — HTML or plain text, bilingual,
  figures-only or legacy. A single-language TLDR that is merely the raw
  abstract fallback (`paper.tldr` equal to `paper.abstract` after trimming)
  collapses to the same unavailable notice; blank abstracts never trigger the
  guard.
- Title rows keep the original echo: a missing title translation shows
  `(translation unavailable / 翻译暂缺)` plus the original title.
- Every paper-derived string is HTML-escaped; links must be plain http(s).
- Figure bytes are never altered or translated; images travel as MIME inline
  parts (`Content-ID: <figure-N>`, `Content-Disposition: inline`) built in
  `email_message.py`. Raw images ≤ 8 MiB total (later figures dropped
  deterministically, reference removed); serialized email ≤ 25 MiB, else
  `EmailTooLargeError`.
- No animations; static email only.
- Preview harnesses may swap `cid:` for `data:` URLs — production HTML must
  reference images only via `cid:`.
