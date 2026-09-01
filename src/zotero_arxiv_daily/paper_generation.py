"""Atomic TLDR and affiliation generation for a final paper."""

from collections.abc import Mapping
import json
from typing import Final, Protocol

from loguru import logger
from omegaconf import DictConfig
import tiktoken

from . import llm as llm_transport
from .llm import LlmValue, _request_llm

NO_CONTENT_TLDR: Final = (
    "Failed to generate TLDR. Neither full text nor abstract is provided"
)
_MAX_PROMPT_TOKENS: Final = 4000
_DIGEST_KEYS: Final = frozenset({"tldr", "affiliations"})


class InvalidPaperDigestError(ValueError):
    """The SDK response violates the strict paper digest contract."""


class DigestPaper(Protocol):
    source: str
    title: str
    abstract: str
    full_text: str | None
    tldr: str | None
    affiliations: list[str] | None


def _build_messages(
    paper: DigestPaper, llm_params: Mapping[str, LlmValue] | DictConfig
) -> list[dict[str, str]]:
    language = llm_params.get("language", "English")
    abstract = paper.abstract.strip()
    prompt_sections = [
        (
            f"Generate a one-sentence TLDR in {language}. Also return the authors' "
            "top-level institutions in author order, using an empty list when no "
            "institution can be identified."
        ),
        f"Title:\n{paper.title.strip()}",
        f"Abstract:\n{abstract}",
    ]
    if paper.full_text is not None and paper.full_text.strip():
        prompt_sections.append(f"Beginning/full text:\n{paper.full_text.strip()}")

    complete_prompt = "\n\n".join(prompt_sections)
    encoding = tiktoken.encoding_for_model("gpt-4o")
    user_prompt = encoding.decode(
        encoding.encode(complete_prompt)[:_MAX_PROMPT_TOKENS]
    )
    return [
        {
            "role": "system",
            "content": "Summarize scientific papers and identify author institutions.",
        },
        {"role": "user", "content": user_prompt},
    ]


def _parse_paper_digest(response: str) -> tuple[str, list[str]]:
    parsed = json.loads(response)
    if type(parsed) is not dict or set(parsed) != _DIGEST_KEYS:
        raise InvalidPaperDigestError

    tldr = parsed["tldr"]
    affiliations = parsed["affiliations"]
    if type(tldr) is not str or not tldr.strip():
        raise InvalidPaperDigestError
    if type(affiliations) is not list:
        raise InvalidPaperDigestError

    normalized_affiliations: list[str] = []
    seen: set[str] = set()
    for affiliation in affiliations:
        if type(affiliation) is not str:
            raise InvalidPaperDigestError
        normalized = affiliation.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            normalized_affiliations.append(normalized)
    return tldr.strip(), normalized_affiliations


def generate_tldr_and_affiliations(
    paper: DigestPaper,
    openai_client: llm_transport.LlmClient,
    llm_params: Mapping[str, LlmValue] | DictConfig,
) -> tuple[str, list[str] | None]:
    """Generate both digest fields with at most one SDK create call."""
    abstract = paper.abstract or ""
    full_text = paper.full_text or ""
    if not abstract.strip() and not full_text.strip():
        paper.tldr = NO_CONTENT_TLDR
        paper.affiliations = None
        return NO_CONTENT_TLDR, None

    try:
        messages = _build_messages(paper, llm_params)
        response = _request_llm(
            openai_client,
            llm_params,
            messages,
            structured_output="paper_digest",
        )
        tldr, affiliations = _parse_paper_digest(response)
    except Exception as error:
        logger.warning(
            "paper_digest fallback category=request_or_schema source={} exception_type={}",
            paper.source,
            type(error).__name__,
        )
        paper.tldr = paper.abstract
        paper.affiliations = None
        return paper.abstract, None

    paper.tldr = tldr
    paper.affiliations = affiliations
    return tldr, affiliations
