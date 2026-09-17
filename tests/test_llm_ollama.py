"""Ollama backend, tested through httpx.MockTransport. Asserts what is sent
(schema-constrained format, temperature 0, no streaming) and how replies
and failures are surfaced."""

import json

import httpx
import pytest
from pydantic import BaseModel, Field

from internship_agent.llm.base import LLMOutputError, LLMTransportError
from internship_agent.llm.ollama import OllamaBackend


class Toy(BaseModel):
    answer: int = Field(ge=0, le=10)
    note: str


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _reply(content: str, **extra) -> httpx.Response:
    body = {
        "model": "qwen2.5:3b",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "prompt_eval_count": 120,
        "eval_count": 15,
        "total_duration": 2_000_000_000,
        **extra,
    }
    return httpx.Response(200, json=body)


def test_complete_sends_schema_constrained_chat_request():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _reply(json.dumps({"answer": 4, "note": "ok"}))

    backend = OllamaBackend(
        model="qwen2.5:3b", host="http://ollama.test:11434", client=_client(handler)
    )
    result = backend.complete(system="sys", user="usr", schema=Toy)

    (req,) = seen
    body = json.loads(req.content)
    assert req.url == "http://ollama.test:11434/api/chat"
    assert body["model"] == "qwen2.5:3b"
    assert body["stream"] is False
    assert body["format"] == Toy.model_json_schema()
    assert body["options"]["temperature"] == 0
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == "sys" and body["messages"][1]["content"] == "usr"
    assert result.parsed == Toy(answer=4, note="ok")
    assert result.model == "qwen2.5:3b"
    assert result.usage["prompt_tokens"] == 120 and result.usage["completion_tokens"] == 15


def test_invalid_json_raises_output_error_with_raw_text():
    backend = OllamaBackend(
        model="m", host="http://x", client=_client(lambda r: _reply("not json at all"))
    )
    with pytest.raises(LLMOutputError) as exc:
        backend.complete(system="s", user="u", schema=Toy)
    assert exc.value.raw_text == "not json at all"


def test_schema_violation_raises_output_error():
    backend = OllamaBackend(
        model="m", host="http://x", client=_client(lambda r: _reply('{"answer": 99, "note": "x"}'))
    )
    with pytest.raises(LLMOutputError) as exc:
        backend.complete(system="s", user="u", schema=Toy)
    assert "answer" in str(exc.value)


def test_http_failure_raises_transport_error():
    backend = OllamaBackend(
        model="m", host="http://x", client=_client(lambda r: httpx.Response(500))
    )
    with pytest.raises(LLMTransportError):
        backend.complete(system="s", user="u", schema=Toy)


def test_connection_refused_raises_transport_error():
    def handler(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    backend = OllamaBackend(model="m", host="http://x", client=_client(handler))
    with pytest.raises(LLMTransportError):
        backend.complete(system="s", user="u", schema=Toy)
