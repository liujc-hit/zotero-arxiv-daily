"""Tests for zotero_arxiv_daily.protocol: Paper.generate_tldr, Paper.generate_affiliations."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from omegaconf import OmegaConf

from tests.canned_responses import make_sample_paper, make_stub_openai_client
from zotero_arxiv_daily.protocol import _request_llm


MINIMAX_M2_MODELS = (
    "MiniMax-M2", "MiniMax-M2.1", "MiniMax-M2.1-highspeed",
    "MiniMax-M2.5", "MiniMax-M2.5-highspeed",
    "MiniMax-M2.7", "MiniMax-M2.7-highspeed",
)
REQUEST_MESSAGES = [{"role": "user", "content": "Summarize"}]


@pytest.fixture()
def llm_params():
    return {
        "api_mode": "chat_completion",
        "language": "English",
        "generation_kwargs": {"model": "gpt-4o-mini", "max_tokens": 16384},
    }


@pytest.fixture()
def recorder_client():
    recorded_requests = []

    def create_chat_completion(**kwargs):
        recorded_requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Recorded"))])

    def create_response(**kwargs):
        recorded_requests.append(kwargs)
        return SimpleNamespace(output_text="Recorded")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_chat_completion)),
        responses=SimpleNamespace(create=create_response),
    )
    return client, recorded_requests


# ---------------------------------------------------------------------------
# generate_tldr
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_tldr_returns_response(llm_params, api_mode):
    llm_params["api_mode"] = api_mode
    client = make_stub_openai_client()
    paper = make_sample_paper()
    result = paper.generate_tldr(client, llm_params)
    assert result == "Hello! How can I assist you today?"
    assert paper.tldr == result


def test_tldr_without_abstract_or_fulltext(llm_params):
    client = make_stub_openai_client()
    paper = make_sample_paper(abstract="", full_text=None)
    result = paper.generate_tldr(client, llm_params)
    assert "Failed to generate TLDR" in result


def test_tldr_falls_back_to_abstract_on_error(llm_params):
    paper = make_sample_paper()

    # Client whose create() raises
    from types import SimpleNamespace

    broken_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("API down")))
        )
    )
    result = paper.generate_tldr(broken_client, llm_params)
    assert result == paper.abstract


def test_tldr_truncates_long_prompt(llm_params):
    client = make_stub_openai_client()
    paper = make_sample_paper(full_text="word " * 10000)
    result = paper.generate_tldr(client, llm_params)
    assert result is not None


def test_response_mode_maps_max_tokens(llm_params):
    from types import SimpleNamespace

    received_kwargs = {}

    def create_response(**kwargs):
        received_kwargs.update(kwargs)
        return SimpleNamespace(output_text="Summary")

    client = SimpleNamespace(
        responses=SimpleNamespace(create=create_response),
    )
    llm_params["api_mode"] = "response"
    paper = make_sample_paper()

    assert paper.generate_tldr(client, llm_params) == "Summary"
    assert received_kwargs["max_output_tokens"] == 16384
    assert "max_tokens" not in received_kwargs


def test_invalid_api_mode_falls_back_to_abstract(llm_params):
    llm_params["api_mode"] = "invalid"
    paper = make_sample_paper()

    assert paper.generate_tldr(make_stub_openai_client(), llm_params) == paper.abstract


# ---------------------------------------------------------------------------
# generate_affiliations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_affiliations_returns_parsed_list(llm_params, api_mode):
    llm_params["api_mode"] = api_mode
    client = make_stub_openai_client()
    paper = make_sample_paper()
    result = paper.generate_affiliations(client, llm_params)
    assert isinstance(result, list)
    assert "TsingHua University" in result
    assert "Peking University" in result


def test_affiliations_none_without_fulltext(llm_params):
    client = make_stub_openai_client()
    paper = make_sample_paper(full_text=None)
    result = paper.generate_affiliations(client, llm_params)
    assert result is None


def test_affiliations_deduplicates(llm_params):
    """The stub returns two distinct affiliations, so no dedup needed.
    But confirm the set() dedup in the code doesn't break anything.
    """
    client = make_stub_openai_client()
    paper = make_sample_paper()
    result = paper.generate_affiliations(client, llm_params)
    assert len(result) == len(set(result))


def test_affiliations_malformed_llm_output(llm_params):
    """LLM returns affiliations without JSON brackets. Should fall back gracefully."""
    from types import SimpleNamespace

    def create_no_brackets(**kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="TsingHua University, Peking University"),
                )
            ]
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create_no_brackets)
        )
    )
    paper = make_sample_paper()
    result = paper.generate_affiliations(client, llm_params)
    # re.search for [...] will fail -> AttributeError -> caught -> returns None
    assert result is None


def test_affiliations_error_returns_none(llm_params):
    from types import SimpleNamespace

    broken_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        )
    )
    paper = make_sample_paper()
    result = paper.generate_affiliations(broken_client, llm_params)
    assert result is None
    assert paper.affiliations is None


# ---------------------------------------------------------------------------
# _request_llm configuration conversion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_request_llm_recursively_converts_omegaconf_values(
    recorder_client, api_mode
):
    # Given
    client, recorded_requests = recorder_client
    llm_params = OmegaConf.create(
        {
            "api_mode": api_mode,
            "generation_kwargs": {
                "model": "gpt-4o-mini",
                "extra_body": {"routing": {"fallbacks": [{"tags": ["fast", "daily"]}]}},
            },
        }
    )

    # When
    _request_llm(client, llm_params, REQUEST_MESSAGES)

    # Then
    extra_body = recorded_requests[0]["extra_body"]
    assert type(extra_body) is dict
    assert type(extra_body["routing"]) is dict
    fallbacks = extra_body["routing"]["fallbacks"]
    assert type(fallbacks) is list
    assert type(fallbacks[0]) is dict
    assert type(fallbacks[0]["tags"]) is list


@pytest.mark.parametrize(
    ("api_mode", "token_field"),
    [("chat_completion", "max_tokens"), ("response", "max_output_tokens")],
)
def test_request_llm_preserves_plain_dict_input_and_token_compatibility(
    recorder_client, api_mode, token_field
):
    # Given
    client, recorded_requests = recorder_client
    generation_kwargs = {
        "model": "gpt-4o-mini",
        "max_tokens": 4096,
        "extra_body": {"routing": {"fallbacks": ["gpt-4.1-mini"]}},
    }
    llm_params = {"api_mode": api_mode, "generation_kwargs": generation_kwargs}

    # When
    result = _request_llm(client, llm_params, REQUEST_MESSAGES)

    # Then
    assert result == "Recorded"
    request = recorded_requests[0]
    assert request[token_field] == 4096
    assert {"max_tokens", "max_output_tokens"} & request.keys() == {token_field}
    assert type(request["extra_body"]) is dict
    assert type(request["extra_body"]["routing"]["fallbacks"]) is list
    assert generation_kwargs == {
        "model": "gpt-4o-mini",
        "max_tokens": 4096,
        "extra_body": {"routing": {"fallbacks": ["gpt-4.1-mini"]}},
    }


# ---------------------------------------------------------------------------
# _request_llm MiniMax thinking behavior
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            ("chat_completion", {"model": "MiniMax-M3", "extra_body": {"route": "global"}},
             "extra_body", {"route": "global", "thinking": {"type": "disabled"}}),
            id="chat_completion",
        ),
        pytest.param(
            ("response", {"model": "MiniMax-M3", "reasoning": {"summary": "detailed"}},
             "reasoning", {"summary": "detailed", "effort": "none"}),
            id="response",
        ),
    ],
)
def test_minimax_m3_disabled_thinking_maps_and_merges_while_preserving_input(
    recorder_client, case
):
    # Given
    client, recorded_requests = recorder_client
    api_mode, generation_kwargs, provider_field, expected_value = case
    llm_params = {"api_mode": api_mode, "thinking": "disabled", "generation_kwargs": generation_kwargs}
    original_llm_params = deepcopy(llm_params)

    # When
    _request_llm(client, llm_params, REQUEST_MESSAGES)

    # Then
    provider_fields = {
        field: recorded_requests[0][field]
        for field in ("extra_body", "reasoning")
        if field in recorded_requests[0]
    }
    assert provider_fields == {provider_field: expected_value}
    assert llm_params == original_llm_params


@pytest.mark.parametrize("model", ("MiniMax-M3", *MINIMAX_M2_MODELS, "gpt-4o-mini"))
@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_null_thinking_preserves_provider_default_payload(
    recorder_client, model, api_mode
):
    # Given
    client, recorded_requests = recorder_client
    llm_params = {"api_mode": api_mode, "thinking": None, "generation_kwargs": {"model": model}}

    # When
    _request_llm(client, llm_params, REQUEST_MESSAGES)

    # Then
    transport_field = "messages" if api_mode == "chat_completion" else "input"
    assert recorded_requests == [{transport_field: REQUEST_MESSAGES, "model": model}]


@pytest.mark.parametrize(
    "model_and_thinking",
    [pytest.param((model, "disabled"), id=model) for model in MINIMAX_M2_MODELS]
    + [pytest.param(("MiniMax-M3", "sometimes"), id="invalid-value")],
)
@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_unsupported_minimax_thinking_is_rejected_before_sdk_call(
    recorder_client, model_and_thinking, api_mode
):
    # Given
    client, recorded_requests = recorder_client
    model, thinking = model_and_thinking
    llm_params = {"api_mode": api_mode, "thinking": thinking, "generation_kwargs": {"model": model}}

    # When
    with pytest.raises(ValueError):
        _request_llm(client, llm_params, REQUEST_MESSAGES)

    # Then
    assert recorded_requests == []


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_generic_model_preserves_request_with_minimax_disabled_setting(
    recorder_client, api_mode
):
    # Given
    client, recorded_requests = recorder_client
    generation_kwargs = {"model": "gpt-4o-mini", "temperature": 0.2}
    llm_params = {"api_mode": api_mode, "thinking": "disabled", "generation_kwargs": generation_kwargs}

    # When
    _request_llm(client, llm_params, REQUEST_MESSAGES)

    # Then
    assert "extra_body" not in recorded_requests[0]
    assert "reasoning" not in recorded_requests[0]
    assert generation_kwargs == {"model": "gpt-4o-mini", "temperature": 0.2}
