"""One retry policy for every agent: on a schema failure, ask again with the
validation error appended; on a transport failure, do not retry here (the
caller decides whether to abort the run)."""

from __future__ import annotations

from collections.abc import Callable

from internship_agent.llm.base import LLMOutputError, LLMResponse, StructuredLLM, T


def default_repair_suffix(error: str, raw_text: str) -> str:
    return (
        "\n\n# Your previous answer was rejected\n"
        f"Validation error: {error}\n"
        f"Previous output: {raw_text[:500]}\n"
        "Answer again. Respect every field constraint."
    )


def complete_with_retry(
    backend: StructuredLLM,
    *,
    system: str,
    user: str,
    schema: type[T],
    max_attempts: int,
    repair_suffix: Callable[[str, str], str] = default_repair_suffix,
) -> LLMResponse[T]:
    prompt = user
    last: LLMOutputError | None = None
    for _ in range(max_attempts):
        try:
            return backend.complete(system=system, user=prompt, schema=schema)
        except LLMOutputError as exc:
            last = exc
            prompt = user + repair_suffix(str(exc), exc.raw_text)
    assert last is not None
    raise last
