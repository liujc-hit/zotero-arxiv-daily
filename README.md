<p align="center">
  <a href="" rel="noopener">
 <img width=200px height=200px src="assets/logo.svg" alt="logo"></a>
</p>

<h3 align="center">Zotero-arXiv-Daily</h3>

<div align="center">

  [![Status](https://img.shields.io/badge/status-active-success.svg)]()
  ![Stars](https://img.shields.io/github/stars/liujc-hit/zotero-arxiv-daily?style=flat)
  [![GitHub Issues](https://img.shields.io/github/issues/liujc-hit/zotero-arxiv-daily)](https://github.com/liujc-hit/zotero-arxiv-daily/issues)
  [![GitHub Pull Requests](https://img.shields.io/github/issues-pr/liujc-hit/zotero-arxiv-daily)](https://github.com/liujc-hit/zotero-arxiv-daily/pulls)
  [![License](https://img.shields.io/github/license/liujc-hit/zotero-arxiv-daily)](/LICENSE)
  [<img src="https://api.gitsponsors.com/api/badge/img?id=893025857" height="20">](https://api.gitsponsors.com/api/badge/link?p=PKMtRut1dWWuC1oFdJweyDSvJg454/GkdIx4IinvBblaX2AY4rQ7FYKAK1ZjApoiNhYEeduIEhfeZVIwoIVlvcwdJXVFD2nV2EE5j6lYXaT/RHrcsQbFl3aKe1F3hliP26OMayXOoZVDidl05wj+yg==)

</div>

---

<p align="center"> Daily arxiv/biorxiv/medrxiv/chemrxiv/OpenAlex paper recommendations based on your Zotero library, delivered by email.
    <br> 
</p>

> [!IMPORTANT]
> This project is under active development. Watch the repo and sync your fork with upstream regularly so you get fixes and new features.

## 🧐 About & Features

*Zotero-arXiv-Daily* ranks each day's new papers against your Zotero library and emails you the most relevant ones. Deployed as a GitHub Actions workflow it costs nothing (public repos), needs no server, and needs only a handful of repository settings.

- Free daily delivery, no installation, fully automated.
- Papers sorted by relevance to your recent reading.
- Multiple retrieval sources: `arxiv`, `biorxiv`, `medrxiv`, `chemrxiv`, and `openalex` (a built-in robotics/mechatronics venue catalog).
- AI-generated TL;DR and resolved author affiliations for every paper in the email.
- Links to PDF and code implementation (when available) in the email.
- Keyword pinning force-matches topics you care about to the top of the email.
- Curate which Zotero collections drive recommendations using glob patterns.

## 📷 Screenshot

![screenshot](./assets/screenshot.png)

## 🚀 Quick Setup

1. Fork (and star 😘) this repo.
   ![fork](./assets/fork.png)

2. Add the repository **Secrets** (Settings → Secrets and variables → Actions → Secrets). Secrets store credentials; they are masked in logs and hidden for good once saved.
   ![secrets](./assets/secrets.png)

   | Key | Required | Description | Example |
   | :--- | :--- | :--- | :--- |
   | ZOTERO_ID | Yes | Numeric user ID of your Zotero account (not your username). Get it from [security settings](https://www.zotero.org/settings/security), see this [screenshot](https://github.com/liujc-hit/zotero-arxiv-daily/blob/main/assets/userid.png). | 12345678 |
   | ZOTERO_KEY | Yes | Zotero API key with read access, from the same page. | AB5tZ877P2j7Sm2Mragq041H |
   | SENDER | Yes | Email account of the SMTP server that sends the mail. | abc@qq.com |
   | SENDER_PASSWORD | Yes | SMTP authentication code for the sender (often not your login password; ask your provider). | abcdefghijklmn |
   | RECEIVER | Yes | Address that receives the paper list. | abc@outlook.com |
   | OPENAI_API_KEY | Yes | API key for the LLM that writes the TL;DRs. Free open-model APIs available at [SiliconFlow](https://cloud.siliconflow.cn/i/b3XhBRAm). | sk-xxx |
   | OPENAI_API_BASE | Yes | Base URL of the LLM API. | https://api.siliconflow.cn/v1 |
   | OPENALEX_API_KEY | No | Primary OpenAlex API key for a higher rate limit. Raw key only; the client adds the `Bearer` prefix itself. | your-openalex-api-key |
   | OPENALEX_API_KEY_2 | No | Standby OpenAlex key, used only after the primary is rate limited. Leave unset if you have one key. | your-backup-key |

3. Add the repository **Variables** (same settings page, Variables tab). Variables are plain non-secret values, readable in the UI, which is why the config you paste below must only *reference* Secrets via `${oc.env:...}`, never contain them.
   ![vars](./assets/repo_var.png)

   | Variable | Required | Description | Example |
   | :--- | :--- | :--- | :--- |
   | CUSTOM_CONFIG | Yes | YAML configuration overlay, written to `config/custom.yaml` at run time. | see below |
   | OPENALEX_ALLOW_ANONYMOUS | No | Only an explicit `true` lets OpenAlex fall back to keyless anonymous requests when no configured key is usable. Anything else (including unset) fails closed. | true |

4. Paste this compact, deployable configuration into the value of `CUSTOM_CONFIG`:
   ![custom_config](./assets/config_var.png)

   ```yaml
   zotero:
     user_id: ${oc.env:ZOTERO_ID}
     api_key: ${oc.env:ZOTERO_KEY}
     include_path: null  # Or glob patterns, e.g. ["2026/survey/**", "2026/reading-group/**"]

   email:
     sender: ${oc.env:SENDER}
     receiver: ${oc.env:RECEIVER}
     smtp_server: smtp.qq.com
     smtp_port: 465
     sender_password: ${oc.env:SENDER_PASSWORD}

   llm:
     api:
       key: ${oc.env:OPENAI_API_KEY}
       base_url: ${oc.env:OPENAI_API_BASE}
     api_mode: chat_completion  # Or "response" for the Responses API.
     thinking: disabled  # MiniMax-M3 only; MiniMax-M2-family models must use null.
     generation_kwargs:
       model: MiniMax-M3

   source:
     arxiv:
       category: ["cs.AI", "cs.CV", "cs.LG", "cs.CL"]
       include_cross_list: false  # true also includes arXiv cross-list papers.
     openalex:
       api_keys:  # Raw keys from Secrets; null entries are skipped, at most two distinct keys.
         - ${oc.env:OPENALEX_API_KEY,null}
         - ${oc.env:OPENALEX_API_KEY_2,null}
       allow_anonymous: ${oc.decode:${oc.env:OPENALEX_ALLOW_ANONYMOUS,'false'}}
       lookback_days: 1  # Completed UTC publication days to retrieve, ending yesterday.

   executor:
     debug: ${oc.decode:${oc.env:DEBUG,null}}
     source: ['arxiv', 'openalex']
     pin_keywords: null  # Or e.g. ["Mamba", "world model"] to force-pin matching papers.
   ```

5. Manually trigger the **Test** workflow to verify everything, then check its log and the receiver inbox.
   ![test](./assets/test.png)

>[!NOTE]
> `${oc.env:XXX,yyy}` resolves to the value of environment variable `XXX`, falling back to `yyy` when unset. `${oc.decode:...}` additionally parses the result as YAML, so `'false'` becomes a boolean.

## 🔧 Configuration & Customization

The example above is intentionally compact but complete for daily use. Configuration is composed with Hydra/OmegaConf from `config/base.yaml` (defaults and full reference, every key commented) plus your `config/custom.yaml` overlay. Common extra knobs:

| Key | Effect |
| :--- | :--- |
| `zotero.ignore_path` | Glob patterns of Zotero collections to exclude from the corpus. |
| `source.biorxiv.category` / `source.medrxiv.category` | Category filters for those sources. |
| `source.chemrxiv.include_new_versions` | Also include revised versions of existing chemrxiv preprints. |
| `llm.language` | Language of the generated TL;DRs (default English). |
| `reranker.topk` | Top-k aggregation size for scoring, default 10. Lower is stricter, higher is smoother. |
| `reranker.recency_half_life_days` | Recency weighting of top-k matches, null disables. |
| `reranker.mmr_lambda` | MMR diversification of the top slots, null disables. |
| `executor.reranker` | Embedding backend: `local` (sentence-transformers) or `api` (OpenAI-compatible endpoint; set `reranker.api.*`). |
| `executor.min_score` | Drop papers scoring below this floor (0-10 scale). |
| `executor.max_paper_num` | Cap on recommended papers per email (pinned papers don't count against it). |
| `executor.pin_keywords` / `executor.max_pinned_num` | Keyword pinning and its cap. |
| `executor.send_empty` | Send an email even when nothing survived filtering. |
| `executor.enrich_workers` | Parallel workers for full-text fetching plus TL;DR/affiliation generation. |

>[!NOTE]
> MiniMax thinking: `llm.thinking: disabled` maps to `extra_body.thinking.type=disabled` in Chat Completions mode and `reasoning.effort=none` in Responses mode, and only for the exact model `MiniMax-M3`. MiniMax-M2-family models (M2, M2.1, M2.5, M2.7 and their highspeed variants) cannot disable thinking and must use `thinking: null`.

### OpenAlex source

The `openalex` source is activated purely by listing `openalex` in `executor.source`. It retrieves papers from a built-in, exact venue catalog (a robotics/mechatronics journal expansion plus a fixed default robotics conference set, matched by exact OpenAlex source IDs and ISSNs), so its only settings are `api_keys`, `allow_anonymous`, and `lookback_days`.

Client behavior, implemented in `src/zotero_arxiv_daily/retriever/openalex_client.py`:

- Keys are sent as `Authorization: Bearer <key>` headers; store the raw key in each Secret without a `Bearer ` prefix. At most two distinct keys are accepted.
- Failover: an HTTP 429 immediately and permanently advances to the next identity (key). A successful response whose `X-RateLimit-Remaining` header is `0` also advances, starting with the next request.
- Transport errors and 5xx responses are retried up to three total attempts on the same identity, without rotating keys.
- Once every identity is exhausted, the run fails closed unless anonymous fallback is explicitly enabled (`OPENALEX_ALLOW_ANONYMOUS=true` / `allow_anonymous: true`), in which case keyless requests from the shared pool are the last resort.
- A client-side sliding-window limiter caps the client at 100 request starts per second, regardless of how many keys are configured.

## ⚙️ GitHub Actions Deployment

The main workflow (`.github/workflows/main.yml`, "Send emails daily") runs every day at **04:27 `Asia/Shanghai` (UTC+8)**, which is 20:27 UTC on the previous day. Edit the `schedule` block in that file to change the time. The workflow checks out code with a plain `actions/checkout` step (no `repository` or `ref` overrides), so both scheduled and manual runs always execute the workflow file and the code from the latest commit of the current repository's default branch. Any change to the schedule or the workflow must therefore be merged into that default branch before it takes effect. Scheduled runs may also be delayed when GitHub is under heavy load, and scheduled workflows only fire in your fork if Actions is enabled there.

You can also trigger "Send emails daily" manually at any time via `workflow_dispatch`. The **Test** workflow is the same pipeline with `DEBUG=true` forced, useful for validating settings; it never runs on a schedule.

## 💻 Local Usage

With [uv](https://github.com/astral-sh/uv) installed:

```bash
git clone https://github.com/liujc-hit/zotero-arxiv-daily.git
cd zotero-arxiv-daily

# Export the same environment variables the workflow sets:
# export ZOTERO_ID=xxxx ZOTERO_KEY=xxxx SENDER=xxxx RECEIVER=xxxx
# export SENDER_PASSWORD=xxxx OPENAI_API_KEY=xxxx OPENAI_API_BASE=xxxx

uv run src/zotero_arxiv_daily/main.py
```

Locally the overlay is read from `config/custom.yaml`; write it with the same content you would paste into `CUSTOM_CONFIG`.

## 📖 How It Works

Each run is a linear pipeline (`src/zotero_arxiv_daily/executor.py`):

1. **Fetch Zotero corpus**: all library items of type conferencePaper / journalArticle / preprint that have an abstract.
2. **Filter corpus**: keep or exclude collections via `zotero.include_path` / `zotero.ignore_path` glob patterns.
3. **Retrieve new papers**: from every source listed in `executor.source` (published yesterday, or `lookback_days` for OpenAlex).
4. **Rerank**: embed the **title + abstract** of every candidate and corpus paper, then score, sort, and optionally diversify candidates (details below).
5. **Keyword pinning**: papers whose title or abstract contains any `executor.pin_keywords` entry (case-insensitive) are split out and pinned; the pinned section is capped at `executor.max_pinned_num`, overflow returns to the ranked pool.
6. **Relevance floor**: papers scoring below `executor.min_score` are dropped from the ranked pool (pinned papers bypass the floor).
7. **Top-N cut**: the ranked pool is truncated to `executor.max_paper_num`.
8. **Lazy enrichment**: only for the final list (pinned + top-N), full text is fetched on demand where missing, then TL;DR and affiliations are generated concurrently (`executor.enrich_workers` threads) through the configured LLM API.
9. **Render and send**: the HTML email is rendered and sent via SMTP.

If nothing survives steps 3-7 and `executor.send_empty` is false, no email is sent.

## 🧮 Recommendation Algorithm

Embedding similarity does the ranking. Each candidate is scored by the **mean of its top-k highest similarities** to the corpus (`reranker.topk`, default 10), where similarity is computed between title+abstract embeddings. Averaging only the best k matches keeps a paper's score driven by the slice of your library it actually aligns with, instead of diluting it across everything you have ever saved. Scores are scaled to a **0-10 range**.

Three optional refinements:

- **Recency weighting** (`reranker.recency_half_life_days`): the top-k matches are weighted by `exp(-age / half_life)` based on when each paper was added to Zotero, so your current direction dominates. `null` (default) disables it.
- **MMR diversification** (`reranker.mmr_lambda`): reorders the leading candidates by Maximal Marginal Relevance so near-duplicates don't fill consecutive email slots. Ordering only; scores are untouched. `null` (default) keeps pure score order.
- **Keyword pinning** (`executor.pin_keywords`): additive on top of the algorithm. Pinned papers appear above the recommendations, never consume `max_paper_num` slots, and are bounded by `max_pinned_num`.

Full text never participates in ranking. It is fetched only for papers that already made the email, to feed TL;DR and affiliation generation.

## 📌 Limitations

- Relevance is heuristic (embedding similarity with the knobs above). Tune `reranker.topk` / `executor.min_score` and curate the corpus via `zotero.include_path` to sharpen results.
- Runtime cost sits in enrichment and scales with the size of the final email (pinned + top-N), not with the day's retrieval volume. Very large emails can still exceed the GitHub-hosted runner quota (6 h per job on public repos, 2000 min/month on private ones). Alternatives: a self-hosted runner, your own server, or paying for the overage.
- The OpenAlex source covers exactly its built-in venue catalog; papers published outside those venues won't appear from that source.

## 📃 License

Distributed under the AGPLv3 License. See `LICENSE` for detail.

## ❤️ Acknowledgement

This project is a fork of [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily); the original project and its author deserve the credit for creating it.

- [pyzotero](https://github.com/urschrei/pyzotero)
- [arxiv](https://github.com/lukasschwab/arxiv.py)
- [sentence_transformers](https://github.com/UKPLab/sentence-transformers)
- [pymupdf4llm](https://pypi.org/project/pymupdf4llm/) (full-text parsing)
- [OpenAlex](https://openalex.org)

## ☕ Buy Me A Coffee

If you find this project helpful, welcome to sponsor me via WeChat or via [ko-fi](https://ko-fi.com/tidedra).
![wechat_qr](assets/wechat_sponsor.JPG)

## 🌟 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=liujc-hit/zotero-arxiv-daily&type=Date)](https://star-history.com/#liujc-hit/zotero-arxiv-daily&Date)
