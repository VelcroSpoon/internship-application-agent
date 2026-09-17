import type { QueueItem } from "./types";

/** Days since a posting went up. Uses the board's own publish date when it
 *  reports one, and falls back to when the scout first saw it. */
export function ageDays(item: Pick<QueueItem, "posted_at" | "first_seen_at">): number {
  const basis = item.posted_at ?? item.first_seen_at;
  const then = Date.parse(basis);
  if (Number.isNaN(then)) return 0;
  return Math.max(0, Math.floor((Date.now() - then) / 86_400_000));
}

export function ageLabel(item: Pick<QueueItem, "posted_at" | "first_seen_at">): string {
  const days = ageDays(item);
  if (days === 0) return "today";
  if (days === 1) return "1d";
  if (days < 60) return `${days}d`;
  return `${Math.floor(days / 30)}mo`;
}

/** 'greenhouse:scaleai' reads as 'greenhouse' in a dense table. */
export function sourceLabel(source: string): string {
  return source.split(":")[0] ?? source;
}

export function shortDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function scoreTone(score: number, max = 5): "strong" | "fair" | "weak" {
  const ratio = score / max;
  if (ratio >= 0.8) return "strong";
  if (ratio >= 0.6) return "fair";
  return "weak";
}

export const STATUS_LABEL: Record<string, string> = {
  drafting: "drafting",
  awaiting_review: "needs review",
  approved: "approved",
  rejected: "rejected",
  submitted: "submitted",
};

/** What the orchestrator's stop reason means, in words. */
export const STOP_REASON_LABEL: Record<string, string> = {
  quality_bar: "cleared the quality bar",
  round_cap: "hit the three-round cap",
  plateau: "stopped improving",
  blockers_unresolved_at_round_cap: "still has unsupported claims at the round cap",
  critic_failed: "the critic could not be read",
  writer_failed: "the writer could not produce a draft",
  backend_unreachable: "the model backend was unreachable",
};
