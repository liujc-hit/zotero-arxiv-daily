"""Tests for the Paper digest delegation and compatible LLM transport export."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from omegaconf import OmegaConf

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.protocol import _request_llm


MINIMAX_M2_MODELS = ("MiniMax-M2", "MiniMax-M2.1", "MiniMax-M2.1-highspeed", "MiniMax-M2.5", "MiniMax-M2.5-highspeed", "MiniMax-M2.7", "MiniMax-M2.7-highspeed")
REQUEST_MESSAGES = [{"role": "user", "content": "Summarize"}]
EXPECTED_PAPER_DIGEST_SCHEMA = {
    "type": "object",
    "properties": {"tldr": {"type": "string"}, "affiliations": {"type": "array", "items": {"type": "string"}}},
    "required": ["tldr", "affiliations"],
    "additionalProperties": False,
}
EXPECTED_PAPER_DIGEST_CONFIG = {"name": "paper_digest", "strict": True, "schema": EXPECTED_PAPER_DIGEST_SCHEMA}


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


def _digest_client(content):
    recorded_requests = []

    def create_chat_completion(**kwargs):
        recorded_requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    def create_response(**kwargs):
        recorded_requests.append(kwargs)
        return SimpleNamespace(output_text=content)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_chat_completion)),
        responses=SimpleNamespace(create=create_response),
    )
    return client, recorded_requests


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_generate_tldr_and_affiliations_uses_one_call_and_sets_both_fields(
    llm_params, api_mode
):
    # Given
    llm_params["api_mode"] = api_mode
    content = json.dumps(
        {
            "tldr": "  A concise digest.  ",
            "affiliations": [" University B ", "University A", "University B", " ", "University A"],
        }
    )
    client, recorded_requests = _digest_client(content)
    paper = make_sample_paper()

    # When
    result = paper.generate_tldr_and_affiliations(client, llm_params)

    # Then
    assert result == ("A concise digest.", ["University B", "University A"])
    assert paper.tldr == "A concise digest."
    assert paper.affiliations == ["University B", "University A"]
    assert len(recorded_requests) == 1


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_legacy_executor_sequence_is_a_single_combined_sdk_call(llm_params, api_mode):
    # Given
    llm_params["api_mode"] = api_mode
    client, recorded_requests = _digest_client(
        json.dumps({"tldr": "Digest.", "affiliations": ["University"]})
    )
    paper = make_sample_paper()

    # When
    tldr = paper.generate_tldr(client, llm_params)
    affiliations = paper.generate_affiliations(client, llm_params)

    # Then
    assert tldr == "Digest."
    assert affiliations == ["University"]
    assert len(recorded_requests) == 1


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


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_request_llm_injects_exact_strict_digest_schema_and_preserves_options(
    recorder_client, api_mode
):
    # Given
    client, recorded_requests = recorder_client
    if api_mode == "chat_completion":
        generation_kwargs = {
            "model": "MiniMax-M3",
            "temperature": 0.2,
            "extra_body": {"routing": {"tags": ["daily"]}},
            "response_format": {"type": "json_object"},
        }
    else:
        generation_kwargs = {
            "model": "MiniMax-M3",
            "max_tokens": 512,
            "metadata": {"job": "daily"},
            "reasoning": {"summary": "detailed"},
            "text": {"verbosity": "low", "format": {"type": "text"}},
        }
    llm_params = {
        "api_mode": api_mode,
        "thinking": "disabled",
        "generation_kwargs": generation_kwargs,
    }
    original_llm_params = deepcopy(llm_params)

    # When
    _request_llm(client, llm_params, REQUEST_MESSAGES, structured_output="paper_digest")

    # Then
    request = recorded_requests[0]
    if api_mode == "chat_completion":
        assert request["response_format"] == {
            "type": "json_schema",
            "json_schema": EXPECTED_PAPER_DIGEST_CONFIG,
        }
        assert request["extra_body"] == {
            "routing": {"tags": ["daily"]},
            "thinking": {"type": "disabled"},
        }
        assert request["temperature"] == 0.2
    else:
        assert request["text"] == {
            "verbosity": "low",
            "format": {"type": "json_schema", **EXPECTED_PAPER_DIGEST_CONFIG},
        }
        assert request["reasoning"] == {"summary": "detailed", "effort": "none"}
        assert request["metadata"] == {"job": "daily"}
        assert request["max_output_tokens"] == 512
    assert llm_params == original_llm_params


# ---------------------------------------------------------------------------
# _request_llm MiniMax thinking behavior
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            ("chat_completion", {"model": "MiniMax-M3", "extra_body": {"route": "global"}}, "extra_body", {"route": "global", "thinking": {"type": "disabled"}}),
            id="chat_completion",
        ),
        pytest.param(
            ("response", {"model": "MiniMax-M3", "reasoning": {"summary": "detailed"}}, "reasoning", {"summary": "detailed", "effort": "none"}),
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
