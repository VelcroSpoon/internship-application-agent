"use client";

import { DIMENSION_WEIGHT, type Critique, type Finding, type Severity } from "@/lib/types";

/**
 * Scores, then findings. Blockers are first and marked in red, because an
 * unsupported claim is the one defect that must not get through: the draft can
 * score well overall and still be unusable.
 */

const SEVERITY_ORDER: Record<Severity, number> = { blocker: 0, major: 1, minor: 2 };

const SEVERITY_STYLE: Record<Severity, string> = {
  blocker: "border-blocker bg-blocker-soft",
  major: "border-major bg-major-soft",
  minor: "border-rule-strong bg-surface",
};

const SEVERITY_LABEL: Record<Severity, string> = {
  blocker: "text-blocker",
  major: "text-major",
  minor: "text-muted",
};

export default function CritiquePanel({
  critique,
  unmatched,
  onJump,
  authoredByHuman,
}: {
  critique: Critique | null;
  unmatched: Set<number>;
  onJump: (findingIndex: number) => void;
  authoredByHuman: boolean;
}) {
  if (authoredByHuman) {
    return (
      <p className="text-muted">
        This round is your own edit. Nothing critiques it, and it is excluded from the
        score-by-round numbers so your editing never reads as the model improving.
      </p>
    );
  }
  if (!critique) {
    return (
      <p className="text-muted">
        No critique for this round. The loop stopped before the Critic could score it.
      </p>
    );
  }

  const ordered = critique.findings
    .map((finding, index) => ({ finding, index }))
    .sort((a, b) => SEVERITY_ORDER[a.finding.severity] - SEVERITY_ORDER[b.finding.severity]);

  return (
    <div className="space-y-5">
      {critique.unsupported_claim_count > 0 ? (
        <p className="border-l-2 border-blocker bg-blocker-soft px-3 py-2 font-medium text-blocker">
          {critique.unsupported_claim_count} unsupported claim
          {critique.unsupported_claim_count === 1 ? "" : "s"}. Do not send this as it stands.
        </p>
      ) : null}

      <div>
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-2xl font-semibold">
            {critique.overall.toFixed(2)}
          </span>
          <span className="text-muted">
            overall · verdict {critique.verdict}
          </span>
        </div>
        <ul className="mt-2 space-y-1.5">
          {critique.scores.map((score) => (
            <li key={score.dimension}>
              <div className="flex items-baseline gap-2">
                <span className="w-20 shrink-0 text-2xs uppercase tracking-wider text-muted">
                  {score.dimension}
                </span>
                <ScoreBar score={score.score} />
                <span className="font-mono font-semibold">{score.score}</span>
                <span className="text-2xs text-faint">
                  ×{DIMENSION_WEIGHT[score.dimension]}
                </span>
              </div>
              <p className="ml-22 text-muted">{score.reason}</p>
            </li>
          ))}
        </ul>
      </div>

      {critique.missing_requirements.length > 0 ? (
        <div className="rule-t pt-3">
          <h3 className="label">Unaddressed requirements the resume could support</h3>
          <ul className="mt-1 space-y-0.5 text-muted">
            {critique.missing_requirements.map((item, i) => (
              <li key={i}>· {item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="rule-t pt-3">
        <h3 className="label">
          {ordered.length} finding{ordered.length === 1 ? "" : "s"}
        </h3>
        {ordered.length === 0 ? (
          <p className="mt-1 text-muted">
            Nothing flagged. An empty findings list is a legitimate result.
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {ordered.map(({ finding, index }) => (
              <FindingRow
                key={index}
                finding={finding}
                locatable={!unmatched.has(index)}
                onJump={() => onJump(index)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function FindingRow({
  finding,
  locatable,
  onJump,
}: {
  finding: Finding;
  locatable: boolean;
  onJump: () => void;
}) {
  return (
    <li className={`border-l-2 px-2.5 py-1.5 ${SEVERITY_STYLE[finding.severity]}`}>
      <p className="flex flex-wrap items-baseline gap-x-2">
        <span
          className={`text-2xs font-semibold uppercase tracking-wider ${SEVERITY_LABEL[finding.severity]}`}
        >
          {finding.severity}
        </span>
        <span className="text-2xs uppercase tracking-wider text-faint">
          {finding.dimension} · {finding.section}
        </span>
      </p>
      {locatable ? (
        <button
          onClick={onJump}
          title="Show this in the draft"
          className="mt-0.5 block text-left font-mono text-[13px] text-accent underline decoration-dotted underline-offset-4"
        >
          “{finding.excerpt}”
        </button>
      ) : (
        <p className="mt-0.5 font-mono text-[13px] text-muted">
          “{finding.excerpt}”
          <span className="ml-1 not-italic text-2xs text-faint">
            (not found verbatim in the draft)
          </span>
        </p>
      )}
      <p className="mt-1">{finding.problem}</p>
      <p className="mt-0.5 text-muted">→ {finding.fix_direction}</p>
      {finding.resume_anchor ? (
        <p className="mt-0.5 text-2xs text-faint">resume: {finding.resume_anchor}</p>
      ) : null}
    </li>
  );
}

function ScoreBar({ score }: { score: number }) {
  const tone = score >= 4 ? "bg-strong" : score >= 3 ? "bg-fair" : "bg-weak";
  return (
    <span className="flex gap-0.5" aria-hidden>
      {[1, 2, 3, 4, 5].map((n) => (
        <span
          key={n}
          className={`h-2.5 w-1.5 ${n <= score ? tone : "bg-rule"}`}
        />
      ))}
    </span>
  );
}
