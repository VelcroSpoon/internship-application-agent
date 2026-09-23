"use client";

import { diffWords } from "diff";
import { useMemo } from "react";
import { diffBullets } from "@/lib/bullet-diff";
import { segmentsFor, type Assignment, type Mark } from "@/lib/highlight";
import type { RoundDetail } from "@/lib/types";
import DiffText from "./DiffText";

/**
 * The draft itself. Two modes:
 *
 * - diff off: the clean current text, with each finding's excerpt highlighted
 *   in place so clicking a finding can jump to it.
 * - diff on: word-level changes against the previous round.
 *
 * Excerpt highlighting deliberately applies only when the diff is off. A word
 * diff cuts the text into segments and an excerpt can straddle several of
 * them; a highlighter that survives that is fragile for very little gain.
 * Clicking a finding turns the diff off first, which the parent handles.
 */
export default function DraftBody({
  round,
  previous,
  showDiff,
  assignment,
}: {
  round: RoundDetail;
  previous: RoundDetail | null;
  showDiff: boolean;
  assignment: Assignment;
}) {
  const letterDiff = useMemo(
    () => (previous ? diffWords(previous.cover_letter, round.cover_letter) : null),
    [previous, round.cover_letter],
  );
  const bulletDiffs = useMemo(
    () => (previous ? diffBullets(previous.bullets, round.bullets) : null),
    [previous, round.bullets],
  );

  const diffing = showDiff && previous !== null;

  return (
    <div className="min-w-0">
      <h3 className="label">Bullets</h3>
      <ul className="mt-2 space-y-3">
        {diffing && bulletDiffs
          ? bulletDiffs.map((entry, i) => (
              <li key={i} className="prose-draft flex gap-2">
                <span className="select-none text-faint">
                  {entry.kind === "added" ? "+" : entry.kind === "removed" ? "−" : "·"}
                </span>
                <span className={entry.kind === "removed" ? "text-del line-through" : ""}>
                  {entry.kind === "changed" ? (
                    <DiffText changes={entry.changes} />
                  ) : (
                    entry.bullet.text
                  )}
                </span>
              </li>
            ))
          : round.bullets.map((bullet, i) => (
              <li key={i} className="flex gap-2">
                <span className="prose-draft select-none text-faint">·</span>
                <div className="min-w-0">
                  <p className="prose-draft">
                    <Highlighted
                      text={bullet.text}
                      marks={assignment.byBlock[`bullet-${i}`] ?? []}
                    />
                  </p>
                  <p className="mt-0.5 text-2xs text-faint">
                    anchor: {bullet.resume_anchor || "—"}
                  </p>
                </div>
              </li>
            ))}
      </ul>

      <h3 className="label rule-t mt-6 pt-3">Cover letter</h3>
      <div className="prose-draft mt-2 max-w-[78ch]">
        {diffing && letterDiff ? (
          <DiffText changes={letterDiff} />
        ) : (
          <Highlighted text={round.cover_letter} marks={assignment.byBlock.letter ?? []} />
        )}
      </div>
    </div>
  );
}

function Highlighted({ text, marks }: { text: string; marks: Mark[] }) {
  if (marks.length === 0) return <>{text}</>;
  return (
    <>
      {segmentsFor(text, marks).map((segment, i) =>
        segment.finding === null ? (
          <span key={i}>{segment.text}</span>
        ) : (
          <mark
            key={i}
            id={`excerpt-${segment.finding}`}
            className="bg-major-soft text-ink"
          >
            {segment.text}
          </mark>
        ),
      )}
    </>
  );
}
