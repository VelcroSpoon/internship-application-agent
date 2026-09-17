import Link from "next/link";
import { ApiError } from "@/lib/api";

/**
 * Empty is not an error. An empty queue means the screener found nothing worth
 * my time, which is the system working. These say so plainly and, where there
 * is a next step, name the command that takes it.
 */

export function Notice({
  title,
  children,
  tone = "neutral",
}: {
  title: string;
  children?: React.ReactNode;
  tone?: "neutral" | "bad";
}) {
  const accent = tone === "bad" ? "border-blocker text-blocker" : "border-rule-strong";
  return (
    <div className={`mx-6 my-8 max-w-2xl border-l-2 ${accent} pl-4`}>
      <p className="font-medium">{title}</p>
      {children ? <div className="mt-1.5 text-muted leading-relaxed">{children}</div> : null}
    </div>
  );
}

export function Command({ children }: { children: React.ReactNode }) {
  return (
    <code className="mt-2 block w-fit bg-surface border border-rule px-2 py-1 font-mono text-[13px]">
      {children}
    </code>
  );
}

/** Renders the right thing for a failed API call, or rethrows what it cannot explain. */
export function ApiFailure({ error, what }: { error: unknown; what: string }) {
  if (!(error instanceof ApiError)) throw error;

  if (error.kind === "unreachable") {
    return (
      <Notice title="The agent service is not running" tone="bad">
        <p>This dashboard reads everything from the Python service. Start it, then reload.</p>
        <Command>uv run python -m internship_agent serve</Command>
      </Notice>
    );
  }
  if (error.kind === "not_found") {
    return (
      <Notice title={`No such ${what}`}>
        <p>{error.message}</p>
        <p className="mt-2">
          <Link href="/" className="text-accent underline underline-offset-4">
            Back to the queue
          </Link>
        </p>
      </Notice>
    );
  }
  return (
    <Notice title={`Could not load the ${what}`} tone="bad">
      <p>{error.message}</p>
    </Notice>
  );
}
