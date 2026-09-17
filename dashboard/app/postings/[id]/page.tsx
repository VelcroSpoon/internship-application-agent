import Link from "next/link";
import StartDrafting from "@/components/StartDrafting";
import { ApiFailure } from "@/components/States";
import { getPosting } from "@/lib/api";
import { STATUS_LABEL, ageLabel, shortDate, sourceLabel } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function PostingPage({ params }: PageProps<"/postings/[id]">) {
  const { id } = await params;
  let posting;
  try {
    posting = await getPosting(Number(id));
  } catch (error) {
    return <ApiFailure error={error} what="posting" />;
  }

  const { screening, application } = posting;

  return (
    <div className="grid grid-cols-1 gap-8 px-6 py-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight">{posting.title}</h1>
        <p className="mt-0.5 text-muted">
          {posting.company}
          {posting.location ? ` · ${posting.location}` : ""} · posted{" "}
          {shortDate(posting.posted_at ?? posting.first_seen_at)} ({ageLabel(posting)} ago)
        </p>
        <p className="mt-1 flex gap-3 text-2xs text-faint">
          <span>{sourceLabel(posting.source)}</span>
          <a
            href={posting.url}
            target="_blank"
            rel="noreferrer"
            className="text-accent underline underline-offset-4"
          >
            original posting ↗
          </a>
        </p>

        <h2 className="label rule-t mt-6 pt-3">Posting text</h2>
        {posting.description ? (
          <div className="prose-draft mt-2 max-w-[75ch] text-ink">{posting.description}</div>
        ) : (
          <p className="mt-2 text-faint">This board returned no description.</p>
        )}
      </div>

      <aside className="min-w-0 space-y-6 xl:border-l xl:border-rule xl:pl-6">
        <section>
          <h2 className="label">Screening</h2>
          {screening ? (
            <>
              <p className="mt-1.5 flex items-baseline gap-2">
                <span className="font-mono text-2xl font-semibold">
                  {screening.fit_score.toFixed(0)}
                </span>
                <span className="text-muted">
                  by {screening.model}
                  {screening.is_internship === false ? " · not an internship" : ""}
                </span>
              </p>
              <p className="mt-1 text-muted">{screening.reason}</p>
              {screening.disqualifiers.length > 0 ? (
                <p className="mt-2 border-l-2 border-blocker pl-2 text-blocker">
                  Disqualified: {screening.disqualifiers.join(", ")}
                </p>
              ) : null}
              <Requirements label="Matched" items={screening.matched_requirements} />
              <Requirements label="Missing" items={screening.missing_requirements} />
            </>
          ) : (
            <p className="mt-1.5 text-faint">Not screened yet.</p>
          )}
        </section>

        <section className="rule-t pt-4">
          <h2 className="label">Application</h2>
          {application ? (
            <p className="mt-1.5">
              <Link
                href={`/applications/${application.application_id}`}
                className="text-accent underline underline-offset-4"
              >
                Open draft review
              </Link>
              <span className="text-muted">
                {" "}
                · {STATUS_LABEL[application.status] ?? application.status}
              </span>
            </p>
          ) : (
            <div className="mt-2">
              <StartDrafting postingId={posting.posting_id} />
            </div>
          )}
        </section>
      </aside>
    </div>
  );
}

function Requirements({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="label">{label}</p>
      <ul className="mt-1 space-y-0.5 text-muted">
        {items.map((item, i) => (
          <li key={i} className="flex gap-1.5">
            <span className="text-faint">·</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
