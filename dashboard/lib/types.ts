/**
 * Mirrors of the FastAPI response models in internship_agent/api/schemas.py.
 * Hand-written rather than generated: the surface is small, and a generator
 * would be another dependency to justify for twelve endpoints.
 */

export type Dimension =
  | "grounding"
  | "coverage"
  | "specificity"
  | "density"
  | "voice";

export type Severity = "blocker" | "major" | "minor";

export interface DimensionScore {
  dimension: Dimension;
  score: number;
  reason: string;
}

export interface Finding {
  dimension: Dimension;
  severity: Severity;
  section: "bullets" | "cover_letter" | "both";
  /** Quoted verbatim from the draft. May not match if the model paraphrased. */
  excerpt: string;
  problem: string;
  fix_direction: string;
  resume_anchor: string | null;
}

export interface Critique {
  round_index: number;
  scores: DimensionScore[];
  findings: Finding[];
  unsupported_claim_count: number;
  missing_requirements: string[];
  verdict: "accept" | "revise";
  overall: number;
}

export interface Bullet {
  text: string;
  resume_anchor: string;
}

export interface RoundDetail {
  round_index: number;
  draft_id: number;
  /** 'human' rounds are my own edits and never enter the eval numbers. */
  authored_by: "writer" | "human";
  bullets: Bullet[];
  cover_letter: string;
  writer_model: string | null;
  voice_hits: string[];
  critique_leaks: string[];
  critique: Critique | null;
}

export interface ApplicationSummary {
  application_id: number;
  posting_id: number;
  company: string;
  title: string;
  location: string | null;
  url: string;
  status: ApplicationStatus;
  rounds: number;
  first_overall: number | null;
  final_overall: number | null;
  final_blockers: number | null;
  created_at: string;
  updated_at: string;
}

export type ApplicationStatus =
  | "drafting"
  | "awaiting_review"
  | "approved"
  | "rejected"
  | "submitted";

export interface ApplicationDetail extends ApplicationSummary {
  drafts: RoundDetail[];
}

export interface QueueItem {
  posting_id: number;
  company: string;
  title: string;
  location: string | null;
  url: string;
  source: string;
  posted_at: string | null;
  first_seen_at: string;
  fit_score: number;
  reason: string;
  model: string;
  screened_at: string;
}

export interface ScreeningOut {
  model: string;
  fit_score: number;
  reason: string;
  is_internship: boolean | null;
  matched_requirements: string[];
  missing_requirements: string[];
  disqualifiers: string[];
  created_at: string;
}

export interface PostingDetail {
  posting_id: number;
  company: string;
  title: string;
  location: string | null;
  url: string;
  source: string;
  external_id: string | null;
  description: string | null;
  posted_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  status: string;
  screening: ScreeningOut | null;
  application: { application_id: number; status: ApplicationStatus } | null;
}

export interface ScoreByRound {
  round_index: number;
  mean_overall: number;
  applications: number;
}

export interface ScoreByDimension {
  round_index: number;
  dimension: Dimension;
  mean_score: number;
  applications: number;
}

export interface LoopRunResponse {
  application_id: number | null;
  status: ApplicationStatus;
  rounds_completed: number;
  stopped_because: string;
  stopped_detail: string | null;
  scores_by_round: {
    round_index: number;
    overall: number;
    unsupported_claim_count: number;
    verdict: string;
  }[];
}

export const DIMENSIONS: Dimension[] = [
  "grounding",
  "coverage",
  "specificity",
  "density",
  "voice",
];

/** The spec's WEIGHTS, for display only. Scoring happens in Python. */
export const DIMENSION_WEIGHT: Record<Dimension, number> = {
  grounding: 0.3,
  coverage: 0.25,
  specificity: 0.2,
  density: 0.15,
  voice: 0.1,
};
