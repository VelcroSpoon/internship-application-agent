"use server";

/**
 * Mutations. These run on the Next server and call FastAPI from there, so the
 * browser never needs a cross-origin request and the API needs no CORS setup.
 *
 * Every one returns a result object rather than throwing, because the caller
 * is a form and an error message is part of the UI, not an exception.
 */

import { revalidatePath } from "next/cache";
import { ApiError, decide, runLoop, saveEdit } from "@/lib/api";
import type { LoopRunResponse } from "@/lib/types";

export type ActionResult<T = undefined> =
  | { ok: true; data: T }
  | { ok: false; error: string };

function failure(error: unknown): { ok: false; error: string } {
  if (error instanceof ApiError) return { ok: false, error: error.message };
  return { ok: false, error: error instanceof Error ? error.message : String(error) };
}

export async function startDraftingAction(
  postingId: number,
): Promise<ActionResult<LoopRunResponse>> {
  try {
    const data = await runLoop(postingId);
    revalidatePath("/");
    revalidatePath(`/postings/${postingId}`);
    if (data.application_id) revalidatePath(`/applications/${data.application_id}`);
    return { ok: true, data };
  } catch (error) {
    return failure(error);
  }
}

export async function decideAction(
  applicationId: number,
  action: "approve" | "reject" | "submitted",
  note: string,
): Promise<ActionResult<{ status: string }>> {
  try {
    const data = await decide(applicationId, action, note);
    revalidatePath(`/applications/${applicationId}`);
    revalidatePath("/applications");
    revalidatePath("/");
    return { ok: true, data: { status: data.status } };
  } catch (error) {
    return failure(error);
  }
}

export async function saveEditAction(
  applicationId: number,
  bullets: { text: string; resume_anchor: string }[],
  coverLetter: string,
  note: string,
): Promise<ActionResult<{ rounds: number }>> {
  try {
    const data = await saveEdit(applicationId, {
      bullets,
      cover_letter: coverLetter,
      note,
    });
    revalidatePath(`/applications/${applicationId}`);
    revalidatePath("/applications");
    return { ok: true, data: { rounds: data.drafts.length } };
  } catch (error) {
    return failure(error);
  }
}
