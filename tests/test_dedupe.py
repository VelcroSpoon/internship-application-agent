"""Dedupe key: sha256 over normalised company|title|location. Cosmetic
variation in a board's text must not create a second posting row."""

from internship_agent.scout.dedupe import content_hash, dedupe_hash, normalize


def test_normalize_collapses_case_punctuation_and_whitespace():
    assert normalize("  Software Engineering Intern (Summer 2027) ") == (
        "software engineering intern summer 2027"
    )


def test_normalize_handles_none():
    assert normalize(None) == ""


def test_normalize_folds_unicode_compatibility_forms():
    # NFKC turns fullwidth and ligature forms into their ASCII equivalents.
    assert normalize("Ｓｏｆｔware ﬁnance") == "software finance"


def test_same_posting_with_cosmetic_title_variation_hashes_equal():
    a = dedupe_hash("Scale AI", "Software Engineering Intern (Summer 2027) ", "San Francisco, CA")
    b = dedupe_hash("scale ai", "software engineering intern - summer 2027", "san francisco ca")
    assert a == b


def test_different_location_hashes_differently():
    sf = dedupe_hash("Scale AI", "Software Engineering Intern", "San Francisco, CA")
    london = dedupe_hash("Scale AI", "Software Engineering Intern", "London, UK")
    assert sf != london


def test_different_company_hashes_differently():
    assert dedupe_hash("A", "SWE Intern", "SF") != dedupe_hash("B", "SWE Intern", "SF")


def test_field_boundaries_are_not_ambiguous():
    # "a b" | "c" must not collide with "a" | "b c".
    assert dedupe_hash("a b", "c", "x") != dedupe_hash("a", "b c", "x")


def test_dedupe_hash_is_hex_sha256():
    h = dedupe_hash("A", "B", "C")
    assert len(h) == 64 and all(ch in "0123456789abcdef" for ch in h)


def test_content_hash_ignores_whitespace_only_changes():
    assert content_hash("We build   things.\n\nApply now.") == content_hash(
        "We build things. Apply now."
    )


def test_content_hash_detects_real_edits():
    assert content_hash("Requires Python.") != content_hash("Requires Python and Rust.")
