import { diffWords, type Change } from "diff";
import type { Bullet } from "./types";

/**
 * Bullets are a list, not prose, so diffing the whole block as one string
 * makes an inserted bullet look like every later bullet changed. Pairing by
 * index keeps each bullet's diff local, and a change in count shows up as a
 * whole bullet added or removed.
 *
 * Pairing by index assumes the Writer revises bullets in place rather than
 * reordering them, which is what the revision prompt asks for. A reorder shows
 * as churn; that is visible and honest rather than wrong.
 */

export type BulletDiff =
  | { kind: "changed"; bullet: Bullet; changes: Change[] }
  | { kind: "added"; bullet: Bullet }
  | { kind: "removed"; bullet: Bullet };

export function diffBullets(previous: Bullet[], current: Bullet[]): BulletDiff[] {
  const out: BulletDiff[] = [];
  const paired = Math.min(previous.length, current.length);

  for (let i = 0; i < paired; i++) {
    out.push({
      kind: "changed",
      bullet: current[i],
      changes: diffWords(previous[i].text, current[i].text),
    });
  }
  for (let i = paired; i < current.length; i++) {
    out.push({ kind: "added", bullet: current[i] });
  }
  for (let i = paired; i < previous.length; i++) {
    out.push({ kind: "removed", bullet: previous[i] });
  }
  return out;
}
