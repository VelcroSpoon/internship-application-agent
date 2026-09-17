/**
 * Server-side calls to the FastAPI service.
 *
 * The browser never talks to FastAPI directly: reads happen in Server
 * Components and writes go through Server Actions. That keeps the API on
 * localhost with no CORS configuration and nothing to expose.
 */

import type {
  ApplicationDetail,
  ApplicationSummary,
  LoopRunResponse,
  PostingDetail,
  QueueItem,
  ScoreByDimension,
  ScoreByRound,
} from "./types";

export const API_BASE = process.env.API_BASE ?? "http://127.0.0.1:8000";

/** The loop blocks for the length of a Writer/Critic run. Node's default
 *  socket timeout is five minutes and a slow three-round loop gets close, so
 *  this call gets its own generous budget. */
const LOOP_TIMEOUT_MS = 15 * 60 * 1000;
const READ_TIMEOUT_MS = 15 * 1000;

export type ApiErrorKind = "unreachable" | "not_found" | "conflict" | "server";

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const { timeoutMs = READ_TIMEOUT_MS, ...rest } = init ?? {};
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...rest,
      signal: AbortSignal.timeout(timeoutMs),
      headers: { "Content-Type": "application/json", ...rest.headers },
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiError(
      "unreachable",
      `Could not reach the agent service at ${API_BASE}. Start it with: uv run python -m internship_agent serve`,
      undefined,
    );
  }

  if (!response.ok) {
    const detail = await response
      .json()
      .then((b) => (typeof b?.detail === "string" ? b.detail : JSON.stringify(b?.detail)))
      .catch(() => response.statusText);
    const kind: ApiErrorKind =
      response.status === 404
        ? "not_found"
        : response.status === 409
          ? "conflict"
          : "server";
    throw new ApiError(kind, detail || `Request failed (${response.status})`, response.status);
  }
  return (await response.json()) as T;
}

// --- reads -------------------------------------------------------------------

export const getQueue = () => request<QueueItem[]>("/queue");

export const getPosting = (id: number) => request<PostingDetail>(`/postings/${id}`);

export const getApplications = (status?: string) =>
  request<ApplicationSummary[]>(`/applications${status ? `?status=${status}` : ""}`);

export const getApplication = (id: number) =>
  request<ApplicationDetail>(`/applications/${id}`);

export const getScoresByRound = () => request<ScoreByRound[]>("/stats/scores-by-round");

export const getScoresByDimension = () =>
  request<ScoreByDimension[]>("/stats/scores-by-dimension");

// --- writes ------------------------------------------------------------------

export const runLoop = (postingId: number) =>
  request<LoopRunResponse>("/loop/run", {
    method: "POST",
    body: JSON.stringify({ posting_id: postingId }),
    timeoutMs: LOOP_TIMEOUT_MS,
  });

export const decide = (
  id: number,
  action: "approve" | "reject" | "submitted",
  note: string,
) =>
  request<{ application_id: number; status: string; message: string }>(
    `/applications/${id}/${action}`,
    { method: "POST", body: JSON.stringify({ note }) },
  );

export const saveEdit = (
  id: number,
  body: { bullets: { text: string; resume_anchor: string }[]; cover_letter: string; note: string },
) =>
  request<ApplicationDetail>(`/applications/${id}/drafts`, {
    method: "POST",
    body: JSON.stringify(body),
  });
