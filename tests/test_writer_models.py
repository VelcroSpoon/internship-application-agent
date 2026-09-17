"""Draft schema: the Writer's output contract. Every bullet cites the
master-resume line it rests on, which is what the Critic's grounding
check and the review UI both consume."""

import pytest
from pydantic import ValidationError

from internship_agent.writer.models import Bullet, Draft

LETTER = "Dear team, " + "I built the thing and it worked. " * 12


def bullet(i: int = 0) -> dict:
    return {"text": f"Did thing {i} with Python.", "resume_anchor": f"Resume line {i}"}


def draft(**overrides) -> dict:
    base = {"bullets": [bullet(i) for i in range(4)], "cover_letter": LETTER}
    return {**base, **overrides}


def test_valid_draft_parses():
    d = Draft.model_validate(draft())
    assert len(d.bullets) == 4 and isinstance(d.bullets[0], Bullet)


@pytest.mark.parametrize("n", [0, 2, 7])
def test_bullet_count_must_be_three_to_six(n):
    with pytest.raises(ValidationError):
        Draft.model_validate(draft(bullets=[bullet(i) for i in range(n)]))


def test_bullet_anchor_is_required_and_non_empty():
    with pytest.raises(ValidationError):
        Bullet.model_validate({"text": "x", "resume_anchor": ""})
    with pytest.raises(ValidationError):
        Bullet.model_validate({"text": "x"})


def test_cover_letter_has_bounds():
    with pytest.raises(ValidationError):
        Draft.model_validate(draft(cover_letter="Too short."))
    with pytest.raises(ValidationError):
        Draft.model_validate(draft(cover_letter="x" * 4001))


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        Draft.model_validate(draft(subject_line="Hi"))


def test_all_fields_required_in_schema():
    schema = Draft.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])


def test_plain_text_rendering_for_diffs_and_review():
    d = Draft.model_validate(draft())
    text = d.as_text()
    assert text.startswith("## Bullets")
    assert "- Did thing 0 with Python." in text
    assert "## Cover letter" in text and LETTER.strip() in text
