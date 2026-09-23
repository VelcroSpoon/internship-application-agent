import type { Finding, RoundDetail } from "./types";

/**
 * Mapping a critique's verbatim excerpts back onto the draft text.
 *
 * The Critic is told to quote exactly, but it is a model, so a miss is
 * expected rather than exceptional. An excerpt that cannot be located is
 * reported as unmatched and its finding renders without a jump target. It is
 * never a crash and never a silent wrong highlight.
 */

export interface Segment {
  text: string;
  /** Index into the critique's findings array, or null for ordinary text. */
  finding: number | null;
}

export interface Mark {
  finding: number;
  excerpt: string;
}

/** Exact match, then the same excerpt trimmed. Anything looser risks
 *  highlighting a span the finding was not about. */
function locate(text: string, excerpt: string): [number, number] | null {
  for (const candidate of [excerpt, excerpt.trim()]) {
    if (!candidate) continue;
    const start = text.indexOf(candidate);
    if (start !== -1) return [start, start + candidate.length];
  }
  return null;
}

export function segmentsFor(text: string, marks: Mark[]): Segment[] {
  const ranges: { start: number; end: number; finding: number }[] = [];
  for (const mark of marks) {
    const found = locate(text, mark.excerpt);
    if (found) ranges.push({ start: found[0], end: found[1], finding: mark.finding });
  }
  ranges.sort((a, b) => a.start - b.start);

  const segments: Segment[] = [];
  let cursor = 0;
  for (const range of ranges) {
    if (range.start < cursor) continue; // overlapping excerpts: first one wins
    if (range.start > cursor) {
      segments.push({ text: text.slice(cursor, range.start), finding: null });
    }
    segments.push({ text: text.slice(range.start, range.end), finding: range.finding });
    cursor = range.end;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), finding: null });
  return segments;
}

export interface Assignment {
  /** Marks to render, keyed by the block of text they were found in. */
  byBlock: Record<string, Mark[]>;
  /** Findings whose excerpt is nowhere in the draft. */
  unmatched: Set<number>;
}

/** Each finding is highlighted at most once, in the first block that has it. */
export function assignFindings(
  blocks: { key: string; text: string }[],
  findings: Finding[],
): Assignment {
  const byBlock: Record<string, Mark[]> = Object.fromEntries(
    blocks.map((block) => [block.key, [] as Mark[]]),
  );
  const unmatched = new Set<number>();

  findings.forEach((finding, index) => {
    const block = blocks.find((b) => locate(b.text, finding.excerpt) !== null);
    if (block) byBlock[block.key].push({ finding: index, excerpt: finding.excerpt });
    else unmatched.add(index);
  });

  return { byBlock, unmatched };
}

/**
 * The blocks of a draft that a finding can be highlighted in: each bullet on
 * its own, then the cover letter. Bullets are separate blocks because each is
 * rendered separately; matching against all of them joined together counted
 * an excerpt spanning two bullets as found, then no single bullet could show
 * it, and the finding offered a jump to nothing.
 */
export function draftBlocks(round: Pick<RoundDetail, "bullets" | "cover_letter">) {
  return [
    ...round.bullets.map((bullet, i) => ({ key: `bullet-${i}`, text: bullet.text })),
    { key: "letter", text: round.cover_letter },
  ];
}
