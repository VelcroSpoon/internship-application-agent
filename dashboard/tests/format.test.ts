import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { draftToText } from "../lib/draft-text";
import { ageDays, ageLabel, sourceLabel } from "../lib/format";
import type { RoundDetail } from "../lib/types";

const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

describe("posting age", () => {
  it("uses the board's own publish date when it has one", () => {
    assert.equal(ageDays({ posted_at: daysAgo(10), first_seen_at: daysAgo(2) }), 10);
  });

  it("falls back to when the scout first saw it", () => {
    assert.equal(ageDays({ posted_at: null, first_seen_at: daysAgo(4) }), 4);
  });

  it("never reports a negative age for a date in the future", () => {
    assert.equal(ageDays({ posted_at: daysAgo(-3), first_seen_at: daysAgo(0) }), 0);
  });

  it("does not crash on an unparseable date", () => {
    assert.equal(ageDays({ posted_at: "not a date", first_seen_at: daysAgo(1) }), 0);
  });

  it("labels ages compactly for a dense table", () => {
    assert.equal(ageLabel({ posted_at: daysAgo(0), first_seen_at: daysAgo(0) }), "today");
    assert.equal(ageLabel({ posted_at: daysAgo(1), first_seen_at: daysAgo(0) }), "1d");
    assert.equal(ageLabel({ posted_at: daysAgo(19), first_seen_at: daysAgo(0) }), "19d");
    assert.equal(ageLabel({ posted_at: daysAgo(95), first_seen_at: daysAgo(0) }), "3mo");
  });
});

describe("sourceLabel", () => {
  it("drops the board token", () => {
    assert.equal(sourceLabel("greenhouse:scaleai"), "greenhouse");
  });
});

describe("draftToText", () => {
  it("renders bullets then the letter, ready to paste", () => {
    const round = {
      bullets: [
        { text: "Built X.", resume_anchor: "a" },
        { text: "Shipped Y.", resume_anchor: "b" },
      ],
      cover_letter: "  Dear team.\n\nI build things.  ",
    } as RoundDetail;
    assert.equal(draftToText(round), "- Built X.\n- Shipped Y.\n\nDear team.\n\nI build things.\n");
  });
});
