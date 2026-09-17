"""Test doubles shared across suites. A fake backend replays scripted
responses and records every prompt it was given."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from internship_agent.llm.base import LLMResponse


@dataclass
class Call:
    system: str
    user: str
    schema: type[BaseModel]


class FakeLLM:
    """``script`` items are either a BaseModel instance to return or an
    Exception to raise. Consumed in order; running out raises."""

    model = "fake-model"

    def __init__(self, script: list[BaseModel | Exception]) -> None:
        self.script = list(script)
        self.calls: list[Call] = []

    def complete(self, *, system: str, user: str, schema: type[BaseModel]) -> LLMResponse:
        self.calls.append(Call(system=system, user=user, schema=schema))
        if not self.script:
            raise AssertionError("FakeLLM script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            parsed=item,
            raw_text=item.model_dump_json(),
            model=self.model,
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_duration_ms": 1500.0},
        )
