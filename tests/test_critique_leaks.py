"""The Critic-writes-prose guard. A fix_direction is instruction; if it turns
up inside the draft it produced, the Critic has written the draft."""

from internship_agent.agents.critic import Dimension, Severity
from internship_agent.critic.leaks import find_critique_leaks
from tests.critique_factory import critique, finding

FIX = "Replace the user-count claim with the actual figure from the resume or cut it entirely"


def test_a_fix_direction_pasted_into_the_draft_is_caught():
    c = critique(findings=[finding(fix_direction=FIX)])
    draft = f"I did the work. {FIX}. Then I shipped it."

    leaks = find_critique_leaks(draft, c)

    assert len(leaks) == 1
    assert leaks[0].startswith("finding 0 (grounding):")


def test_a_lightly_edited_paste_is_still_caught():
    """Capitalisation and punctuation changes do not launder a copy."""
    c = critique(findings=[finding(fix_direction=FIX)])
    draft = "Replace the User-Count claim, with the actual figure from the resume; or cut it."

    assert len(find_critique_leaks(draft, c)) == 1


def test_a_draft_that_acts_on_the_finding_in_its_own_words_is_clean():
    c = critique(findings=[finding(fix_direction=FIX)])
    draft = "Deduplicated 12% near-duplicate tickets across a 40k-ticket corpus using MinHash."

    assert find_critique_leaks(draft, c) == []


def test_shared_technical_jargon_does_not_false_positive():
    c = critique(
        findings=[
            finding(fix_direction="Name the model architecture and the dataset size in the bullet.")
        ]
    )
    draft = "Fine-tuned a BERT-style classifier on 40k labelled tickets using PyTorch."

    assert find_critique_leaks(draft, c) == []


def test_each_leaking_finding_is_reported_separately():
    second = "State the macro F1 before and after rather than the percentage improvement alone"
    c = critique(
        findings=[
            finding(fix_direction=FIX),
            finding(Dimension.SPECIFICITY, Severity.MAJOR, fix_direction=second),
        ]
    )
    draft = f"{FIX}. And also: {second}."

    leaks = find_critique_leaks(draft, c)

    assert len(leaks) == 2
    assert leaks[1].startswith("finding 1 (specificity):")


def test_no_findings_means_no_leaks():
    assert find_critique_leaks("Any draft text at all here.", critique()) == []


def test_a_draft_shorter_than_the_window_cannot_leak():
    c = critique(findings=[finding(fix_direction=FIX)])

    assert find_critique_leaks("Too short.", c) == []
