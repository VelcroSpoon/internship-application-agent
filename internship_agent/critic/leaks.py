"""Guard on the Critic/Writer boundary.

Non-negotiable: the Critic diagnoses, the Writer writes. A ``fix_direction``
is instruction, never copy. If the Writer pastes one into the draft, the
Critic has written prose by proxy and the draft is quietly worse for it.

The schema cannot prevent that, so code checks for it: shared runs of
consecutive words between a finding and the draft it produced. Word runs
rather than exact substrings, because a copy that survives light editing is
still a copy, and rather than fuzzy similarity, because a shared run of
eight words is evidence while a similarity score is an argument.

Detection only. Nothing here edits a draft.
"""

from __future__ import annotations

import re

from internship_agent.agents.critic import Critique

SHINGLE = 8  # consecutive words; short enough to catch a copied clause, long
# enough that shared jargon ("experience with distributed systems") does not fire

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _shingles(words: list[str], n: int = SHINGLE) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def find_critique_leaks(draft_text: str, critique: Critique, n: int = SHINGLE) -> list[str]:
    """Labels for each finding whose fix_direction reappears in the draft."""
    draft_shingles = _shingles(_words(draft_text), n)
    if not draft_shingles:
        return []
    leaks: list[str] = []
    for i, finding in enumerate(critique.findings):
        overlap = _shingles(_words(finding.fix_direction), n) & draft_shingles
        if overlap:
            phrase = " ".join(sorted(overlap)[0])
            leaks.append(f"finding {i} ({finding.dimension.value}): {phrase}")
    return leaks
