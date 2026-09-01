from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable, Literal, Optional, TypeVar, assert_never
from datetime import datetime
import re
import tiktoken
from openai import OpenAI
from openai.types.chat import ChatCompletion
from openai.types.responses import Response
from loguru import logger
from omegaconf import DictConfig, OmegaConf
import json
RawPaperItem = TypeVar('RawPaperItem')

type LlmValue = (
    str | int | float | bool | None | list["LlmValue"] | dict[str, "LlmValue"]
)
type ApiMode = Literal["chat_completion", "response"]
type SdkArgument = LlmValue | list[dict[str, str]]


class InvalidLlmMappingError(TypeError):
    def __init__(self, path: str) -> None:
        self.path: str = path
        super().__init__(f"{path} must be a mapping")


class UnsupportedThinkingError(ValueError):
    def __init__(self) -> None:
        super().__init__("Unsupported llm.thinking; expected null or 'disabled'.")


class ThinkingCannotBeDisabledError(ValueError):
    def __init__(self, model: str) -> None:
        self.model: str = model
        super().__init__(f"{model} cannot disable thinking; set llm.thinking to null.")


class UnsupportedApiModeError(ValueError):
    def __init__(self, api_mode: LlmValue) -> None:
        self.api_mode: LlmValue = api_mode
        super().__init__(
            f"Unsupported llm.api_mode: {api_mode}. Expected "
            "'chat_completion' or 'response'."
        )


class InvalidAffiliationsResponseError(ValueError):
    def __init__(self) -> None:
        super().__init__("LLM response does not contain an affiliations list")


def _call_sdk[ResponseT](
    create: Callable[..., ResponseT], **kwargs: SdkArgument
) -> ResponseT:
    return create(**kwargs)


_MINIMAX_M2_MODELS: frozenset[str] = frozenset(
    {
        "MiniMax-M2",
        "MiniMax-M2.1",
        "MiniMax-M2.1-highspeed",
        "MiniMax-M2.5",
        "MiniMax-M2.5-highspeed",
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
    }
)


def _request_llm(
    openai_client: OpenAI,
    llm_params: Mapping[str, LlmValue] | DictConfig,
    messages: list[dict[str, str]],
) -> str:
    api_mode_value = llm_params.get("api_mode", "chat_completion")
    if api_mode_value not in ("chat_completion", "response"):
        raise UnsupportedApiModeError(api_mode_value)
    api_mode: ApiMode = api_mode_value
    generation_source = llm_params.get("generation_kwargs", {})
    if not isinstance(generation_source, (dict, DictConfig)):
        raise InvalidLlmMappingError("llm.generation_kwargs")
    generation_config = OmegaConf.create(generation_source)
    resolved_kwargs = OmegaConf.to_container(generation_config, resolve=True)
    if not isinstance(resolved_kwargs, dict):
        raise InvalidLlmMappingError("llm.generation_kwargs")
    generation_kwargs: dict[str, LlmValue] = {}
    for key, value in resolved_kwargs.items():
        if not isinstance(key, str):
            raise InvalidLlmMappingError("llm.generation_kwargs")
        generation_kwargs[key] = value
    thinking = llm_params.get("thinking")
    if thinking is not None and thinking != "disabled":
        raise UnsupportedThinkingError()

    model_value = generation_kwargs.get("model")
    model = model_value if isinstance(model_value, str) else None
    if thinking == "disabled" and model in _MINIMAX_M2_MODELS:
        raise ThinkingCannotBeDisabledError(model)

    match api_mode:
        case "chat_completion":
            if thinking == "disabled" and model == "MiniMax-M3":
                extra_body = generation_kwargs.get("extra_body", {})
                if not isinstance(extra_body, dict):
                    raise InvalidLlmMappingError("llm.generation_kwargs.extra_body")
                thinking_body = extra_body.get("thinking", {})
                if not isinstance(thinking_body, dict):
                    raise InvalidLlmMappingError(
                        "llm.generation_kwargs.extra_body.thinking"
                    )
                generation_kwargs["extra_body"] = {
                    **extra_body,
                    "thinking": {**thinking_body, "type": "disabled"},
                }
            chat_response: ChatCompletion = _call_sdk(
                openai_client.chat.completions.create,
                messages=messages,
                **generation_kwargs,
            )
            return chat_response.choices[0].message.content or ""

        case "response":
            if thinking == "disabled" and model == "MiniMax-M3":
                reasoning = generation_kwargs.get("reasoning", {})
                if not isinstance(reasoning, dict):
                    raise InvalidLlmMappingError("llm.generation_kwargs.reasoning")
                generation_kwargs["reasoning"] = {
                    **reasoning,
                    "effort": "none",
                }
            max_tokens = generation_kwargs.pop("max_tokens", None)
            if max_tokens is not None and "max_output_tokens" not in generation_kwargs:
                generation_kwargs["max_output_tokens"] = max_tokens
            api_response: Response = _call_sdk(
                openai_client.responses.create,
                input=messages,
                **generation_kwargs,
            )
            return api_response.output_text

        case unreachable:
            assert_never(unreachable)


@dataclass
class Paper:
    source: str
    title: str
    authors: list[str]
    abstract: str
    url: str
    pdf_url: Optional[str] = None
    full_text: Optional[str] = None
    tldr: Optional[str] = None
    affiliations: Optional[list[str]] = None
    score: Optional[float] = None
    pinned: bool = False  # True when force-included by keyword pinning

    def _generate_tldr_with_llm(
        self,
        openai_client: OpenAI,
        llm_params: Mapping[str, LlmValue] | DictConfig,
    ) -> str:
        lang = llm_params.get('language', 'English')
        prompt = f"Given the following information of a paper, generate a one-sentence TLDR summary in {lang}:\n\n"
        if self.title:
            prompt += f"Title:\n {self.title}\n\n"

        if self.abstract:
            prompt += f"Abstract: {self.abstract}\n\n"

        if self.full_text:
            prompt += f"Preview of main content:\n {self.full_text}\n\n"

        if not self.full_text and not self.abstract:
            logger.warning(f"Neither full text nor abstract is provided for {self.url}")
            return "Failed to generate TLDR. Neither full text nor abstract is provided"
        
        # use gpt-4o tokenizer for estimation
        enc = tiktoken.encoding_for_model("gpt-4o")
        prompt_tokens = enc.encode(prompt)
        prompt_tokens = prompt_tokens[:4000]  # truncate to 4000 tokens
        prompt = enc.decode(prompt_tokens)
        
        tldr = _request_llm(
            openai_client,
            llm_params,
            [
                {
                    "role": "system",
                    "content": f"You are an assistant who perfectly summarizes scientific paper, and gives the core idea of the paper to the user. Your answer should be in {lang}.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        return tldr
    
    def generate_tldr(
        self,
        openai_client: OpenAI,
        llm_params: Mapping[str, LlmValue] | DictConfig,
    ) -> str:
        try:
            tldr = self._generate_tldr_with_llm(openai_client,llm_params)
            self.tldr = tldr
            return tldr
        except Exception as e:
            logger.warning(f"Failed to generate tldr of {self.url}: {e}")
            tldr = self.abstract
            self.tldr = tldr
            return tldr

    def _generate_affiliations_with_llm(
        self,
        openai_client: OpenAI,
        llm_params: Mapping[str, LlmValue] | DictConfig,
    ) -> list[str] | None:
        if self.full_text is not None:
            prompt = f"Given the beginning of a paper, extract the affiliations of the authors in a python list format, which is sorted by the author order. If there is no affiliation found, return an empty list '[]':\n\n{self.full_text}"
            # use gpt-4o tokenizer for estimation
            enc = tiktoken.encoding_for_model("gpt-4o")
            prompt_tokens = enc.encode(prompt)
            prompt_tokens = prompt_tokens[:2000]  # truncate to 2000 tokens
            prompt = enc.decode(prompt_tokens)
            affiliations = _request_llm(
                openai_client,
                llm_params,
                [
                    {
                        "role": "system",
                        "content": "You are an assistant who perfectly extracts affiliations of authors from a paper. You should return a python list of affiliations sorted by the author order, like [\"TsingHua University\",\"Peking University\"]. If an affiliation is consisted of multi-level affiliations, like 'Department of Computer Science, TsingHua University', you should return the top-level affiliation 'TsingHua University' only. Do not contain duplicated affiliations. If there is no affiliation found, you should return an empty list [ ]. You should only return the final list of affiliations, and do not return any intermediate results.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            affiliations_match = re.search(
                r'\[.*?\]', affiliations, flags=re.DOTALL
            )
            if affiliations_match is None:
                raise InvalidAffiliationsResponseError()
            affiliations_json = json.loads(affiliations_match.group(0))
            if not isinstance(affiliations_json, list):
                raise InvalidAffiliationsResponseError()
            affiliations = list({str(a) for a in affiliations_json})

            return affiliations
    
    def generate_affiliations(
        self,
        openai_client: OpenAI,
        llm_params: Mapping[str, LlmValue] | DictConfig,
    ) -> list[str] | None:
        try:
            affiliations = self._generate_affiliations_with_llm(openai_client,llm_params)
            self.affiliations = affiliations
            return affiliations
        except Exception as e:
            logger.warning(f"Failed to generate affiliations of {self.url}: {e}")
            self.affiliations = None
            return None
@dataclass
class CorpusPaper:
    title: str
    abstract: str
    added_date: datetime
    paths: list[str]
