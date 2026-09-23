"use client";

import { useCallback, useMemo, useState } from "react";
import { assignFindings, draftBlocks } from "@/lib/highlight";
import type { ApplicationDetail } from "@/lib/types";
import CritiquePanel from "./CritiquePanel";
import { DiffLegend } from "./DiffText";
import DraftBody from "./DraftBody";
import EditForm from "./EditForm";
import ReviewActions from "./ReviewActions";
import RevisionRail from "./RevisionRail";

/**
 * Owns everything interactive on the review page. All rounds arrived in the
 * server component's single fetch, so switching revisions is local state
 * rather than a navigation: clicking through rounds is the main action here
 * and a round trip per click would be the slowest part of the page.
 */
export default function ReviewClient({ application }: { application: ApplicationDetail }) {
  const rounds = application.drafts;
  const last = rounds.at(-1);
  const [selected, setSelected] = useState(last?.round_index ?? 0);
  const [showDiff, setShowDiff] = useState(true);
  const [editing, setEditing] = useState(false);

  const position = rounds.findIndex((r) => r.round_index === selected);
  const round = rounds[position] ?? last;
  const previous = position > 0 ? rounds[position - 1] : null;

  const assignment = useMemo(() => {
    if (!round) return { byBlock: {}, unmatched: new Set<number>() };
    return assignFindings(draftBlocks(round), round.critique?.findings ?? []);
  }, [round]);

  /** Jumping only makes sense against the clean text, so turn the diff off
   *  first and scroll once the highlight has rendered. */
  const jumpToFinding = useCallback((index: number) => {
    setShowDiff(false);
    requestAnimationFrame(() => {
      const target = document.getElementById(`excerpt-${index}`);
      if (!target) return;
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.remove("excerpt-flash");
      void target.offsetWidth; // restart the animation if it is already applied
      target.classList.add("excerpt-flash");
    });
  }, []);

  if (!round) {
    return (
      <p className="px-6 py-8 text-muted">
        This application has no drafts. The loop stopped before the Writer produced one.
      </p>
    );
  }

  return (
    <div className="grid min-h-0 grid-cols-1 gap-6 px-6 py-4 lg:grid-cols-[11rem_minmax(0,1fr)] xl:grid-cols-[11rem_minmax(0,1fr)_24rem]">
      <div className="lg:border-r lg:border-rule lg:pr-2">
        <RevisionRail rounds={rounds} selected={selected} onSelect={setSelected} />
      </div>

      <div className="min-w-0">
        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
          <h2 className="font-semibold">
            {round.authored_by === "human" ? "Your edit" : `Round ${round.round_index}`}
          </h2>
          <span className="text-2xs text-faint">
            {round.writer_model ?? "written by you"}
          </span>
          {previous ? (
            <label className="ml-auto flex items-center gap-1.5 text-muted">
              <input
                type="checkbox"
                checked={showDiff}
                onChange={(e) => setShowDiff(e.target.checked)}
              />
              show changes from round {previous.round_index}
            </label>
          ) : (
            <span className="ml-auto text-2xs text-faint">
              first draft · nothing to compare against
            </span>
          )}
        </div>

        {showDiff && previous ? (
          <p className="mb-3">
            <DiffLegend />
          </p>
        ) : null}

        {round.voice_hits.length > 0 || round.critique_leaks.length > 0 ? (
          <div className="mb-4 space-y-1">
            {round.voice_hits.length > 0 ? (
              <p className="border-l-2 border-major bg-major-soft px-2.5 py-1">
                <span className="label text-major">voice</span>{" "}
                {round.voice_hits.join(" · ")}
              </p>
            ) : null}
            {round.critique_leaks.length > 0 ? (
              <p className="border-l-2 border-blocker bg-blocker-soft px-2.5 py-1">
                <span className="label text-blocker">critique leaked into the draft</span>{" "}
                {round.critique_leaks.join(" · ")}
              </p>
            ) : null}
          </div>
        ) : null}

        {editing ? (
          <EditForm
            applicationId={application.application_id}
            base={round}
            onDone={() => setEditing(false)}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <DraftBody
            round={round}
            previous={previous}
            showDiff={showDiff}
            assignment={assignment}
          />
        )}

        <div className="rule-t mt-6 pt-4">
          <ReviewActions
            applicationId={application.application_id}
            status={application.status}
            finalRound={last ?? round}
            onEdit={() => setEditing(true)}
          />
        </div>
      </div>

      <aside className="min-w-0 xl:border-l xl:border-rule xl:pl-6">
        <h2 className="label mb-2">Critique</h2>
        <CritiquePanel
          critique={round.critique}
          unmatched={assignment.unmatched}
          onJump={jumpToFinding}
          authoredByHuman={round.authored_by === "human"}
        />
      </aside>
    </div>
  );
}
