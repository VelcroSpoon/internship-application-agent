"""Screening schema: the small-model output contract. Bounds are enforced
here so a model that returns 110 or a paragraph-long reason is rejected
at the boundary rather than stored."""

import pytest
from pydantic import ValidationError

from internship_agent.screener.models import Screening


def _ok(**overrides) -> dict:
    base = {
        "fit_score": 72,
        "reason": "Python and ML coursework match; no Rust.",
        "is_internship": True,
        "matched_requirements": ["Python", "PyTorch"],
        "missing_requirements": ["Rust"],
        "disqualifiers": [],
    }
    return {**base, **overrides}


def test_valid_screening_parses():
    s = Screening.model_validate(_ok())
    assert s.fit_score == 72 and s.is_internship is True


def test_lists_are_required_not_defaulted():
    # Deliberate: with schema-constrained decoding the model always emits every
    # key, and a missing key means something upstream is wrong.
    with pytest.raises(ValidationError):
        Screening.model_validate({"fit_score": 10, "reason": "no", "is_internship": False})


@pytest.mark.parametrize("score", [-1, 101, 55.5, "high"])
def test_fit_score_must_be_int_in_0_100(score):
    with pytest.raises(ValidationError):
        Screening.model_validate(_ok(fit_score=score))


def test_reason_is_capped():
    with pytest.raises(ValidationError):
        Screening.model_validate(_ok(reason="x" * 241))


def test_requirement_lists_are_capped_at_five():
    with pytest.raises(ValidationError):
        Screening.model_validate(_ok(matched_requirements=list("abcdef")))


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Screening.model_validate(_ok(confidence=0.9))


def test_json_schema_has_no_optional_fields_for_the_decoder():
    """Ollama constrains decoding to this schema. Every key must be required so the
    model cannot dodge the hard fields."""
    schema = Screening.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
