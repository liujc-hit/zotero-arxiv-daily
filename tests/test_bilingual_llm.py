"""Offline SDK request contracts for bilingual structured output."""

from copy import deepcopy

import pytest

from tests.llm_fakes import make_recording_client
from zotero_arxiv_daily.llm import ApiMode, LlmValue, _request_llm


@pytest.mark.parametrize("mode", ["chat_completion", "response"])
@pytest.mark.parametrize("model", ["gpt-4o-mini", "MiniMax-M3"])
@pytest.mark.parametrize("thinking", [None, "disabled"])
def test_bilingual_schema_when_requesting_digest(mode: ApiMode, model: str, thinking: str | None):
    # Given
    params: dict[str, LlmValue] = {
        "api_mode": mode, "thinking": thinking,
        "generation_kwargs": {
            "model": model, "max_tokens": 4096,
            "extra_body": {"custom": True, "thinking": {"custom": True}},
            "reasoning": {"summary": "auto"},
        },
    }
    original = deepcopy(params)
    client, calls = make_recording_client(
        "payload", tool_calls=(("paper_digest", "wrong"), ("bilingual_paper_digest", "payload")),
    )
    # When
    result = _request_llm(client, params, [], structured_output="bilingual_paper_digest")
    # Then
    assert result == "payload"
    assert params == original
    assert len(calls) == 1
    request = calls[0]
    if mode == "chat_completion" and model == "MiniMax-M3":
        tools = request["tools"]
        assert isinstance(tools, list)
        tool = tools[0]
        assert isinstance(tool, dict)
        function = tool["function"]
        assert isinstance(function, dict)
        assert function["name"] == "bilingual_paper_digest"
        schema = function["parameters"]
        assert "response_format" not in request
    elif mode == "chat_completion":
        response_format = request["response_format"]
        assert isinstance(response_format, dict)
        config = response_format["json_schema"]
        assert isinstance(config, dict)
        assert config["name"] == "bilingual_paper_digest"
        assert config["strict"] is True
        schema = config["schema"]
    else:
        text = request["text"]
        assert isinstance(text, dict)
        config = text["format"]
        assert isinstance(config, dict)
        assert config["name"] == "bilingual_paper_digest"
        assert config["strict"] is True
        schema = config["schema"]
        assert request["max_output_tokens"] == 4096
        assert "max_tokens" not in request
    assert isinstance(schema, dict)
    assert schema["additionalProperties"] is False
    required = schema["required"]
    assert isinstance(required, list)
    assert set(required) == {
        "title_en", "title_zh", "abstract_en", "abstract_zh", "tldr_en", "tldr_zh", "affiliations",
    }
    if thinking == "disabled" and model == "MiniMax-M3":
        if mode == "response":
            assert request["reasoning"] == {"summary": "auto", "effort": "none"}
        else:
            assert request["extra_body"] == {"custom": True, "thinking": {"custom": True, "type": "disabled"}}


def test_entire_content_when_minimax_returns_wrong_tool():
    # Given
    client, calls = make_recording_client("not a digest", tool_calls=(("paper_digest", "wrong"),))
    params: dict[str, LlmValue] = {"generation_kwargs": {"model": "MiniMax-M3"}}
    # When
    result = _request_llm(client, params, [], structured_output="bilingual_paper_digest")
    # Then
    assert result == "not a digest"
    assert len(calls) == 1
