"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { decideAction } from "@/app/actions";
import { draftToText } from "@/lib/draft-text";
import { STATUS_LABEL } from "@/lib/format";
import type { ApplicationStatus, RoundDetail } from "@/lib/types";

/**
 * Approve means "ready for me to send", and it copies the text so the next
 * thing I do is paste it into the employer's own form. Nothing here submits
 * anything, which is the product boundary, not a missing feature.
 */
export default function ReviewActions({
  applicationId,
  status,
  finalRound,
  onEdit,
}: {
  applicationId: number;
  status: ApplicationStatus;
  finalRound: RoundDetail;
  onEdit: () => void;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const decided = status !== "awaiting_review" && status !== "drafting";

  async function copy() {
    try {
      await navigator.clipboard.writeText(draftToText(finalRound));
      setMessage("Copied. Paste it into the employer's form yourself.");
    } catch {
      setMessage("Approved, but the clipboard was blocked. Select the text and copy it.");
    }
  }

  function run(action: "approve" | "reject" | "submitted") {
    setError(null);
    setMessage(null);
    startTransition(async () => {
      const result = await decideAction(applicationId, action, note);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      if (action === "approve") await copy();
      if (action === "reject") setMessage("Rejected. The reason is recorded.");
      if (action === "submitted") setMessage("Recorded that you submitted it.");
      setRejecting(false);
      setNote("");
      router.refresh();
    });
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label">{STATUS_LABEL[status] ?? status}</span>
        {!decided ? (
          <>
            <button
              onClick={() => run("approve")}
              disabled={pending}
              className="border border-ink bg-ink px-3 py-1 font-medium text-bg disabled:opacity-50"
            >
              Approve &amp; copy
            </button>
            <button
              onClick={onEdit}
              disabled={pending}
              className="border border-rule-strong bg-surface px-3 py-1 disabled:opacity-50"
            >
              Edit
            </button>
            <button
              onClick={() => setRejecting((v) => !v)}
              disabled={pending}
              className="border border-rule-strong bg-surface px-3 py-1 disabled:opacity-50"
            >
              Reject
            </button>
          </>
        ) : null}
        {status === "approved" ? (
          <>
            <button
              onClick={copy}
              className="border border-rule-strong bg-surface px-3 py-1"
            >
              Copy again
            </button>
            <button
              onClick={() => run("submitted")}
              disabled={pending}
              className="border border-rule-strong bg-surface px-3 py-1 disabled:opacity-50"
            >
              I submitted it
            </button>
          </>
        ) : null}
      </div>

      {rejecting ? (
        <div className="flex flex-wrap items-center gap-2">
          <input
            autoFocus
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why? (recorded, and worth having later)"
            className="min-w-0 flex-1 border border-rule-strong bg-surface px-2 py-1"
          />
          <button
            onClick={() => run("reject")}
            disabled={pending}
            className="border border-blocker bg-blocker px-3 py-1 font-medium text-bg disabled:opacity-50"
          >
            Confirm reject
          </button>
        </div>
      ) : null}

      {message ? <p className="text-muted">{message}</p> : null}
      {error ? (
        <p className="border-l-2 border-blocker pl-2 text-blocker">{error}</p>
      ) : null}
    </div>
  );
}
