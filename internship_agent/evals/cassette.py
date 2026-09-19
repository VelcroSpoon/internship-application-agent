"""Record model responses once, replay them for free forever.

The eval needs the Writer and Critic to actually run, which costs money and
needs a credential. Recording every response once and replaying it makes the
eval free, repeatable, and runnable in CI. It also makes the numbers stable:
two runs of the same eval over the same cassette produce the same report, so
a change in the report means a change in the code.

Keying
------
A recording is keyed by sha256 over the arm tag, the model, the schema name
and both prompts. That has two consequences worth stating:

- Change a prompt and the key changes, so replay misses loudly instead of
  quietly serving the response to a question you no longer ask. A miss means
  "re-record", not "fall back to something close".
- The arm tag is in the key on purpose. A single-pass control draft uses the
  same prompt as the loop's round 0, so without the tag the two would share
  one recording and the control would be identical to round 0 by
  construction. Tagged separately, the control is an independent sample and
  the comparison measures something real.

Each key holds a list, consumed in order, because one arm can legitimately
ask the same question twice (a retry after a schema failure re-sends a
modified prompt, but a future caller might not). Running out raises rather
than repeating the last answer.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from internship_agent.llm.base import LLMOutputError, LLMResponse, StructuredLLM, T


class CassetteMiss(RuntimeError):
    """The cassette has no recording for this call. Re-record."""


def call_key(*, tag: str, model: str, schema: str, system: str, user: str) -> str:
    digest = hashlib.sha256()
    for part in (tag, model, schema, system, user):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


@dataclass
class Recording:
    key: str
    tag: str
    model: str
    schema: str
    raw_text: str
    usage: dict[str, Any]
    # Kept for a human reading the cassette; never used for matching.
    system_preview: str = ""
    user_preview: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "tag": self.tag,
            "model": self.model,
            "schema": self.schema,
            "raw_text": self.raw_text,
            "usage": self.usage,
            "system_preview": self.system_preview,
            "user_preview": self.user_preview,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Recording:
        return cls(
            key=data["key"],
            tag=data["tag"],
            model=data["model"],
            schema=data["schema"],
            raw_text=data["raw_text"],
            usage=data.get("usage", {}),
            system_preview=data.get("system_preview", ""),
            user_preview=data.get("user_preview", ""),
        )


@dataclass
class Cassette:
    recordings: list[Recording] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Cassette:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([Recording.from_json(r) for r in data["recordings"]])

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"version": 1, "recordings": [r.to_json() for r in self.recordings]},
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def cost_summary(self) -> dict[str, dict[str, int]]:
        """Input and output tokens per model, so the recording's cost is on record."""
        totals: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0})
        for rec in self.recordings:
            entry = totals[rec.model]
            entry["calls"] += 1
            entry["in"] += rec.usage.get("prompt_tokens") or 0
            entry["out"] += rec.usage.get("completion_tokens") or 0
        return dict(totals)


class RecordingBackend:
    """Wraps a real backend, passes every call through, and keeps the answer."""

    def __init__(self, inner: StructuredLLM, cassette: Cassette, tag: str) -> None:
        self._inner = inner
        self._cassette = cassette
        self._tag = tag
        self.model = inner.model

    def complete(self, *, system: str, user: str, schema: type[T]) -> LLMResponse[T]:
        response = self._inner.complete(system=system, user=user, schema=schema)
        self._cassette.recordings.append(
            Recording(
                key=call_key(
                    tag=self._tag,
                    model=self.model,
                    schema=schema.__name__,
                    system=system,
                    user=user,
                ),
                tag=self._tag,
                model=response.model,
                schema=schema.__name__,
                raw_text=response.raw_text or response.parsed.model_dump_json(),
                usage=response.usage,
                system_preview=system[:200],
                user_preview=user[:200],
            )
        )
        return response


class ReplayBackend:
    """Serves recorded answers. Never touches the network."""

    def __init__(self, cassette: Cassette, tag: str, model: str) -> None:
        self._by_key: dict[str, list[Recording]] = defaultdict(list)
        for rec in cassette.recordings:
            self._by_key[rec.key].append(rec)
        self._cursor: dict[str, int] = defaultdict(int)
        self._tag = tag
        self.model = model

    def complete(self, *, system: str, user: str, schema: type[T]) -> LLMResponse[T]:
        key = call_key(
            tag=self._tag, model=self.model, schema=schema.__name__, system=system, user=user
        )
        available = self._by_key.get(key, [])
        index = self._cursor[key]
        if index >= len(available):
            raise CassetteMiss(
                f"no recording for a {schema.__name__} call on {self.model} in arm "
                f"'{self._tag}' (key {key[:12]}…). The prompt or the model changed; "
                f"re-record with: internship_agent evals record"
            )
        self._cursor[key] += 1
        rec = available[index]
        try:
            parsed = schema.model_validate_json(rec.raw_text)
        except ValidationError as exc:  # a cassette edited by hand, or a schema change
            raise LLMOutputError(
                f"recorded output no longer validates: {exc}", rec.raw_text
            ) from exc
        return LLMResponse(
            parsed=parsed, raw_text=rec.raw_text, model=rec.model, usage=dict(rec.usage)
        )


def replay_backend_for(cassette: Cassette, tag: str, model: str) -> ReplayBackend:
    return ReplayBackend(cassette, tag=tag, model=model)


__all__ = [
    "Cassette",
    "CassetteMiss",
    "Recording",
    "RecordingBackend",
    "ReplayBackend",
    "call_key",
    "replay_backend_for",
]
