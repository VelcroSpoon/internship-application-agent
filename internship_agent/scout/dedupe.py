"""Identity and change detection for postings.

Two hashes with two jobs:

- ``dedupe_hash`` answers "is this the same posting?" It is built from
  company, title and location after aggressive normalisation, so a board
  reformatting its titles (trailing spaces, punctuation, case) does not
  spawn duplicate rows. The tradeoff is accepted knowingly: two genuinely
  distinct reqs with identical company/title/location collapse into one.
  The scout logs that case as an event rather than hiding it.

- ``content_hash`` answers "did the description change?" It only collapses
  whitespace, because the point is to notice real edits to requirements.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_NON_ALNUM = re.compile(r"[^0-9a-z\s]+")
_WS = re.compile(r"\s+")
_SEP = "\x1f"  # ASCII unit separator: cannot appear in normalised text


def normalize(text: str | None) -> str:
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    stripped = _NON_ALNUM.sub(" ", folded)
    return _WS.sub(" ", stripped).strip()


def dedupe_hash(company: str | None, title: str | None, location: str | None) -> str:
    key = _SEP.join((normalize(company), normalize(title), normalize(location)))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def content_hash(description: str | None) -> str:
    collapsed = _WS.sub(" ", description or "").strip()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()
