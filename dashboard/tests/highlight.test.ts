import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { assignFindings, draftBlocks, segmentsFor } from "../lib/highlight";
import type { Finding, RoundDetail } from "../lib/types";

function finding(excerpt: string): Finding {
  return {
    dimension: "grounding",
    severity: "blocker",
    section: "bullets",
    excerpt,
    problem: "p",
    fix_direction: "f",
    resume_anchor: null,
  };
}

function round(bullets: string[], letter: string): RoundDetail {
  return {
    round_index: 0,
    draft_id: 1,
    authored_by: "writer",
    bullets: bullets.map((text) => ({ text, resume_anchor: "a" })),
    cover_letter: letter,
    writer_model: "m",
    voice_hits: [],
    critique_leaks: [],
    critique: null,
  };
}

const joined = (segments: { text: string }[]) => segments.map((s) => s.text).join("");

describe("segmentsFor", () => {
  it("marks an exact excerpt with its finding index", () => {
    const segments = segmentsFor("led a team serving 10,000 users daily", [
      { finding: 3, excerpt: "serving 10,000 users daily" },
    ]);
    assert.deepEqual(
      segments.filter((s) => s.finding !== null),
      [{ text: "serving 10,000 users daily", finding: 3 }],
    );
  });

  it("matches an excerpt the model padded with whitespace", () => {
    const segments = segmentsFor("built a pipeline", [{ finding: 0, excerpt: "  a pipeline " }]);
    assert.equal(segments.find((s) => s.finding === 0)?.text, "a pipeline");
  });

  it("leaves an excerpt that is not in the text unhighlighted rather than guessing", () => {
    const segments = segmentsFor("the real text", [{ finding: 0, excerpt: "a paraphrase" }]);
    assert.deepEqual(segments, [{ text: "the real text", finding: null }]);
  });

  it("keeps the first of two overlapping excerpts", () => {
    const segments = segmentsFor("abcdef", [
      { finding: 0, excerpt: "bcd" },
      { finding: 1, excerpt: "cde" },
    ]);
    assert.deepEqual(
      segments.filter((s) => s.finding !== null).map((s) => s.finding),
      [0],
    );
  });

  it("never loses or duplicates a character, whatever the marks", () => {
    const text = "Fine-tuned a classifier; raised macro-F1 from 0.71 to 0.84. Shipped it.";
    const segments = segmentsFor(text, [
      { finding: 0, excerpt: "raised macro-F1" },
      { finding: 1, excerpt: "macro-F1 from 0.71" }, // overlaps the first
      { finding: 2, excerpt: "Shipped it." },
      { finding: 3, excerpt: "not present" },
    ]);
    assert.equal(joined(segments), text);
  });
});

describe("assignFindings over a draft", () => {
  it("finds an excerpt inside one bullet", () => {
    const r = round(["Built X with Python.", "Wrote Y in C."], "A letter.");
    const { byBlock, unmatched } = assignFindings(draftBlocks(r), [finding("Y in C")]);
    assert.deepEqual(byBlock["bullet-1"], [{ finding: 0, excerpt: "Y in C" }]);
    assert.equal(unmatched.size, 0);
  });

  it("finds an excerpt in the cover letter", () => {
    const r = round(["Built X."], "I am passionate about compilers.");
    const { byBlock } = assignFindings(draftBlocks(r), [finding("passionate about")]);
    assert.equal(byBlock.letter?.length, 1);
  });

  it("reports an excerpt spanning two bullets as unmatched instead of a dead jump link", () => {
    // Joined, the bullets read "...Python.\nWrote Y...". An excerpt across that
    // line break used to count as found, then no single bullet could highlight
    // it, so the finding offered a jump to nothing.
    const r = round(["Built X with Python.", "Wrote Y in C."], "A letter.");
    const { unmatched } = assignFindings(draftBlocks(r), [finding("Python.\nWrote Y")]);
    assert.deepEqual([...unmatched], [0]);
  });

  it("reports a paraphrased excerpt as unmatched", () => {
    const r = round(["Built X."], "A letter.");
    const { unmatched } = assignFindings(draftBlocks(r), [finding("constructed X")]);
    assert.deepEqual([...unmatched], [0]);
  });
});
