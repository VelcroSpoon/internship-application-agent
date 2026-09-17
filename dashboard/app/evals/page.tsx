import ScoreChart from "@/components/ScoreChart";
import { ApiFailure, Command, Notice } from "@/components/States";
import { getScoresByDimension, getScoresByRound } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function EvalsPage() {
  let byRound, byDimension;
  try {
    [byRound, byDimension] = await Promise.all([getScoresByRound(), getScoresByDimension()]);
  } catch (error) {
    return <ApiFailure error={error} what="scores" />;
  }

  if (byRound.length === 0) {
    return (
      <Notice title="No critiques yet">
        <p>
          This chart is built from every critique the loop has produced. Run the loop on a
          posting and it fills in.
        </p>
        <Command>uv run python -m internship_agent loop run --posting N</Command>
      </Notice>
    );
  }

  return (
    <div className="px-6 py-4">
      <div className="mb-3 flex items-baseline gap-3">
        <h1 className="text-base font-semibold tracking-tight">Score by round</h1>
        <span className="text-muted">
          Mean across every application. Your own edits are excluded.
        </span>
      </div>
      <ScoreChart byRound={byRound} byDimension={byDimension} />
    </div>
  );
}
