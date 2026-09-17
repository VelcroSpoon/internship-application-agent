"""Writer output contract.

Each bullet carries ``resume_anchor``: the master-resume line the Writer
says supports it. That is the provenance trail the Critic's GROUNDING check
verifies and the review UI shows. A bullet with no anchor cannot exist.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Bullet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=300, description="One tailored resume bullet.")
    resume_anchor: str = Field(
        min_length=1,
        max_length=300,
        description="The master-resume line, quoted closely, that this bullet is built from.",
    )


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bullets: list[Bullet] = Field(
        min_length=3,
        max_length=6,
        description="Tailored resume bullets for this posting, strongest first.",
    )
    cover_letter: str = Field(
        min_length=300,
        max_length=4000,
        description="Plain-text cover letter, roughly 200-350 words, no subject line.",
    )

    def as_text(self) -> str:
        """Stable plain-text form, used for diffs between revisions and for review."""
        lines = ["## Bullets", ""]
        lines += [f"- {b.text}" for b in self.bullets]
        lines += ["", "## Cover letter", "", self.cover_letter.strip(), ""]
        return "\n".join(lines)
