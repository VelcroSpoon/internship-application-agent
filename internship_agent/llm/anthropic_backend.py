"""Anthropic backend behind the shared StructuredLLM interface.

Uses ``messages.parse`` with the Pydantic class as ``output_format`` so the
schema is enforced server-side and the SDK hands back a validated instance.
The system prompt carries a cache breakpoint: for one application the
Writer and Critic send the same system text and master resume several
times in a row, and for the Screener it is identical across every posting.

Error mapping onto the shared types:
- SDK connection/status errors -> LLMTransportError (do not retry the prompt)
- refusal, truncation (max_tokens), or no parsed output -> LLMOutputError
  (one retry with feedback is reasonable)
"""

from __future__ import annotations

from typing import Any, Literal

import anthropic

from internship_agent.llm.base import LLMOutputError, LLMResponse, LLMTransportError, T

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AnthropicBackend:
    def __init__(
        self,
        model: str,
        *,
        client: Any | None = None,
        effort: Effort = "medium",
        max_tokens: int = 8000,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        # Zero-arg client resolves ANTHROPIC_API_KEY or an `ant auth login` profile.
        self._client = client or anthropic.Anthropic()

    def complete(self, *, system: str, user: str, schema: type[T]) -> LLMResponse[T]:
        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                output_config={"effort": self.effort},
            )
        except anthropic.APIConnectionError as exc:
            raise LLMTransportError(f"anthropic {self.model}: connection error: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMTransportError(f"anthropic {self.model}: {exc.message}") from exc

        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        if response.stop_reason == "refusal":
            detail = (
                getattr(response.stop_details, "explanation", None)
                if response.stop_details
                else None
            )
            raise LLMOutputError(f"anthropic {self.model}: refused ({detail or 'no detail'})", text)
        if response.stop_reason == "max_tokens":
            raise LLMOutputError(f"anthropic {self.model}: output truncated at max_tokens", text)
        parsed = response.parsed_output
        if parsed is None:
            raise LLMOutputError(f"anthropic {self.model}: no parsed output", text)

        u = response.usage
        usage = {
            "prompt_tokens": u.input_tokens,
            "completion_tokens": u.output_tokens,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None),
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", None),
        }
        return LLMResponse(parsed=parsed, raw_text=text, model=response.model, usage=usage)
