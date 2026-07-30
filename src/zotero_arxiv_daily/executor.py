from loguru import logger
from pyzotero import zotero
from omegaconf import DictConfig, ListConfig
from .utils import glob_match
from .retriever import get_retriever_cls
from .protocol import CorpusPaper, Paper
import random
from datetime import datetime
from .reranker import get_reranker_cls
from .construct_email import render_email
from .utils import send_email
from openai import OpenAI
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor


def _paper_matches_keywords(paper: Paper, keywords: list[str]) -> bool:
    """True if any keyword appears (case-insensitive) in the title or abstract."""
    haystack = f"{paper.title}\n{paper.abstract}".lower()
    return any(kw in haystack for kw in keywords)


def normalize_path_patterns(patterns: list[str] | ListConfig | None, config_key: str) -> list[str] | None:
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


class Executor:
    def __init__(self, config:DictConfig):
        self.config = config
        self.include_path_patterns = normalize_path_patterns(config.zotero.include_path, "include_path")
        self.ignore_path_patterns = normalize_path_patterns(config.zotero.ignore_path, "ignore_path")
        self.retrievers = {
            source: get_retriever_cls(source)(config) for source in config.executor.source
        }
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.openai_client = OpenAI(api_key=config.llm.api.key, base_url=config.llm.api.base_url)
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
        if self.include_path_patterns:
            logger.info(f"Selecting zotero papers matching include_path: {self.include_path_patterns}")
            corpus = [
                c for c in corpus
                if any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.include_path_patterns
                )
            ]
        if self.ignore_path_patterns:
            logger.info(f"Excluding zotero papers matching ignore_path: {self.ignore_path_patterns}")
            corpus = [
                c for c in corpus
                if not any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.ignore_path_patterns
                )
            ]
        if self.include_path_patterns or self.ignore_path_patterns:
            samples = random.sample(corpus, min(5, len(corpus)))
            samples = '\n'.join([c.title + ' - ' + '\n'.join(c.paths) for c in samples])
            logger.info(f"Selected {len(corpus)} zotero papers:\n{samples}\n...")
        return corpus

    
    def run(self):
        corpus = self.fetch_zotero_corpus()
        corpus = self.filter_corpus(corpus)
        if len(corpus) == 0:
            logger.error(f"No zotero papers found. Please check your zotero settings:\n{self.config.zotero}")
            return
        all_papers = []
        for source, retriever in self.retrievers.items():
            logger.info(f"Retrieving {source} papers...")
            papers = retriever.retrieve_papers()
            if len(papers) == 0:
                logger.info(f"No {source} papers found")
                continue
            logger.info(f"Retrieved {len(papers)} {source} papers")
            all_papers.extend(papers)
        logger.info(f"Total {len(all_papers)} papers retrieved from all sources")
        reranked_papers = []
        pinned_papers: list[Paper] = []
        if len(all_papers) > 0:
            logger.info("Reranking papers...")
            reranked_papers = self.reranker.rerank(all_papers, corpus)
            # Keyword pinning sits ON TOP of the recommendation algorithm:
            # papers matching a configured keyword are force-pinned to the top of
            # the email and do NOT count against max_paper_num, so they never
            # displace the algorithm's picks. It scans the full retrieved set so
            # that low-scored-but-keyword-matched papers are still surfaced.
            pinned_papers, reranked_papers = self._select_pinned(reranked_papers)
            reranked_papers = reranked_papers[:self.config.executor.max_paper_num]
            self._enrich_papers(pinned_papers + reranked_papers)
        elif not self.config.executor.send_empty:
            logger.info("No new papers found. No email will be sent.")
            return
        logger.info("Sending email...")
        email_content = render_email(pinned_papers + reranked_papers)
        send_email(self.config, email_content)
        logger.info("Email sent successfully")

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

    def _select_pinned(self, papers: list[Paper]) -> tuple[list[Paper], list[Paper]]:
        """Split out keyword-pinned papers.

        Returns ``(pinned, remaining)``. ``papers`` is assumed score-sorted
        (as produced by the reranker); both outputs preserve that order. Pinned
        papers are additive: they do not consume ``max_paper_num`` slots. A
        ``max_pinned_num`` cap bounds the pinned section; matches beyond the cap
        flow back into ``remaining`` so they can still surface via the algorithm.
        """
        keywords = self._pin_keywords()
        if not keywords or len(papers) == 0:
            return [], papers

        pinned: list[Paper] = []
        remaining: list[Paper] = []
        for p in papers:
            if _paper_matches_keywords(p, keywords):
                p.pinned = True
                pinned.append(p)
            else:
                remaining.append(p)

        cap = self._max_pinned_num()
        if len(pinned) > cap:
            overflow = pinned[cap:]
            pinned = pinned[:cap]
            # papers is score-sorted, so overflow entries are the lower-scored
            # matches; prepending keeps remaining score-sorted for the top-N slice.
            remaining = overflow + remaining
            logger.warning(
                f"{cap + len(overflow)} papers matched pin keywords; capping the "
                f"pinned section to {cap} (max_pinned_num). The remaining "
                f"{len(overflow)} will compete via the normal recommendation. "
                f"Raise max_pinned_num to keep more."
            )
        if pinned:
            logger.info(f"Pinned {len(pinned)} paper(s) by keyword match")
        return pinned, remaining

    def _enrich_papers(self, papers: list[Paper]) -> None:
        """Fetch full text lazily and generate TL;DR + affiliations in parallel.

        Why this is the central speed fix:
        - Full text is only needed for TL;DR/affiliation generation of the papers
          that actually appear in the email, and is irrelevant to ranking. So we
          fetch it only here, for the already-reranked top-N, instead of for the
          hundreds of papers retrieved each day.
        - TL;DR + affiliation generation is I/O-bound (LLM API calls). Running it
          serially over ~100 papers dominated wall-clock time; a thread pool turns
          it into a few minutes.
        """
        if len(papers) == 0:
            return
        workers_cfg = getattr(self.config.executor, "enrich_workers", None) or 8
        workers = max(1, min(int(workers_cfg), len(papers)))
        logger.info(
            f"Fetching full text + generating TL;DR/affiliations for "
            f"{len(papers)} papers ({workers} workers)..."
        )

        def _enrich_one(p: Paper) -> None:
            # Lazy full-text: only download if not already present (e.g. a source
            # that ships full text, or a stub in tests).
            if p.full_text is None:
                retriever = self.retrievers.get(p.source)
                if retriever is not None:
                    try:
                        p.full_text = retriever.fetch_full_text(p)
                    except Exception as e:
                        logger.warning(f"Failed to fetch full text for {p.url}: {e}")
            p.generate_tldr(self.openai_client, self.config.llm)
            p.generate_affiliations(self.openai_client, self.config.llm)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(tqdm(ex.map(_enrich_one, papers), total=len(papers), desc="Enriching papers"))
