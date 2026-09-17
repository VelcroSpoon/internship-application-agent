"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { saveEditAction } from "@/app/actions";
import type { RoundDetail } from "@/lib/types";

/**
 * My edit is saved as a new round attributed to me, not as a change to the
 * model's round. The model's output stays on the record exactly as produced,
 * and the eval numbers never see my writing.
 */
export default function EditForm({
  applicationId,
  base,
  onDone,
  onCancel,
}: {
  applicationId: number;
  base: RoundDetail;
  onDone: () => void;
  onCancel: () => void;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [bullets, setBullets] = useState(base.bullets.map((b) => ({ ...b })));
  const [coverLetter, setCoverLetter] = useState(base.cover_letter);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  function setBulletText(index: number, text: string) {
    setBullets((prev) => prev.map((b, i) => (i === index ? { ...b, text } : b)));
  }

  function save() {
    setError(null);
    const kept = bullets.filter((b) => b.text.trim().length > 0);
    if (kept.length === 0 || coverLetter.trim().length === 0) {
      setError("A draft needs at least one bullet and a cover letter.");
      return;
    }
    startTransition(async () => {
      const result = await saveEditAction(applicationId, kept, coverLetter, note);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      router.refresh();
      onDone();
    });
  }

  return (
    <div className="min-w-0">
      <p className="text-muted">
        Editing round {base.round_index}. Saving adds a new round marked as yours; the
        model&apos;s rounds are left untouched.
      </p>

      <h3 className="label mt-4">Bullets</h3>
      <ul className="mt-2 space-y-3">
        {bullets.map((bullet, i) => (
          <li key={i}>
            <textarea
              value={bullet.text}
              onChange={(e) => setBulletText(i, e.target.value)}
              rows={2}
              className="prose-draft w-full resize-y border border-rule-strong bg-surface px-2 py-1"
            />
            <p className="mt-0.5 text-2xs text-faint">
              anchor: {bullet.resume_anchor || "—"} (kept as the Writer set it)
            </p>
          </li>
        ))}
      </ul>
      <button
        onClick={() => setBullets((prev) => [...prev, { text: "", resume_anchor: "" }])}
        className="mt-2 border border-rule-strong bg-surface px-2 py-0.5 text-2xs"
      >
        + bullet
      </button>

      <h3 className="label rule-t mt-5 pt-3">Cover letter</h3>
      <textarea
        value={coverLetter}
        onChange={(e) => setCoverLetter(e.target.value)}
        rows={18}
        className="prose-draft mt-2 w-full max-w-[80ch] resize-y border border-rule-strong bg-surface px-2 py-1"
      />

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="What did you change? (optional, recorded)"
          className="min-w-0 flex-1 border border-rule-strong bg-surface px-2 py-1"
        />
        <button
          onClick={save}
          disabled={pending}
          className="border border-ink bg-ink px-3 py-1 font-medium text-bg disabled:opacity-50"
        >
          {pending ? "Saving…" : "Save as my revision"}
        </button>
        <button
          onClick={onCancel}
          disabled={pending}
          className="border border-rule-strong bg-surface px-3 py-1 disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
      {error ? (
        <p className="mt-2 border-l-2 border-blocker pl-2 text-blocker">{error}</p>
      ) : null}
    </div>
  );
}
