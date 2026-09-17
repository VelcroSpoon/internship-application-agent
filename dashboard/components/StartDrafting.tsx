"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";
import { startDraftingAction } from "@/app/actions";
import { STOP_REASON_LABEL } from "@/lib/format";

/**
 * The loop endpoint blocks for the length of a real Writer/Critic run, which
 * is minutes. There is no progress to report from a blocking call, so the
 * honest thing is an elapsed counter and a note about what it is waiting for.
 */
export default function StartDrafting({ postingId }: { postingId: number }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const started = useRef(0);

  useEffect(() => {
    if (!pending) return;
    started.current = Date.now();
    setElapsed(0);
    const id = setInterval(
      () => setElapsed(Math.floor((Date.now() - started.current) / 1000)),
      1000,
    );
    return () => clearInterval(id);
  }, [pending]);

  function start() {
    setError(null);
    startTransition(async () => {
      const result = await startDraftingAction(postingId);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      const { application_id, stopped_because, stopped_detail } = result.data;
      if (!application_id) {
        setError(stopped_detail ?? stopped_because);
        return;
      }
      if (stopped_detail) setError(stopped_detail);
      router.push(`/applications/${application_id}`);
    });
  }

  return (
    <div>
      <button
        onClick={start}
        disabled={pending}
        className="border border-ink bg-ink px-3 py-1.5 font-medium text-bg disabled:border-rule-strong disabled:bg-rule disabled:text-muted"
      >
        {pending ? `Drafting… ${elapsed}s` : "Start drafting"}
      </button>
      {pending ? (
        <p className="mt-2 max-w-prose text-muted">
          Writing, critiquing and revising, up to three rounds. This holds the request
          open for a few minutes. Every round is saved as it finishes, so closing this
          tab loses the redirect, not the work.
        </p>
      ) : null}
      {error ? (
        <p className="mt-2 max-w-prose border-l-2 border-blocker pl-3 text-blocker">
          {STOP_REASON_LABEL[error] ?? error}
        </p>
      ) : null}
    </div>
  );
}
