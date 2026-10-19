# noqa: SIZE_OK - Executor is the existing single-file pipeline orchestrator.

from loguru import logger
from pyzotero import zotero
from omegaconf import DictConfig, ListConfig
from .utils import glob_match
from .retriever import get_retriever_cls
from .retriever.base import BaseRetriever
from .retriever.crossref_retriever import CrossrefRetriever
from .retriever.openalex_errors import MissingOpenAlexCredentialsError
from .protocol import CorpusPaper, Paper
from .enrichment.pipeline import build_pipeline_enrichers
from .final_enrichment import enrich_final_papers
from .presentation_config import PresentationOptions
from .paper_identity import filter_sent_paper_candidates, paper_identities
from .retrieval import retrieve_and_merge
from .sent_doi_state import build_sent_doi_state_store
from math import isfinite
from datetime import datetime
from functools import cached_property
from .reranker import get_reranker_cls
from .construct_email import render_email, render_email_bundle
from .utils import send_email
from openai import OpenAI


def _paper_matches_keywords(paper: Paper, keywords: list[str]) -> bool:
    """True if any keyword appears (case-insensitive) in the title or abstract."""
    haystack = f"{paper.title}\n{paper.abstract}".lower()
    return any(kw in haystack for kw in keywords)


def normalize_path_patterns(
    patterns: list[str] | ListConfig | str | None,
    config_key: str,
) -> list[str] | None:
    if patterns is None:
        return None

    if not isinstance(patterns, (list, ListConfig)):
        raise TypeError(
            f"config.zotero.{config_key} must be a list of glob patterns or null, "
            'for example ["2026/survey/**"]. Single strings are not supported.'
        )

    if any(not isinstance(pattern, str) for pattern in patterns):
        raise TypeError(f"config.zotero.{config_key} must contain only glob pattern strings.")

    return list(patterns)


def _build_retrievers(config: DictConfig) -> dict[str, BaseRetriever]:
    """Construct effective sources in first-occurrence configured order."""
    retrievers: dict[str, BaseRetriever] = {}
    for requested_source in config.executor.source:
        if requested_source == "crossref" and "crossref" in retrievers:
            continue

        retriever_cls = get_retriever_cls(requested_source)
        if requested_source != "openalex":
            retrievers[requested_source] = retriever_cls(config)
            continue

        try:
            openalex_retriever = retriever_cls(config)
        except MissingOpenAlexCredentialsError:
            if "crossref" not in retrievers:
                retrievers["crossref"] = CrossrefRetriever(config)
            continue
        retrievers[requested_source] = openalex_retriever
    return retrievers


class Executor:
    def __init__(self, config:DictConfig):
        self.config = config
        self.sent_doi_state_store = build_sent_doi_state_store(config)
        self.include_path_patterns = normalize_path_patterns(config.zotero.include_path, "include_path")
        self.ignore_path_patterns = normalize_path_patterns(config.zotero.ignore_path, "ignore_path")
        self.retrievers = _build_retrievers(config)
        self.pipeline_enrichers = build_pipeline_enrichers(config, self.retrievers)
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.openai_client = OpenAI(
            api_key=config.llm.api.key,
            base_url=config.llm.api.base_url,
            max_retries=0,
            timeout=45.0,
        )

    @cached_property
    def presentation_options(self) -> PresentationOptions:
        return PresentationOptions.from_config(self.config)

    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        zot = zotero.Zotero(self.config.zotero.user_id, 'user', self.config.zotero.api_key)
        collections = zot.everything(zot.collections())
        collections = {c['key']:c for c in collections}
        corpus = zot.everything(zot.items(itemType='conferencePaper || journalArticle || preprint'))
        corpus = [c for c in corpus if c['data']['abstractNote'] != '']
        def get_collection_path(col_key:str) -> str:
            if p := collections[col_key]['data']['parentCollection']:
                return get_collection_path(p) + '/' + collections[col_key]['data']['name']
            else:
                return collections[col_key]['data']['name']
        for c in corpus:
            paths = [get_collection_path(col) for col in c['data']['collections']]
            c['paths'] = paths
        logger.info(f"Fetched {len(corpus)} zotero papers")
        return [CorpusPaper(
            title=c['data']['title'],
            abstract=c['data']['abstractNote'],
            added_date=datetime.strptime(c['data']['dateAdded'], '%Y-%m-%dT%H:%M:%SZ'),
            paths=c['paths']
        ) for c in corpus]
    
    def filter_corpus(self, corpus:list[CorpusPaper]) -> list[CorpusPaper]:
        input_count = len(corpus)
        if self.include_path_patterns:
            corpus = [
                c for c in corpus
                if any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.include_path_patterns
                )
            ]
        if self.ignore_path_patterns:
            corpus = [
                c for c in corpus
                if not any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.ignore_path_patterns
                )
            ]
        if self.include_path_patterns or self.ignore_path_patterns:
            logger.info(
                "Zotero corpus filter: input={} output={} removed={} include_enabled={} ignore_enabled={}",
                input_count, len(corpus), input_count - len(corpus),
                bool(self.include_path_patterns), bool(self.ignore_path_patterns),
            )
        return corpus

    
    def run(self) -> None:
        sent_identities = self.sent_doi_state_store.load()
        corpus = self.fetch_zotero_corpus()
        corpus = self.filter_corpus(corpus)
        if len(corpus) == 0:
            logger.error(
                "No zotero papers found. Please check your zotero settings and corpus filters (api_key omitted for safety)."
            )
            return
        all_papers = retrieve_and_merge(self.retrievers)
        all_papers = filter_sent_paper_candidates(all_papers, sent_identities)
        self.pipeline_enrichers.enrich_before_rerank(all_papers)
        logger.info(f"Total {len(all_papers)} papers retrieved from all sources")
        reranked_papers: list[Paper] = []
        pinned_papers: list[Paper] = []
        if len(all_papers) > 0:
            logger.info("Reranking papers...")
            reranked_papers = self.reranker.rerank(all_papers, corpus)
            # Keyword pinning sits ON TOP of the recommendation algorithm:
            # papers matching a configured keyword and pinned_min_score go to the top of
            # the email and do NOT count against max_paper_num, so they never
            # displace the algorithm's picks. It scans the full retrieved set so
            # that eligible matches below the ordinary min_score still surface.
            pinned_papers, reranked_papers = self._select_pinned(reranked_papers)
            reranked_papers = self._apply_min_score(reranked_papers)
            reranked_papers = reranked_papers[:self.config.executor.max_paper_num]
        email_papers = pinned_papers + reranked_papers
        if len(all_papers) > 0:
            if len(email_papers) == 0 and not self.config.executor.send_empty:
                logger.info("No papers survived reranking + relevance floor. No email will be sent.")
                return
        elif not self.config.executor.send_empty:
            logger.info("No new papers found. No email will be sent.")
            return
        self._enrich_papers(email_papers)
        logger.info("Sending email...")
        options = self.presentation_options
        if options.bilingual or options.figures:
            bundle = render_email_bundle(email_papers)
            if bundle.inline_images or bundle.plain_text.strip():
                send_email(
                    self.config, bundle.html,
                    inline_images=bundle.inline_images, plain_text=bundle.plain_text,
                )
            else:
                send_email(self.config, bundle.html)
        else:
            send_email(self.config, render_email(email_papers))
        emailed_identities = paper_identities(email_papers)
        if emailed_identities:
            self.sent_doi_state_store.save(sent_identities | emailed_identities)
        logger.info("Email sent successfully")

    # ------------------------------------------------------------------
    # Relevance floor (executor.min_score)
    # ------------------------------------------------------------------

    def _min_score(self) -> float | None:
        """Resolve executor.min_score defensively; None disables the floor."""
        cfg = getattr(self, "config", None)
        executor_cfg = getattr(cfg, "executor", None) if cfg is not None else None
        if executor_cfg is None:
            return None
        try:
            val = (
                executor_cfg.get("min_score", None)
                if hasattr(executor_cfg, "get")
                else getattr(executor_cfg, "min_score", None)
            )
        except Exception:
            return None
        if val is None:
            return None
        try:
            return float(val)
        except Exception:
            return None

    def _apply_min_score(self, papers: list[Paper]) -> list[Paper]:
        """Drop papers whose reranker score is below executor.min_score.

        The floor is a precision gate: on days when nothing in the candidate
        pool is genuinely similar to the corpus, the email is left empty (or
        filled only by pinned papers) instead of padded with weak matches.
        Pinned papers bypass this filter by construction — the floor is
        applied only to the algorithm's pool, so explicit user intent wins.
        """
        floor = self._min_score()
        if floor is None or len(papers) == 0:
            return papers
        kept = [p for p in papers if (p.score if p.score is not None else 0.0) >= floor]
        dropped = len(papers) - len(kept)
        if dropped:
            logger.info(
                f"min_score filter: dropped {dropped}/{len(papers)} papers below {floor}"
            )
        return kept

    # ------------------------------------------------------------------
    # Keyword pinning
    # ------------------------------------------------------------------

    def _pin_keywords(self) -> list[str]:
        """Normalized, lowercased, non-empty pin keywords from config (or [])."""
        raw = getattr(self.config.executor, "pin_keywords", None)
        if not raw:
            return []
        keywords = [str(k).lower().strip() for k in raw]
        return [k for k in keywords if k]

    def _max_pinned_num(self) -> int:
        val = getattr(self.config.executor, "max_pinned_num", 20)
        try:
            return max(0, int(val))
        except Exception:
            return 20

    def _pinned_min_score(self) -> float:
        """Resolve the independent pinning floor; invalid values keep 4.9."""
        val = getattr(self.config.executor, "pinned_min_score", 4.9)
        if val is True or val is False:
            return 4.9
        try:
            floor = float(val)
        except (TypeError, ValueError, OverflowError):
            return 4.9
        return floor if isfinite(floor) else 4.9

    def _select_pinned(self, papers: list[Paper]) -> tuple[list[Paper], list[Paper]]:
        """Split out keyword-pinned papers.

        Returns ``(pinned, remaining)`` in the reranker's original relative order,
        including any MMR diversification. Only finite scores at or above
        ``pinned_min_score`` qualify for pinning. Pinned
        papers are additive: they do not consume ``max_paper_num`` slots. A
        ``max_pinned_num`` cap bounds the pinned section; matches beyond the cap
        flow back into ``remaining`` so they can still surface via the algorithm.
        """
        keywords = self._pin_keywords()
        floor = self._pinned_min_score()
        cap = self._max_pinned_num()
        pinned: list[Paper] = []
        remaining: list[Paper] = []
        eligible_matches = 0
        for p in papers:
            p.pinned = False
            eligible = (
                p.score is not None
                and isfinite(p.score)
                and p.score >= floor
                and _paper_matches_keywords(p, keywords)
            )
            if eligible:
                eligible_matches += 1
            if eligible and len(pinned) < cap:
                p.pinned = True
                pinned.append(p)
            else:
                remaining.append(p)

        if eligible_matches > cap:
            logger.warning(
                f"{eligible_matches} papers matched pin keywords at or above "
                f"pinned_min_score={floor}; capping the "
                f"pinned section to {cap} (max_pinned_num). The remaining "
                f"{eligible_matches - cap} will compete via the normal recommendation. "
                f"Raise max_pinned_num to keep more."
            )
        if pinned:
            logger.info(f"Pinned {len(pinned)} paper(s) by keyword match")
        return pinned, remaining

    def _enrich_papers(self, papers: list[Paper]) -> None:
        """Enrich the ordered final selection with one combined LLM call each."""
        if len(papers) == 0:
            return
        workers_config = getattr(self.config.executor, "enrich_workers", 8)
        try:
            workers = max(1, int(workers_config))
        except (TypeError, ValueError):
            workers = 8
        worker_count = min(workers, 4, len(papers))
        logger.info(
            f"Fetching full text + generating TL;DR/affiliations for "
            f"{len(papers)} papers ({worker_count} workers)..."
        )
        options = self.presentation_options
        if not options.bilingual and not options.figures:
            _ = enrich_final_papers(
                papers, self.retrievers, self.openai_client, self.config.llm, workers,
            )
            return
        _ = enrich_final_papers(
            papers,
            self.retrievers,
            self.openai_client,
            self.config.llm,
            workers,
            bilingual=options.bilingual,
            figures=options.figures,
        )
