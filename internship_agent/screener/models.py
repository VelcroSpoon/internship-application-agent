"""Screener output contract.

Kept deliberately small: the Screener runs on a 3B local model, and every
field here costs the model attention. Every field is required (no defaults)
because decoding is schema-constrained; a missing key means the backend or
the prompt is broken, and that should fail loudly.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Screening(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fit_score: int = Field(
        ge=0,
        le=100,
        description="0-100. 90+: near-perfect match. 70-89: strong, apply. "
        "40-69: partial. <40: poor fit or wrong role type.",
        strict=True,
    )
    reason: str = Field(
        max_length=240,
        description="One sentence, under 30 words, naming the deciding factor.",
    )
    is_internship: bool = Field(
        description="True only if this is an internship/co-op for the target cycle."
    )
    matched_requirements: list[str] = Field(
        max_length=5,
        description="Posting requirements the resume clearly supports. Short phrases.",
    )
    missing_requirements: list[str] = Field(
        max_length=5,
        description="Posting requirements the resume does not show. Short phrases.",
    )
    disqualifiers: list[str] = Field(
        max_length=5,
        description="Hard blockers present in the posting (clearance, PhD, location). "
        "Empty if none.",
    )
