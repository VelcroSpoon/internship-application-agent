"use client";

import type { RoundDetail } from "@/lib/types";

/**
 * Every round persisted by the loop, selectable. The score and blocker count
 * sit here so the shape of the run is readable without opening each round.
 */
export default function RevisionRail({
  rounds,
  selected,
  onSelect,
}: {
  rounds: RoundDetail[];
  selected: number;
  onSelect: (roundIndex: number) => void;
}) {
  return (
    <nav aria-label="Revisions">
      <h2 className="label">Revisions</h2>
      <ul className="mt-2">
        {rounds.map((round) => {
          const active = round.round_index === selected;
          const blockers = round.critique?.unsupported_claim_count ?? 0;
          return (
            <li key={round.draft_id}>
              <button
                onClick={() => onSelect(round.round_index)}
                aria-current={active ? "true" : undefined}
                className={`w-full border-l-2 px-2.5 py-1.5 text-left ${
                  active
                    ? "border-accent bg-accent-soft"
                    : "border-transparent hover:bg-accent-soft/50"
                }`}
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="font-medium">
                    {round.authored_by === "human" ? "your edit" : `round ${round.round_index}`}
                  </span>
                  <span className="font-mono">
                    {round.critique ? round.critique.overall.toFixed(2) : "—"}
                  </span>
                </span>
                <span className="mt-0.5 flex flex-wrap gap-x-2 text-2xs text-faint">
                  {blockers > 0 ? (
                    <span className="font-semibold text-blocker">
                      {blockers} blocker{blockers === 1 ? "" : "s"}
                    </span>
                  ) : null}
                  {round.critique ? (
                    <span>
                      {round.critique.findings.length} finding
                      {round.critique.findings.length === 1 ? "" : "s"}
                    </span>
                  ) : null}
                  {round.voice_hits.length > 0 ? (
                    <span>{round.voice_hits.length} voice</span>
                  ) : null}
                  {round.critique_leaks.length > 0 ? (
                    <span className="text-blocker">leak</span>
                  ) : null}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
