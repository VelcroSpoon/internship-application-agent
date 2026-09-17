"""The one LLM interface every agent talks through.

A backend takes a system prompt, a user prompt, and a Pydantic class, and
returns a validated instance plus usage. Agents never see raw strings, and
swapping a local model for a hosted one is a config change.

Two failure types, because callers treat them differently:
- LLMTransportError: the backend could not be reached or returned an HTTP
  error. Retrying the same prompt is pointless right now; skip and move on.
- LLMOutputError: the model answered, but the answer did not validate.
  Worth one retry with the validation error fed back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)  # TypeVar rather than PEP 695: requires-python is 3.11


class LLMError(RuntimeError):
    pass


class LLMTransportError(LLMError):
    pass


class LLMOutputError(LLMError):
    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


@dataclass
class LLMResponse(Generic[T]):
    parsed: T
    raw_text: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)


class StructuredLLM(Protocol):
    model: str

    def complete(self, *, system: str, user: str, schema: type[T]) -> LLMResponse[T]: ...
