from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from omegaconf import DictConfig

from . import llm as llm_transport
from .llm import (
    ApiMode,
    InvalidLlmMappingError,
    LlmValue,
    SdkArgument,
    ThinkingCannotBeDisabledError,
    UnsupportedApiModeError,
    UnsupportedThinkingError,
    _request_llm,
)

__all__ = (
    "ApiMode",
    "CorpusPaper",
    "InvalidLlmMappingError",
    "LlmValue",
    "Paper",
    "RawPaperItem",
    "SdkArgument",
    "ThinkingCannotBeDisabledError",
    "UnsupportedApiModeError",
    "UnsupportedThinkingError",
    "_request_llm",
)

RawPaperItem = TypeVar("RawPaperItem")


@dataclass
class Paper:
    """Mutable paper record enriched throughout the recommendation pipeline."""

    source: str
    title: str
    authors: list[str]
    abstract: str
    url: str
    pdf_url: str | None = None
    full_text: str | None = None
    tldr: str | None = None
    affiliations: list[str] | None = None
    score: float | None = None
    pinned: bool = False  # True when force-included by keyword pinning
    doi: str | None = None
    publisher: str | None = None
    issns: tuple[str, ...] = ()
    is_preprint: bool | None = None
    venue_citation_proxy: float | None = None
    journal: str | None = None

    def generate_tldr_and_affiliations(
        self,
        openai_client: llm_transport.LlmClient,
        llm_params: Mapping[str, LlmValue] | DictConfig,
    ) -> tuple[str, list[str] | None]:
        from .paper_generation import generate_tldr_and_affiliations

        return generate_tldr_and_affiliations(self, openai_client, llm_params)

    def generate_tldr(
        self,
        openai_client: llm_transport.LlmClient,
        llm_params: Mapping[str, LlmValue] | DictConfig,
    ) -> str:
        """Compatibility wrapper for Executor's pending combined-flow migration."""
        if self.tldr is None:
            tldr, _ = self.generate_tldr_and_affiliations(openai_client, llm_params)
            return tldr
        return self.tldr

    def generate_affiliations(
        self,
        openai_client: llm_transport.LlmClient,
        llm_params: Mapping[str, LlmValue] | DictConfig,
    ) -> list[str] | None:
        """Compatibility wrapper sharing the same combined generation result."""
        if self.tldr is None:
            _, affiliations = self.generate_tldr_and_affiliations(
                openai_client, llm_params
            )
            return affiliations
        return self.affiliations


@dataclass
class CorpusPaper:
    title: str
    abstract: str
    added_date: datetime
    paths: list[str]
