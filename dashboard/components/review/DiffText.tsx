import type { Change } from "diff";

/**
 * Inline word diff. Additions and deletions sit where they happened, rather
 * than in two columns: a cover letter is prose, and side-by-side means
 * reading both blocks and aligning them by eye.
 */
export default function DiffText({ changes }: { changes: Change[] }) {
  return (
    <>
      {changes.map((change, i) => {
        // The padding is not decoration: word diffs put a deletion straight
        // against its replacement, and "WroteBuilt" reads as one word without it.
        if (change.added) {
          return (
            <ins key={i} className="mx-px rounded-[2px] bg-add-soft px-0.5 text-add no-underline">
              {change.value}
            </ins>
          );
        }
        if (change.removed) {
          return (
            <del key={i} className="mx-px rounded-[2px] bg-del-soft px-0.5 text-del decoration-del/60">
              {change.value}
            </del>
          );
        }
        return <span key={i}>{change.value}</span>;
      })}
    </>
  );
}

export function DiffLegend() {
  return (
    <span className="text-2xs text-faint">
      <ins className="bg-add-soft text-add no-underline px-1">added</ins>{" "}
      <del className="bg-del-soft text-del px-1">removed</del> since the previous round
    </span>
  );
}
