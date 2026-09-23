import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { diffBullets } from "../lib/bullet-diff";

const b = (text: string) => ({ text, resume_anchor: "a" });

describe("diffBullets", () => {
  it("diffs bullets in place when the count is unchanged", () => {
    const out = diffBullets([b("Wrote the pipeline.")], [b("Built the pipeline.")]);
    assert.equal(out.length, 1);
    assert.equal(out[0].kind, "changed");
    if (out[0].kind === "changed") {
      assert.ok(out[0].changes.some((c) => c.removed && c.value.includes("Wrote")));
      assert.ok(out[0].changes.some((c) => c.added && c.value.includes("Built")));
    }
  });

  it("shows an identical bullet with no additions or removals", () => {
    const [entry] = diffBullets([b("Same text.")], [b("Same text.")]);
    assert.equal(entry.kind, "changed");
    if (entry.kind === "changed") {
      assert.ok(entry.changes.every((c) => !c.added && !c.removed));
    }
  });

  it("marks a new bullet as added rather than churning the others", () => {
    const out = diffBullets([b("One."), b("Two.")], [b("One."), b("Two."), b("Three.")]);
    assert.deepEqual(
      out.map((e) => e.kind),
      ["changed", "changed", "added"],
    );
    assert.equal(out[2].bullet.text, "Three.");
  });

  it("marks a dropped bullet as removed", () => {
    const out = diffBullets([b("One."), b("Two.")], [b("One.")]);
    assert.deepEqual(
      out.map((e) => e.kind),
      ["changed", "removed"],
    );
    assert.equal(out[1].bullet.text, "Two.");
  });
});
