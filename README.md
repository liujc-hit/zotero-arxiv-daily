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

<p align="center"> Daily arxiv/biorxiv/medrxiv/chemrxiv/Crossref/OpenAlex/PubMed paper recommendations based on your Zotero library, delivered by email.
    <br> 
</p>

> [!IMPORTANT]
> This project is under active development. Watch the repo and sync your fork with upstream regularly so you get fixes and new features.

## 🧐 About & Features

*Zotero-arXiv-Daily* ranks each day's new papers against your Zotero library and emails you the most relevant ones. Deployed as a GitHub Actions workflow it costs nothing (public repos), needs no server, and needs only a handful of repository settings.

- Free daily delivery, no installation, fully automated.
- Papers sorted by relevance to your recent reading.
- Retrieval sources: `arxiv`, `biorxiv`, `medrxiv`, `chemrxiv`, `crossref`, `openalex`, and `pubmed` (`crossref` and `openalex` draw from a built-in robotics/mechatronics venue catalog, while `pubmed` runs whatever query you write). Up to four configured sources are retrieved concurrently and DOI duplicates are merged.
- Optional pre-rerank abstract enrichment for published papers missing an abstract: Crossref first, then at most one of PubMed / IEEE Xplore / Elsevier / Springer Nature.
- Optional venue prestige weighting built from OpenAlex's open citation statistics.
- AI-generated TL;DR and resolved author affiliations for every paper in the email, through at most one strict structured LLM request per paper.
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
   | OPENALEX_API_KEY | When OpenAlex is enabled unless anonymous access is allowed | Primary OpenAlex identity for source retrieval and venue lookups. Raw key only; the client adds the `Bearer` prefix itself. | your-openalex-api-key |
   | OPENALEX_API_KEY_2 | No | Standby OpenAlex key, used only after the primary is rate limited. Leave unset if you have one key. | your-backup-key |
   | NIH_API | No | PubMed (NCBI E-utilities) API key. Optional for both the `pubmed` discovery source and PubMed abstract enrichment; raises the rate cap from 3 to 10 requests/s. | your-nih-api-key |
   | IEEE_XPLORE_API | No | IEEE Xplore API key. Only needed when IEEE abstract enrichment is enabled. | your-ieee-key |
   | ELSEVIER_API | No | Elsevier API key. Only needed when Elsevier abstract enrichment is enabled. | your-elsevier-key |
   | SPRINGER_API | No | Springer Nature metadata API key. Only needed when Springer abstract enrichment is enabled. | your-springer-key |

3. Add the repository **Variables** (same settings page, Variables tab). Variables are plain non-secret values, readable in the UI, which is why the config you paste below must only *reference* Secrets via `${oc.env:...}`, never contain them.
   ![vars](./assets/repo_var.png)

   | Variable | Required | Description | Example |
   | :--- | :--- | :--- | :--- |
   | CUSTOM_CONFIG | Yes | YAML configuration overlay, written to `config/custom.yaml` at run time. | see below |
   | PAPER_SOURCES | No | JSON/YAML list string that overrides `executor.source`. Order is preserved and matters: it sets retrieval priority and decides which object wins a DOI duplicate. Default `["arxiv","openalex"]`. | `["arxiv","biorxiv","medrxiv","chemrxiv","crossref","openalex","pubmed"]` |
   | CROSSREF_MAILTO | No | Contact email for Crossref's polite pool. Needed whenever the `crossref` source or abstract enrichment is enabled; unset means no Crossref client and abstract enrichment is skipped with a warning. | curator@example.com |
   | ABSTRACT_ENRICHMENT_ENABLED | No | Master switch of pre-rerank abstract enrichment. Unset (and anything but `true`) resolves to `false`. | true |
   | PUBMED_ENABLED | No | Enables the PubMed abstract enrichment vertical only. It does not enable PubMed discovery; list `pubmed` in `PAPER_SOURCES` for that. Default `false`. | true |
   | PUBMED_EMAIL | No | Contact email identifying you to NCBI E-utilities; required by both the `pubmed` discovery source and PubMed enrichment (it is not a Secret). | curator@example.com |
   | PUBMED_QUERY | When `pubmed` is in `PAPER_SOURCES` | PubMed search query (any PubMed search syntax) driving the `pubmed` discovery source. There is no default; if it is unset or blank while `pubmed` is a source, the run fails at startup. | robotics[Title] |
   | PUBMED_ISSNS | No | JSON/YAML list string of ISSNs; PubMed enrichment only touches papers whose ISSNs intersect this list. Default `[]` (matches nothing). | `["0028-4793","0098-7484"]` |
   | IEEE_ENABLED | No | Enables the IEEE Xplore enrichment vertical. Default `false`. | true |
   | ELSEVIER_ENABLED | No | Enables the Elsevier enrichment vertical. Default `false`. | true |
   | SPRINGER_ENABLED | No | Enables the Springer Nature enrichment vertical. Default `false`. | true |
   | VENUE_PRESTIGE_ENABLED | No | Enables OpenAlex venue citation proxy weighting in the reranker. Default `false`. | true |
   | OPENALEX_ALLOW_ANONYMOUS | No | Only an explicit `true` lets OpenAlex fall back to keyless anonymous requests when no configured key is usable. Anything else (including unset) fails closed. | true |

4. Paste this full-capability configuration into the value of `CUSTOM_CONFIG`. Every optional feature is present and wired to a repository Variable or Secret, so toggling a feature never means editing YAML:
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
      biorxiv:
        category: ["biochemistry", "animal behavior and cognition"]
      medrxiv:
        category: ["psychiatry and clinical psychology", "neurology"]
      chemrxiv:
        include_new_versions: false  # true also includes revised versions of existing preprints.
      crossref:
        mailto: ${oc.env:CROSSREF_MAILTO,null} # Required when the Crossref source or abstract enrichment is enabled.
        lookback_days: 1  # Completed UTC publication days to retrieve, ending yesterday.
      openalex:
        api_keys:  # Raw keys from Secrets; null entries are skipped, at most two distinct keys.
          - ${oc.env:OPENALEX_API_KEY,null}
          - ${oc.env:OPENALEX_API_KEY_2,null}
        allow_anonymous: ${oc.decode:${oc.env:OPENALEX_ALLOW_ANONYMOUS,'false'}}
      pubmed:  # Discovery source; independent of enrichment.pubmed below.
        query: ${oc.env:PUBMED_QUERY,null} # Required when this source is enabled; there is no default query.
        contact_email: ${oc.env:PUBMED_EMAIL,null} # Required when this source is enabled.
        api_key: ${oc.env:NIH_API,null} # Optional; raises the effective rate cap from 3/s to 10/s.
        lookback_days: 3  # Completed UTC entry days to retrieve, ending yesterday.
        request_rate: 10.0  # Client-side cap; the effective rate is still 3/s anonymous, 10/s with a key.
        lookback_days: 1  # Completed UTC publication days to retrieve, ending yesterday.

    enrichment:
      enabled: ${oc.decode:${oc.env:ABSTRACT_ENRICHMENT_ENABLED,'false'}}
      workers: 3  # Parallel enrichment threads.
      max_papers: 50  # Upper bound on papers enriched per run, before reranking.
      pubmed:
        enabled: ${oc.decode:${oc.env:PUBMED_ENABLED,'false'}}
        contact_email: ${oc.env:PUBMED_EMAIL,null}
        api_key: ${oc.env:NIH_API,null}
        request_rate: 10.0  # Effective rate is capped at 3/s anonymous, 10/s with a key.
        issns: ${oc.decode:${oc.env:PUBMED_ISSNS,'[]'}} # Only papers whose ISSNs intersect this list.
      ieee:
        enabled: ${oc.decode:${oc.env:IEEE_ENABLED,'false'}}
        api_key: ${oc.env:IEEE_XPLORE_API,null}
        request_rate: 1.0
        issns: []
        doi_prefixes: ["10.1109"]
      elsevier:
        enabled: ${oc.decode:${oc.env:ELSEVIER_ENABLED,'false'}}
        api_key: ${oc.env:ELSEVIER_API,null}
        request_rate: 9.0  # Hard cap; the run stops requesting once the weekly quota header reports zero.
        issns: []
        doi_prefixes: ["10.1016"]
      springer:
        enabled: ${oc.decode:${oc.env:SPRINGER_ENABLED,'false'}}
        api_key: ${oc.env:SPRINGER_API,null}
        request_rate: 1.0
        issns: []
        doi_prefixes: ["10.1007", "10.1038", "10.1057", "10.1186"]

    reranker:
      topk: 10
      recency_half_life_days: null  # Or e.g. 180 to weight top-k matches by Zotero age.
      mmr_lambda: null  # Or e.g. 0.7 for MMR diversification of the top slots.
      venue_prestige:
        enabled: ${oc.decode:${oc.env:VENUE_PRESTIGE_ENABLED,'false'}}
        weight: 0.1
        max_multiplier: 1.5
      local:  # Used when executor.reranker is 'local'.
        model: jinaai/jina-embeddings-v5-text-nano-retrieval
        encode_kwargs:
          task: retrieval
          prompt_name: document
      api:  # Used when executor.reranker is 'api'; fill in or leave null.
        key: null
        base_url: null
        model: null
        batch_size: null

    executor:
      debug: ${oc.decode:${oc.env:DEBUG,null}}
      send_empty: false
      max_paper_num: 100
      min_score: null  # Or e.g. 5.0 to drop weak matches (0-10 scale).
      pin_keywords: null  # Or e.g. ["Mamba", "world model"] to force-pin matching papers.
      max_pinned_num: 20
      enrich_workers: 8
      source: ${oc.decode:${oc.env:PAPER_SOURCES,'["arxiv","openalex"]'}}
      reranker: local  # Or 'api'.
    ```

   With this overlay pasted, the exact Variables that turn every new feature on are:

    ```text
    PAPER_SOURCES=["arxiv","biorxiv","medrxiv","chemrxiv","crossref","openalex","pubmed"]
    ABSTRACT_ENRICHMENT_ENABLED=true
    PUBMED_ENABLED=true
    IEEE_ENABLED=true
    ELSEVIER_ENABLED=true
    SPRINGER_ENABLED=true
    VENUE_PRESTIGE_ENABLED=true
    CROSSREF_MAILTO=curator@example.com
    PUBMED_QUERY=robotics[Title]
    PUBMED_EMAIL=curator@example.com
    PUBMED_ISSNS=["0028-4793","0098-7484"]
    ```

   together with the provider Secrets `NIH_API`, `IEEE_XPLORE_API`, `ELSEVIER_API`, and `SPRINGER_API`. Every provider flag is independent: leaving any of them `false` (or unset) simply skips that vertical, and abstract enrichment also works with `CROSSREF_MAILTO` alone (Crossref-only). OpenAlex requires an `OPENALEX_API_KEY` Secret or `OPENALEX_ALLOW_ANONYMOUS=true` whenever `openalex` is in `PAPER_SOURCES`. `VENUE_PRESTIGE_ENABLED=true` has the same identity requirement even when `openalex` is not a source; otherwise venue weighting is skipped with a warning. The `PUBMED_ISSNS` value above is a format example (NEJM and JAMA ISSNs), not a recommendation, and `PUBMED_QUERY=robotics[Title]` is likewise a syntax example: the query has no default, so write your own. Listing `pubmed` in `PAPER_SOURCES` is what enables PubMed discovery, and it needs `PUBMED_QUERY` plus `PUBMED_EMAIL`; `PUBMED_ENABLED` stays enrichment-only.

5. Manually trigger the **Test** workflow to verify everything, then check its log and the receiver inbox.
   ![test](./assets/test.png)

>[!NOTE]
> `${oc.env:XXX,yyy}` resolves to the value of environment variable `XXX`, falling back to `yyy` when unset. `${oc.decode:...}` additionally parses the result as YAML, so `'false'` becomes a boolean.

## 🔧 Configuration & Customization

Configuration is composed with Hydra/OmegaConf from `config/base.yaml` (defaults and full reference, every key commented) plus your `config/custom.yaml` overlay. The full-capability example above already shows every section; the two knobs it leaves at `null`:

| Key | Effect |
| :--- | :--- |
| `zotero.ignore_path` | Glob patterns of Zotero collections to exclude from the corpus. |
| `llm.language` | Language of the generated TL;DRs (default English). |

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
### PubMed source

The `pubmed` source is activated by listing `pubmed` in `executor.source`; `PUBMED_ENABLED` has nothing to do with it (that variable is enrichment-only). Unlike `crossref` and `openalex` with their fixed venue catalog, retrieval is query-driven: `source.pubmed.query` (`PUBMED_QUERY`) accepts any PubMed search syntax, has no default, and is required together with `source.pubmed.contact_email` (`PUBMED_EMAIL`, the same variable the enrichment vertical uses) whenever the source is enabled. `source.pubmed.api_key` (`NIH_API`) is optional and only raises the rate cap.

Discovery, implemented in `src/zotero_arxiv_daily/retriever/pubmed_client.py`:

- One ESearch with `usehistory=y` over completed UTC entry days (`source.pubmed.lookback_days`, default 3, ending yesterday, filtered by entry date), then paged EFetch of the stored History result, 200 records per page. No request is retried.
- EFetch responses may interleave journal records (`PubmedArticle`) and book chapters (`PubmedBookArticle`); both are accepted in response order. The canonical PubMed publication type UI `D000076942` marks a record as a preprint, other explicit publication types mark it as published, and missing publication-type metadata remains unknown.
- The fetched record count must reproduce the ESearch count and PMIDs must be unique; otherwise the source fails rather than returning a partial result.
- Fail-closed cap: an ESearch reporting more than 10,000 records fails the source instead of silently truncating. Narrow the query or the window if you hit it.
- The effective rate is `min(request_rate, 3/s)` without an API key and `min(request_rate, 10/s)` with one, so the default `request_rate: 10.0` means 10/s keyed and 3/s anonymous.
- Runtime failures (request errors, the cap above, count mismatches) are isolated like any other source: logged, and that source contributes nothing while the rest still run. Bad configuration is not isolated: a blank query or contact email while `pubmed` is listed fails the whole run at startup. In debug mode (the Test workflow) the source contributes at most its first 10 records.

This is History-based discovery over PubMed entry dates; `enrichment.pubmed` (below) is still DOI-to-PMID abstract enrichment. They share `NIH_API`, `PUBMED_EMAIL`, and the 3/s versus 10/s rate vocabulary, but the two features are enabled and configured independently.


### Concurrent retrieval and DOI merge

`executor.source` (the `PAPER_SOURCES` variable) lists retrieval sources in priority order; available names are `arxiv`, `biorxiv`, `medrxiv`, `chemrxiv`, `crossref`, `openalex`, and `pubmed`. Configured sources are retrieved concurrently, at most four at a time, and each source failure is isolated: the error is logged and that source contributes nothing while the rest still run. The flattened candidate list preserves configured source order, which also decides DOI duplicates: papers sharing a normalized DOI keep the object from the earliest configured source, and the duplicate only fills fields missing on the winner (abstract, publisher, journal, `is_preprint`, plus a normalized union of ISSNs). Papers without a valid DOI pass through untouched.

The `crossref` source retrieves works from completed UTC publication days (`source.crossref.lookback_days`, ending yesterday) for the same built-in venue catalog as the OpenAlex source (a robotics/mechatronics journal expansion plus the fixed default robotics conference set, matched by exact ISSN). It requires a nonblank `source.crossref.mailto` (`CROSSREF_MAILTO`) and does not retry. The `pubmed` source instead runs your own query over completed UTC entry days (`source.pubmed.lookback_days`, default 3, ending yesterday), as described in the PubMed source section above.

Effective outbound limits, enforced client-side:

- Crossref (both the discovery retriever and the abstract-enrichment lookups): 10 request starts per second and at most 3 requests in flight per client; no retries.
- PubMed (both the discovery source's ESearch/EFetch and enrichment): 3 requests/s anonymous, up to 10/s with `NIH_API`; no retries.
- Elsevier enrichment: hard-capped at 9 requests/s; once a response reports zero remaining weekly quota, further Elsevier requests are skipped for the rest of the run.
- IEEE and Springer enrichment: conservative default of 1 request/s (`request_rate` is configurable); this project does not assert undocumented universal daily quotas for them.
- OpenAlex Works (source retrieval): up to three attempts per request with identity failover, as described above. OpenAlex Sources (venue lookup): exactly one attempt per ISSN batch, no retry.
- LLM: SDK-level retries are disabled. Each recommended paper uses at most one request; papers without usable abstract or full text use none (see How It Works).

### Abstract enrichment (before rerank)

Master switch: `enrichment.enabled: true` (`ABSTRACT_ENRICHMENT_ENABLED=true`) plus a usable Crossref identity (`CROSSREF_MAILTO`). A paper is eligible only when it is confirmed published (`is_preprint is false`), its abstract is blank, and it carries a valid DOI. At most `enrichment.max_papers` eligible candidates are processed per run by `enrichment.workers` threads.

Each eligible paper is enriched in two steps:

1. Crossref metadata: one `works/{doi}` lookup fills missing publisher, ISSNs, and `is_preprint` on the paper and takes the Crossref abstract when present. If Crossref returned an abstract, the paper is done.
2. Otherwise at most one vertical adapter is selected, in the fixed priority PubMed > IEEE > Elsevier > Springer, matched by DOI prefix, publisher name, or ISSN intersection. Each provider gets exactly one attempt; there is no cross-provider retry or fallback, and a failed or empty attempt just leaves the abstract blank.

Do not confuse the vertical with the retrieval source of the same name. `enrichment.pubmed` is strictly a DOI-to-PMID abstract enrichment step: it resolves the DOI to a single PMID and fetches that record, and only for papers whose ISSNs intersect `enrichment.pubmed.issns` (`PUBMED_ISSNS`). `PUBMED_ENABLED` toggles only this enrichment; it never enables discovery and is never a fallback for the other providers. Discovery is the `source.pubmed` History ESearch/EFetch described in the PubMed source section above, turned on by listing `pubmed` in `PAPER_SOURCES`.

### Venue citation proxy weighting

Enabled by `reranker.venue_prestige` (`VENUE_PRESTIGE_ENABLED=true` with numeric `weight >= 0` and `max_multiplier >= 1`). Before reranking, the ISSNs of published candidates are looked up in OpenAlex Sources in batches of 100 (one attempt per batch), and each paper receives its venue's runtime `summary_stats.2yr_mean_citedness`, matched by ISSN. During scoring every candidate's score is multiplied by `min(1 + weight * log1p(proxy), max_multiplier)` and the result is clipped to the 0-10 scale; candidates with no proxy keep their raw score. Confirmed preprints (`is_preprint is true`) receive the median proxy among the venues sampled in the current candidate set, an open analogue of sitting at the Q2/Q3 boundary; when no venue was sampled, preprints stay neutral.

This proxy is OpenAlex's open citation statistics. It is not Clarivate Journal Citation Reports, not a Journal Impact Factor, and not an official quartile; no proprietary ranking table is bundled or queried, and the multiplier must not be read as one. Keep `weight` small (default 0.1, cap 1.5x) so it acts as a tie-breaker. Venue weighting needs an OpenAlex identity (an API key, or `allow_anonymous: true`) even when `openalex` is not a retrieval source; without one it is skipped with a warning.

## ⚙️ GitHub Actions Deployment

The main workflow (`.github/workflows/main.yml`, "Send emails daily") runs every day at **04:07 `Asia/Shanghai` (UTC+8)**, which is 20:07 UTC on the previous day. Edit the `schedule` block in that file to change the time. The workflow checks out code with a plain `actions/checkout` step (no `repository` or `ref` overrides), so both scheduled and manual runs always execute the workflow file and the code from the latest commit of the current repository's default branch. Any change to the schedule or the workflow must therefore be merged into that default branch before it takes effect. Scheduled runs may also be delayed when GitHub is under heavy load, and scheduled workflows only fire in your fork if Actions is enabled there.

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
3. **Retrieve new papers concurrently**: every source listed in `executor.source` runs in parallel (up to four at once, failures isolated), the results are flattened in configured source order, and DOI duplicates are merged (details above). Papers are those announced yesterday; `crossref` and `openalex` instead use `lookback_days` completed UTC publication days ending yesterday.
4. **Pre-rerank enrichment**: optional abstract enrichment and venue citation proxies are applied to the candidate pool (details above).
5. **Rerank**: embed the **title + abstract** of every candidate and corpus paper, apply the optional venue multiplier, then score, sort, and optionally diversify candidates (details below).
6. **Keyword pinning**: papers whose title or abstract contains any `executor.pin_keywords` entry (case-insensitive) are split out and pinned; the pinned section is capped at `executor.max_pinned_num`, overflow returns to the ranked pool.
7. **Relevance floor**: papers scoring below `executor.min_score` are dropped from the ranked pool (pinned papers bypass the floor).
8. **Top-N cut**: the ranked pool is truncated to `executor.max_paper_num`.
9. **Lazy enrichment**: only for the final list (pinned + top-N), full text is fetched on demand where missing, then the TL;DR and affiliations are generated concurrently (`executor.enrich_workers` threads) through at most one strict structured JSON request per paper; papers without usable content make no request, and a failed request falls back to the abstract as the TL;DR.
10. **Render and send**: the HTML email is rendered and sent via SMTP.

If nothing survives steps 3-8 and `executor.send_empty` is false, no email is sent.

## 🧮 Recommendation Algorithm

Embedding similarity does the ranking. Each candidate is scored by the **mean of its top-k highest similarities** to the corpus (`reranker.topk`, default 10), where similarity is computed between title+abstract embeddings. Averaging only the best k matches keeps a paper's score driven by the slice of your library it actually aligns with, instead of diluting it across everything you have ever saved. Scores are scaled to a **0-10 range**.

Four optional refinements:

- **Recency weighting** (`reranker.recency_half_life_days`): the top-k matches are weighted by `exp(-age / half_life)` based on when each paper was added to Zotero, so your current direction dominates. `null` (default) disables it.
- **MMR diversification** (`reranker.mmr_lambda`): reorders the leading candidates by Maximal Marginal Relevance so near-duplicates don't fill consecutive email slots. Ordering only; scores are untouched. MMR reuses the candidate embeddings already computed for scoring, so no duplicate model load or embedding pass occurs. `null` (default) keeps pure score order.
- **Venue prestige** (`reranker.venue_prestige`): multiplies scores by a bounded factor derived from OpenAlex's open per-venue citation statistics (details above). Default off.
- **Keyword pinning** (`executor.pin_keywords`): additive on top of the algorithm. Pinned papers appear above the recommendations, never consume `max_paper_num` slots, and are bounded by `max_pinned_num`.

Full text never participates in ranking. It is fetched only for papers that already made the email, to feed TL;DR and affiliation generation.

## 📌 Limitations

- Relevance is heuristic (embedding similarity with the knobs above). Tune `reranker.topk` / `executor.min_score` and curate the corpus via `zotero.include_path` to sharpen results.
- Runtime cost sits in two bounded places: pre-rerank abstract enrichment, capped by `enrichment.max_papers` regardless of the day's retrieval volume, and the final stage (lazy full text plus one LLM request per paper), which scales with the selected output (pinned + top-N), not with retrieval volume. Very large emails can still exceed the GitHub-hosted runner quota (6 h per job on public repos, 2000 min/month on private ones). Alternatives: a self-hosted runner, your own server, or paying for the overage.
- Third-party APIs and their quotas may change at any time. Failing sources and providers are skipped gracefully, but a quota change can effectively disable a feature.
- The `openalex` and `crossref` sources cover exactly the built-in venue catalog; papers published outside those venues won't appear from those sources.

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
