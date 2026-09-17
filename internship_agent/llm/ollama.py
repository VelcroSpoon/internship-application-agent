"""Ollama backend over its local HTTP API.

Talks to ``/api/chat`` directly with httpx rather than the ``ollama`` Python
package: two fields of one endpoint do not justify a dependency. The Pydantic
JSON schema is passed as ``format`` so the server constrains decoding to
valid JSON of that shape. Validation still runs client-side, because the
grammar guarantees shape, not bounds (a 0-100 field can still come back 250).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from internship_agent.llm.base import LLMOutputError, LLMResponse, LLMTransportError, T


class OllamaBackend:
    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        *,
        client: httpx.Client | None = None,
        num_ctx: int = 4096,
        # CPU-only inference on an 8 GB machine has hit 120 s per posting under
        # memory pressure; 300 s produced a spurious timeout on a live run.
        timeout_s: float = 900.0,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.num_ctx = num_ctx
        self.timeout_s = timeout_s
        self._client = client or httpx.Client()

    def complete(self, *, system: str, user: str, schema: type[T]) -> LLMResponse[T]:
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = self._client.post(f"{self.host}/api/chat", json=body, timeout=self.timeout_s)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMTransportError(f"ollama {self.model}: {exc}") from exc

        data = resp.json()
        raw = (data.get("message") or {}).get("content", "")
        try:
            parsed = schema.model_validate(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise LLMOutputError(f"ollama {self.model}: not JSON: {exc}", raw) from exc
        except ValidationError as exc:
            raise LLMOutputError(f"ollama {self.model}: schema violation: {exc}", raw) from exc

        usage = {
            "prompt_tokens": data.get("prompt_eval_count"),
            "completion_tokens": data.get("eval_count"),
            "total_duration_ms": _ns_to_ms(data.get("total_duration")),
            "load_duration_ms": _ns_to_ms(data.get("load_duration")),
        }
        return LLMResponse(
            parsed=parsed, raw_text=raw, model=data.get("model", self.model), usage=usage
        )


def _ns_to_ms(ns: int | None) -> float | None:
    return None if ns is None else round(ns / 1_000_000, 1)
