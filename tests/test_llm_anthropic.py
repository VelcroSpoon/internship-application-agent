"""Anthropic backend through a fake client object. Asserts the request shape
(structured output via output_format, cached system block, effort) and how
stop reasons and SDK exceptions map onto the shared LLM error types."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, Field

from internship_agent.llm.anthropic_backend import AnthropicBackend, MissingCredentials
from internship_agent.llm.base import LLMOutputError, LLMTransportError


class Toy(BaseModel):
    answer: int = Field(ge=0, le=10)
    note: str


def _response(parsed, *, stop_reason="end_turn", text='{"answer": 4, "note": "ok"}'):
    return SimpleNamespace(
        parsed_output=parsed,
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=None,
        model="claude-opus-5",
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=15,
            cache_read_input_tokens=100,
            cache_creation_input_tokens=0,
        ),
    )


class FakeClient:
    def __init__(self, outcome):
        self.calls: list[dict] = []
        self._outcome = outcome
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _req() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def test_complete_sends_structured_output_request_with_cached_system():
    client = FakeClient(_response(Toy(answer=4, note="ok")))
    backend = AnthropicBackend(model="claude-opus-5", client=client, effort="medium")

    result = backend.complete(system="SYS", user="USR", schema=Toy)

    (call,) = client.calls
    assert call["model"] == "claude-opus-5"
    assert call["output_format"] is Toy
    assert call["output_config"] == {"effort": "medium"}
    assert call["system"] == [
        {"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}
    ]
    assert call["messages"] == [{"role": "user", "content": "USR"}]
    assert call["max_tokens"] >= 4000
    assert result.parsed == Toy(answer=4, note="ok")
    assert result.model == "claude-opus-5"
    assert result.usage["prompt_tokens"] == 120 and result.usage["completion_tokens"] == 15
    assert result.usage["cache_read_input_tokens"] == 100


def test_refusal_stop_reason_raises_output_error():
    client = FakeClient(_response(None, stop_reason="refusal", text=""))
    backend = AnthropicBackend(model="m", client=client)

    with pytest.raises(LLMOutputError, match="refus"):
        backend.complete(system="s", user="u", schema=Toy)


def test_truncated_output_raises_output_error_with_partial_text():
    client = FakeClient(_response(None, stop_reason="max_tokens", text='{"answer": 4, "no'))
    backend = AnthropicBackend(model="m", client=client)

    with pytest.raises(LLMOutputError) as exc:
        backend.complete(system="s", user="u", schema=Toy)
    assert exc.value.raw_text == '{"answer": 4, "no'


def test_missing_parsed_output_raises_output_error():
    client = FakeClient(_response(None))
    backend = AnthropicBackend(model="m", client=client)

    with pytest.raises(LLMOutputError):
        backend.complete(system="s", user="u", schema=Toy)


def test_connection_error_maps_to_transport_error():
    client = FakeClient(anthropic.APIConnectionError(request=_req()))
    backend = AnthropicBackend(model="m", client=client)

    with pytest.raises(LLMTransportError):
        backend.complete(system="s", user="u", schema=Toy)


def test_status_error_maps_to_transport_error():
    resp = httpx2.Response(500, request=_req())
    client = FakeClient(anthropic.APIStatusError("boom", response=resp, body=None))
    backend = AnthropicBackend(model="m", client=client)

    with pytest.raises(LLMTransportError, match="boom"):
        backend.complete(system="s", user="u", schema=Toy)


def test_missing_credentials_become_a_clear_transport_error():
    """The SDK raises a bare TypeError at request time when it cannot resolve
    a credential. That surfaced as a traceback with no advice; it should be an
    actionable message instead."""
    client = FakeClient(
        TypeError(
            "Could not resolve authentication method. Expected one of api_key, "
            "auth_token, or credentials to be set."
        )
    )
    backend = AnthropicBackend(model="claude-opus-5", client=client)

    with pytest.raises(MissingCredentials) as exc:
        backend.complete(system="s", user="u", schema=Toy)

    assert isinstance(exc.value, LLMTransportError)  # existing handling still applies
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_an_unrelated_type_error_is_not_swallowed():
    client = FakeClient(TypeError("unexpected keyword argument 'foo'"))
    backend = AnthropicBackend(model="m", client=client)

    with pytest.raises(TypeError, match="unexpected keyword"):
        backend.complete(system="s", user="u", schema=Toy)
