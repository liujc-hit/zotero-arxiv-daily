"""Bounded concurrent enrichment for papers selected for final output."""

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from loguru import logger
from omegaconf import DictConfig

from . import llm as llm_transport
from .llm import LlmValue
from .protocol import Paper


class FullTextRetriever(Protocol):
    def fetch_full_text(self, paper: Paper) -> str | None: ...


def enrich_final_papers(
    papers: list[Paper],
    retrievers: Mapping[str, FullTextRetriever],
    client: llm_transport.LlmClient,
    llm_params: Mapping[str, LlmValue] | DictConfig,
    workers: int,
) -> list[Paper]:
    """Fetch missing text and generate one digest per paper in input order."""
    if not papers:
        return []

    worker_count = max(1, min(workers, len(papers)))

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
        _ = paper.generate_tldr_and_affiliations(client, llm_params)
        return paper

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(enrich_one, papers))
