"use client";

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="mx-6 my-8 max-w-2xl border-l-2 border-blocker pl-4">
      <p className="font-medium text-blocker">Something in this page failed to render</p>
      <p className="mt-1.5 text-muted">{error.message}</p>
      <button
        onClick={reset}
        className="mt-3 border border-rule-strong bg-surface px-3 py-1"
      >
        Try again
      </button>
    </div>
  );
}
