"""Bounded concurrent enrichment for papers selected for final output."""

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Final, Protocol

from loguru import logger
from omegaconf import DictConfig

from . import llm as llm_transport
from .bilingual_generation import generate_bilingual_digest
from .figures import fetch_first_figure
from .llm import LlmValue
from .paper_generation import GENERATION_FAILED_TLDR, NO_CONTENT_TLDR
from .paper_presentation import PaperPresentation
from .protocol import Paper

_FIGURE_BUDGET_BYTES: Final = 8 * 1024 * 1024


class FullTextRetriever(Protocol):
    def fetch_full_text(self, paper: Paper) -> str | None: ...


def enrich_final_papers(
    papers: list[Paper],
    retrievers: Mapping[str, FullTextRetriever],
    client: llm_transport.LlmClient,
    llm_params: Mapping[str, LlmValue] | DictConfig,
    workers: int,
    *,
    bilingual: bool = False,
    figures: bool = False,
) -> list[Paper]:
    """Enrich final papers in place; preserve the legacy positional API.

    The keyword switches are independent for backwards-compatible direct callers.
    Adapter failures are isolated here as an optional-content boundary.
    """
    if not papers:
        return []

    worker_count = max(1, min(workers, 4, len(papers)))

    def enrich_one(paper: Paper) -> Paper:
        if paper.full_text is None or not paper.full_text.strip():
            retriever = retrievers.get(paper.source)
            if retriever is not None:
                try:
                    paper.full_text = retriever.fetch_full_text(paper)
                except Exception as error:
                    logger.warning(
                        "final_enrichment full_text_failure source={} exception_type={}",
                        paper.source,
                        type(error).__name__,
                    )
        presentation = PaperPresentation()
        if bilingual:
            try:
                presentation = generate_bilingual_digest(paper, client, llm_params)
            except Exception as error:  # noqa: BROAD_EXCEPT_OK - Optional adapter boundary must fail soft.
                logger.warning(
                    "final_enrichment bilingual_failure exception_type={}",
                    type(error).__name__,
                )
                if not paper.tldr:
                    paper.tldr = paper.abstract if paper.abstract.strip() else (
                        GENERATION_FAILED_TLDR if (paper.full_text or "").strip() else NO_CONTENT_TLDR
                    )
            paper.presentation = presentation
        else:
            _ = paper.generate_tldr_and_affiliations(client, llm_params)
            if figures:
                presentation = PaperPresentation(bilingual_enabled=False)
                paper.presentation = presentation
        if figures:
            try:
                figure = fetch_first_figure(paper)
            except Exception as error:  # noqa: BROAD_EXCEPT_OK - Optional adapter boundary; redact payloads.
                logger.warning(
                    "final_enrichment figure_failure exception_type={}",
                    type(error).__name__,
                )
            else:
                paper.presentation = replace(
                    presentation, first_figure=figure,
                )
        return paper

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        enriched = list(executor.map(enrich_one, papers))

    if not figures:
        return enriched

    # Collection preserves pinned + top-N order; completion order cannot spend budget.
    remaining = _FIGURE_BUDGET_BYTES
    for paper in enriched:
        presentation = paper.presentation
        if presentation is not None and presentation.first_figure is not None:
            size = len(presentation.first_figure.data)
            if size <= remaining:
                remaining -= size
            else:
                paper.presentation = replace(presentation, first_figure=None)
    return enriched
