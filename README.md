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
- Optional cross-run deduplication: papers you were already emailed are skipped by paper identity (valid DOI or DOI-resolver URL, else canonical arXiv ID, else normalized stable URL), with the delivery state kept Fernet-encrypted (a dedicated branch of your fork under GitHub Actions, a local file otherwise). The DOI-named settings are retained legacy names.
- Optional pre-rerank abstract enrichment for published papers missing an abstract: providers are routed directly, in the fixed priority IEEE Xplore > Elsevier > Springer Nature > PubMed, at most two matched providers are attempted per paper, and the first nonblank abstract wins.
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
   | OPENALEX_API_KEY | No, if the Crossref substitute is configured | Primary OpenAlex identity for source retrieval and venue lookups. Raw key only; the client adds the `Bearer` prefix itself. Without it and without `OPENALEX_ALLOW_ANONYMOUS=true`, a requested `openalex` source is replaced by `crossref` at startup. | your-openalex-api-key |
   | OPENALEX_API_KEY_2 | No | Standby OpenAlex key, used only after the primary is rate limited. Leave unset if you have one key. | your-backup-key |
   | NIH_API | No | PubMed (NCBI E-utilities) API key. Optional for both the `pubmed` discovery source and PubMed abstract enrichment; raises the rate cap from 3 to 10 requests/s. | your-nih-api-key |
   | IEEE_XPLORE_API | No | IEEE Xplore API key. Only needed when IEEE abstract enrichment is enabled. | your-ieee-key |
   | ELSEVIER_API | No | Elsevier API key. Only needed when Elsevier abstract enrichment is enabled. | your-elsevier-key |
   | SPRINGER_API | No | Springer Nature metadata API key. Only needed when Springer abstract enrichment is enabled. | your-springer-key |
   | SENT_DOI_STATE_KEY | When `SENT_DOI_STATE_ENABLED=true` | Fernet key encrypting the persistent sent-paper delivery state; the DOI-named variable is a retained legacy name (see GitHub Actions Deployment). Generate one with `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and store only the printed key. | your-fernet-key |

3. Add the repository **Variables** (same settings page, Variables tab). Variables are plain non-secret values, readable in the UI, which is why the config you paste below must only *reference* Secrets via `${oc.env:...}`, never contain them.
   ![vars](./assets/repo_var.png)

   | Variable | Required | Description | Example |
   | :--- | :--- | :--- | :--- |
   | CUSTOM_CONFIG | Yes | YAML configuration overlay, written to `config/custom.yaml` at run time. | see below |
   | PAPER_SOURCES | No | JSON/YAML list string that overrides `executor.source`. Order is preserved and matters: it sets retrieval priority and decides which object wins a DOI duplicate. Default `["arxiv","openalex"]`. A requested `openalex` that has no API key and no anonymous fallback is replaced once by `crossref` at startup. | `["arxiv","biorxiv","medrxiv","chemrxiv","crossref","openalex","pubmed"]` |
   | CROSSREF_MAILTO | No | Contact email for Crossref's polite pool. Required whenever a Crossref retriever is constructed: the `crossref` source, or an `openalex` source replaced at startup because it has no API key and anonymous access is off. A missing or blank value in those cases fails the run at startup. Abstract enrichment never uses Crossref. | curator@example.com |
   | ABSTRACT_ENRICHMENT_ENABLED | No | Master switch of pre-rerank abstract enrichment. Unset (and anything but `true`) resolves to `false`. | true |
   | PUBMED_ENABLED | No | Enables the PubMed abstract enrichment vertical only. It does not enable PubMed discovery; list `pubmed` in `PAPER_SOURCES` for that. Default `false`. | true |
   | PUBMED_EMAIL | No | Contact email identifying you to NCBI E-utilities; required by both the `pubmed` discovery source and PubMed enrichment (it is not a Secret). | curator@example.com |
   | PUBMED_ISSNS | No | JSON/YAML list string of ISSNs; PubMed enrichment only touches papers whose ISSNs intersect this list. Default `[]` (matches nothing). | `["0028-4793","0098-7484"]` |
   | PUBMED_QUERY | When `pubmed` is in `PAPER_SOURCES` | PubMed search query (any PubMed search syntax) driving the `pubmed` discovery source. There is no default; if it is unset or blank while `pubmed` is a source, the run fails at startup. | robotics[Title] |
   | IEEE_ENABLED | No | Enables the IEEE Xplore enrichment vertical. Default `false`. | true |
   | ELSEVIER_ENABLED | No | Enables the Elsevier enrichment vertical. Default `false`. | true |
   | SPRINGER_ENABLED | No | Enables the Springer Nature enrichment vertical. Default `false`. | true |
   | VENUE_PRESTIGE_ENABLED | No | Enables OpenAlex venue citation proxy weighting in the reranker. Default `false`. | true |
   | OPENALEX_ALLOW_ANONYMOUS | No | Only an explicit `true` lets OpenAlex fall back to keyless anonymous requests when no configured key is usable. Anything else (including unset) means no anonymous access; a requested `openalex` source with no key is then replaced by `crossref` at startup. | true |
   | SENT_DOI_STATE_ENABLED | No | Enables persistent cross-run deduplication: papers whose identity (valid DOI or DOI-resolver URL, else canonical arXiv ID, else normalized stable URL) was already emailed are skipped in later runs. The DOI-named variable is a retained legacy name. Requires the `SENT_DOI_STATE_KEY` Secret. Unset (and anything but `true`) resolves to `false`. | true |

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
        mailto: ${oc.env:CROSSREF_MAILTO,null} # Required when a Crossref retriever is constructed: the crossref source itself, or an openalex source replaced at startup for lacking credentials. Never used by abstract enrichment.
        lookback_days: 1  # Completed UTC publication days to retrieve, ending yesterday.
      openalex:
        api_keys:  # Raw keys from Secrets; null entries are skipped, at most two distinct keys.
          - ${oc.env:OPENALEX_API_KEY,null}
          - ${oc.env:OPENALEX_API_KEY_2,null}
        allow_anonymous: ${oc.decode:${oc.env:OPENALEX_ALLOW_ANONYMOUS,'false'}}
        lookback_days: 30  # Completed UTC publication days to retrieve, ending yesterday. The 30-day default mitigates OpenAlex's delayed indexing; cross-run deduplication keeps the overlap from being re-sent.
      pubmed:  # Discovery source; independent of enrichment.pubmed below.
        query: ${oc.env:PUBMED_QUERY,null} # Required when this source is enabled; there is no default query.
        contact_email: ${oc.env:PUBMED_EMAIL,null} # Required when this source is enabled.
        api_key: ${oc.env:NIH_API,null} # Optional; raises the effective rate cap from 3/s to 10/s.
        lookback_days: 3  # Completed UTC entry days to retrieve, ending yesterday.
        request_rate: 10.0  # Client-side cap; the effective rate is still 3/s anonymous, 10/s with a key.

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

    sent_doi_state:  # Persistent cross-run deduplication by paper identity; DOI-named settings are retained legacy names. The workflows also pass these three fields on the command line.
      enabled: ${oc.decode:${oc.env:SENT_DOI_STATE_ENABLED,'false'}}
      path: .state/sent-dois.fernet  # Encrypted delivery state of sent-paper identities, replaced atomically after each successful send.
      key: ${oc.env:SENT_DOI_STATE_KEY,null} # Fernet key; required when enabled.
     ```

   With this overlay pasted, configure the source set and optional feature switches with these Variables:

     ```text
     PAPER_SOURCES=["arxiv","biorxiv","medrxiv","chemrxiv","crossref","openalex","pubmed"]
     ABSTRACT_ENRICHMENT_ENABLED=true
     PUBMED_ENABLED=true
     IEEE_ENABLED=true
     ELSEVIER_ENABLED=true
     SPRINGER_ENABLED=true
     VENUE_PRESTIGE_ENABLED=true
     CROSSREF_MAILTO=curator@example.com
     PUBMED_EMAIL=curator@example.com
     PUBMED_QUERY=robotics[Title]
     PUBMED_ISSNS=["0028-4793","0098-7484"]
     SENT_DOI_STATE_ENABLED=true
     ```

   together with the provider Secrets `NIH_API`, `IEEE_XPLORE_API`, `ELSEVIER_API`, and `SPRINGER_API`. Every provider flag is independent: leaving any of them `false` (or unset) simply skips that vertical, and abstract enrichment needs no Crossref identity at all. Whenever `openalex` is in `PAPER_SOURCES` without an `OPENALEX_API_KEY` Secret and without `OPENALEX_ALLOW_ANONYMOUS=true`, the requested source is replaced once by `crossref` at startup, so `CROSSREF_MAILTO` must be set or the run fails at startup. `VENUE_PRESTIGE_ENABLED=true` needs an OpenAlex identity even when `openalex` is not a source; otherwise venue weighting is skipped with a warning. The `PUBMED_ISSNS` value above is a format example (NEJM and JAMA ISSNs), not a recommendation, and `PUBMED_QUERY=robotics[Title]` is likewise a syntax example: the query has no default, so write your own. Listing `pubmed` in `PAPER_SOURCES` is what enables PubMed discovery, and it needs `PUBMED_QUERY` plus `PUBMED_EMAIL`; `PUBMED_ENABLED` stays enrichment-only. `SENT_DOI_STATE_ENABLED=true` additionally requires the `SENT_DOI_STATE_KEY` Secret and turns on persistent cross-run deduplication of already-emailed papers (details in the GitHub Actions Deployment section).

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

If no API key is configured and `allow_anonymous` is false, the OpenAlex retriever cannot be constructed. Startup then replaces it with a single Crossref retriever (no duplicate is added when `crossref` is already configured), and that substitution is the only fallback: it happens at construction time for the missing identity, never in response to a failed OpenAlex request at runtime. The replacement makes `source.crossref.mailto` (`CROSSREF_MAILTO`) required, so a missing or blank value fails the run at startup through the usual Crossref configuration error.

`lookback_days` defaults to 30 completed UTC publication days (ending yesterday). OpenAlex keeps indexing many works for days to weeks after publication, so the wide window is the mitigation for that delay: late-indexed works still get caught. The cost is overlap, since most of each 30-day window was already retrievable by earlier runs and by other sources. Within one run the DOI merge collapses that overlap; across runs, persistent identity-based deduplication (below) keeps the overlap from being emailed twice.

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

`executor.source` (the `PAPER_SOURCES` variable) lists retrieval sources in priority order; available names are `arxiv`, `biorxiv`, `medrxiv`, `chemrxiv`, `crossref`, `openalex`, and `pubmed`. A requested `openalex` that cannot be constructed (no API key, `allow_anonymous: false`) is replaced by one Crossref retriever at startup, never as a duplicate when `crossref` is already listed. Configured sources are retrieved concurrently, at most four at a time, and each source failure is isolated: the error is logged and that source contributes nothing while the rest still run. The flattened candidate list preserves configured source order, which also decides DOI duplicates: papers sharing a normalized DOI keep the object from the earliest configured source, and the duplicate only fills fields missing on the winner (abstract, publisher, journal, `is_preprint`, plus a normalized union of ISSNs). Papers without a valid DOI pass through untouched.

The `crossref` source retrieves works from completed UTC publication days (`source.crossref.lookback_days`, ending yesterday) for the same built-in venue catalog as the OpenAlex source (a robotics/mechatronics journal expansion plus the fixed default robotics conference set, matched by exact ISSN). It requires a nonblank `source.crossref.mailto` (`CROSSREF_MAILTO`) and does not retry. The `pubmed` source instead runs your own query over completed UTC entry days (`source.pubmed.lookback_days`, default 3, ending yesterday), as described in the PubMed source section above.

Effective outbound limits, enforced client-side:

- Crossref (discovery retriever): 10 request starts per second and at most 3 requests in flight per client; no retries.
- PubMed (both the discovery source's ESearch/EFetch and enrichment): 3 requests/s anonymous, up to 10/s with `NIH_API`; no retries.
- Elsevier enrichment: hard-capped at 9 requests/s; once a response reports zero remaining weekly quota, further Elsevier requests are skipped for the rest of the run.
- IEEE and Springer enrichment: conservative default of 1 request/s (`request_rate` is configurable); this project does not assert undocumented universal daily quotas for them.
- OpenAlex Works (source retrieval): up to three attempts per request with identity failover, as described above. OpenAlex Sources (venue lookup): exactly one attempt per ISSN batch, no retry.
- LLM: SDK-level retries are disabled. Each recommended paper uses at most one request; papers without usable abstract or full text use none (see How It Works).

### Sent-DOI deduplication (encrypted delivery state)

With `sent_doi_state.enabled: true` (`SENT_DOI_STATE_ENABLED=true` plus the `SENT_DOI_STATE_KEY` Secret), the pipeline remembers the papers it has already emailed and skips them in later runs. The `sent_doi_state` section, the `SENT_DOI_*` names, the `.state/sent-dois.fernet` path, and the `sent-doi-state-v1` branch are legacy deployment names kept for compatibility; what the state holds is canonical paper identities. The state is a versioned snapshot of sorted canonical identities, encrypted with Fernet (authenticated encryption) using the key from the Secret, and written to `sent_doi_state.path` (`.state/sent-dois.fernet`) through an atomic temp-file-and-replace with 0600 permissions. Generate the key with:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Each paper is reduced to one identity, by precedence: a valid paper DOI; a DOI-resolver URL (`doi.org` or `dx.doi.org`), reduced to that DOI; a recognized arXiv `abs`/`pdf` URL on the exact host `arxiv.org` (taken from the paper URL or the PDF URL), reduced to the canonical arXiv ID; the paper URL as a canonical absolute HTTP(S) URL; otherwise the paper has no persistable identity. arXiv revision suffixes are stripped along the way, so the `v1`/`v2` and `abs`/`pdf` forms of one paper collapse to the same unversioned arXiv ID. The URL fallback is normalized narrowly: lowercase scheme and host, default-port removal (`:80` for http, `:443` for https), and fragment removal, with path and query preserved. No cross-site equivalence is attempted, so the same paper behind two unrelated pages collapses only when its DOI or arXiv ID identifies it.

Placement in the pipeline, implemented in `src/zotero_arxiv_daily/executor.py`:

- The state loads before any Zotero or source call.
- Already-sent candidates are filtered right after the cross-source DOI merge and before pre-rerank enrichment, reranking, and everything downstream, so re-found OpenAlex window overlap costs no enrichment or embedding work.
- New identities are saved only after the SMTP send succeeds, and only when at least one emailed paper has a persistable identity. A run that sends nothing, or fails before or during the send, records nothing.

Two honest limits. Papers without a persistable identity always pass through, so they can recur in later emails. And delivery is not exactly-once: if SMTP succeeds but the state upload fails, the email is already out while the workflow reports failure, so the next successful run re-sends those papers once.

State versions and lazy migration. A v1 state written by earlier versions (a plain DOI list) still loads and deduplicates as DOI identities, and loading never rewrites it. The rewrite to the current v2 schema (namespaced `doi:`/`arxiv:`/`url:` identities) is lazy: the first later send that succeeds with at least one identifiable paper saves the merged set as v2. After a v2 save, older application code can no longer read the state and fails closed, leaving the ciphertext intact, until the code is upgraded or the state is deliberately reset.

Corrupt, tampered, or undecryptable state (for example after a key change) fails the run instead of silently resetting. A missing local file starts empty; in GitHub Actions only a genuinely absent state branch starts empty, while an existing branch with a missing file fails closed. The branch lifecycle is described in the GitHub Actions Deployment section.

### Abstract enrichment (before rerank)

Master switch: `enrichment.enabled: true` (`ABSTRACT_ENRICHMENT_ENABLED=true`). Crossref plays no part here; no Crossref identity is needed or consulted. A paper is eligible only when it is confirmed published (`is_preprint is false`), its abstract is blank, and it carries a valid DOI. At most `enrichment.max_papers` eligible candidates are processed per run by `enrichment.workers` threads.

Each eligible paper is routed straight to the provider adapters, with no metadata pre-step:

1. Matching adapters are ordered by the fixed priority IEEE Xplore > Elsevier > Springer Nature > PubMed. An adapter matches when its vertical is enabled with its credentials (PubMed additionally needs its contact email) and the paper points at it: matching is by DOI prefix, publisher name, or ISSN intersection (PubMed matches by ISSN intersection only).
2. At most two matched adapters are attempted per paper, each adapter at most once. The first attempt that returns a nonblank abstract wins and enrichment stops there; if neither does, the abstract stays blank.

Do not confuse the vertical with the retrieval source of the same name. `enrichment.pubmed` is strictly a DOI-to-PMID abstract enrichment step: it resolves the DOI to a single PMID and fetches that record, and only for papers whose ISSNs intersect `enrichment.pubmed.issns` (`PUBMED_ISSNS`). `PUBMED_ENABLED` toggles only this enrichment; it never enables discovery and is never a fallback for the other providers. Discovery is the `source.pubmed` History ESearch/EFetch described in the PubMed source section above, turned on by listing `pubmed` in `PAPER_SOURCES`.

### Venue citation proxy weighting

Enabled by `reranker.venue_prestige` (`VENUE_PRESTIGE_ENABLED=true` with numeric `weight >= 0` and `max_multiplier >= 1`). Before reranking, the ISSNs of published candidates are looked up in OpenAlex Sources in batches of 100 (one attempt per batch), and each paper receives its venue's runtime `summary_stats.2yr_mean_citedness`, matched by ISSN. During scoring every candidate's score is multiplied by `min(1 + weight * log1p(proxy), max_multiplier)` and the result is clipped to the 0-10 scale; candidates with no proxy keep their raw score. Confirmed preprints (`is_preprint is true`) receive the median proxy among the venues sampled in the current candidate set, an open analogue of sitting at the Q2/Q3 boundary; when no venue was sampled, preprints stay neutral.

This proxy is OpenAlex's open citation statistics. It is not Clarivate Journal Citation Reports, not a Journal Impact Factor, and not an official quartile; no proprietary ranking table is bundled or queried, and the multiplier must not be read as one. Keep `weight` small (default 0.1, cap 1.5x) so it acts as a tie-breaker. Venue weighting needs an OpenAlex identity (an API key, or `allow_anonymous: true`) even when `openalex` is not a retrieval source; without one it is skipped with a warning.

## ⚙️ GitHub Actions Deployment

The main workflow (`.github/workflows/main.yml`, "Send emails daily") runs every day at **04:07 `Asia/Shanghai` (UTC+8)**, which is 20:07 UTC on the previous day. Edit the `schedule` block in that file to change the time. The workflow checks out code with a plain `actions/checkout` step (no `repository` or `ref` overrides), so both scheduled and manual runs always execute the workflow file and the code from the latest commit of the current repository's default branch. Any change to the schedule or the workflow must therefore be merged into that default branch before it takes effect. Scheduled runs may also be delayed when GitHub is under heavy load, and scheduled workflows only fire in your fork if Actions is enabled there.

You can also trigger "Send emails daily" manually at any time via `workflow_dispatch`. The **Test** workflow is the same pipeline with `DEBUG=true` forced, useful for validating settings; it never runs on a schedule.

### Encrypted sent-DOI state in Actions

When `SENT_DOI_STATE_ENABLED=true`, the sent state is persisted inside your repository, because the hosted runner's filesystem is thrown away after every run:

- The state lives on the dedicated branch `sent-doi-state-v1` as the single file `.state/sent-dois.fernet`, containing only Fernet ciphertext. The decrypting key exists solely in the `SENT_DOI_STATE_KEY` Secret, so no plaintext identity list is ever committed; that is what makes this safe on a public fork.
- The state branch is never checked out and never merged. A `github-script` step reads its file metadata through the Contents API, then retrieves the matching base64 ciphertext through the Git Blobs API. This supports GitHub's documented blob limit of 100 MB; larger state fails rather than being truncated. Writes also use the REST contents/git APIs, so your default branch and working tree stay untouched.
- First creation is atomic: the persist step creates the blob, a tree, and a parentless root commit, then creates the branch ref as the final call. Any failure along the way leaves no branch behind, and the next successful run creates it again. If the branch is absent and the run sends no identifiable paper, no local ciphertext is produced and persistence intentionally does nothing, leaving the branch absent.
- Updates are SHA-guarded: the load step records the state file's blob SHA and the update PUT supplies it. If the file changed in between, the update fails rather than silently overwriting.
- Loading fails closed when the branch exists but something is wrong: the state file is missing, its content is not valid base64/ciphertext, or it cannot be decrypted with the configured key (the wrong-key case after rotation). The run stops instead of starting from an empty state and re-sending everything. Enabling persistence without a key, or with a value other than `true`/`false`, also fails immediately.
- Both this workflow and the Test workflow declare the same concurrency group (`sent-doi-state-v1`, `cancel-in-progress: false`), so two runs never read or write the state at the same time; a run that arrives mid-flight waits its turn instead of being canceled. Both jobs request only the job-scoped `contents: write` permission those API calls need, disable checkout credential persistence, and execute the application with `uv run --locked`.
- The **Test** workflow shares and updates the same state. It runs the full pipeline and really sends papers, so whatever it emails is marked sent and suppressed from later emails too.

Recovery and key rotation: with persistence disabled (`SENT_DOI_STATE_ENABLED=false`), nothing is deduplicated and already-delivered papers can come back. If `SENT_DOI_STATE_KEY` is lost or replaced, the existing ciphertext can no longer be decrypted, and every run fails closed until you intentionally reset the state: delete the `sent-doi-state-v1` branch, then set the new key and re-enable. After a reset, previously sent papers may appear once more. Rolling back application code has one caveat: a v1 state keeps loading, but once any v2 save has happened (including the lazy rewrite on the next successful send) older versions of this project fail closed on the state until they are upgraded or the state is reset, never erasing it (see the Sent-DOI deduplication section).

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

Locally the overlay is read from `config/custom.yaml`; write it with the same content you would paste into `CUSTOM_CONFIG`. The `sent_doi_state` section works the same way here: enable it in the overlay (exporting `SENT_DOI_STATE_ENABLED=true` and `SENT_DOI_STATE_KEY` if you reuse the one above) and the encrypted file is written to the git-ignored `.state/sent-dois.fernet` after the first successful send containing at least one identifiable paper.

## 📖 How It Works

Each run is a linear pipeline (`src/zotero_arxiv_daily/executor.py`):

1. **Load sent state**: when persistence is enabled, the encrypted delivery state of already-emailed papers is decrypted before any Zotero or source call (details above).
2. **Fetch Zotero corpus**: all library items of type conferencePaper / journalArticle / preprint that have an abstract.
3. **Filter corpus**: keep or exclude collections via `zotero.include_path` / `zotero.ignore_path` glob patterns.
4. **Retrieve new papers concurrently**: every source listed in `executor.source` runs in parallel (up to four at once, failures isolated), the results are flattened in configured source order, and DOI duplicates are merged (details above). Papers are those announced yesterday; `crossref` and `openalex` instead use `lookback_days` completed UTC publication days ending yesterday, and `pubmed` uses `lookback_days` completed UTC entry days.
5. **Drop already-sent papers**: candidates whose canonical identity is in the loaded state are removed, after the DOI merge and before enrichment; papers without a persistable identity always pass through (details above).
6. **Pre-rerank enrichment**: optional abstract enrichment and venue citation proxies are applied to the candidate pool (details above).
7. **Rerank**: embed the **title + abstract** of every candidate and corpus paper, apply the optional venue multiplier, then score, sort, and optionally diversify candidates (details below).
8. **Keyword pinning**: papers whose title or abstract contains any `executor.pin_keywords` entry (case-insensitive) are split out and pinned; the pinned section is capped at `executor.max_pinned_num`, overflow returns to the ranked pool.
9. **Relevance floor**: papers scoring below `executor.min_score` are dropped from the ranked pool (pinned papers bypass the floor).
10. **Top-N cut**: the ranked pool is truncated to `executor.max_paper_num`.
11. **Lazy enrichment**: only for the final list (pinned + top-N), full text is fetched on demand where missing, then the TL;DR and affiliations are generated concurrently (`executor.enrich_workers` threads) through at most one strict structured JSON request per paper; papers without usable content make no request, and a failed request falls back to the abstract as the TL;DR.
12. **Render and send**: the HTML email is rendered and sent via SMTP; only after the send succeeds are the emailed papers' identities merged back into the sent state.

If nothing survives steps 4-10 and `executor.send_empty` is false, no email is sent.

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
- Third-party APIs and their quotas may change at any time. Expected request and response failures are isolated so other sources or matched providers can continue; unexpected programming errors still propagate. A quota change can effectively disable a feature.
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
