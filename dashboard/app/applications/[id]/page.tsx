import Link from "next/link";
import ReviewClient from "@/components/review/ReviewClient";
import { ApiFailure } from "@/components/States";
import { getApplication } from "@/lib/api";
import { STATUS_LABEL } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function ApplicationPage({ params }: PageProps<"/applications/[id]">) {
  const { id } = await params;
  let application;
  try {
    application = await getApplication(Number(id));
  } catch (error) {
    return <ApiFailure error={error} what="application" />;
  }

  const modelRounds = application.drafts.filter((d) => d.authored_by === "writer").length;

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-rule px-6 py-2.5">
        <h1 className="font-semibold tracking-tight">{application.title}</h1>
        <span className="text-muted">
          {application.company}
          {application.location ? ` · ${application.location}` : ""}
        </span>
        <span className="label">{STATUS_LABEL[application.status] ?? application.status}</span>
        <span className="ml-auto flex gap-3 text-2xs text-faint">
          <span>
            {modelRounds} model round{modelRounds === 1 ? "" : "s"}
            {application.first_overall !== null && application.final_overall !== null
              ? ` · ${application.first_overall.toFixed(2)} → ${application.final_overall.toFixed(2)}`
              : ""}
          </span>
          <Link
            href={`/postings/${application.posting_id}`}
            className="text-accent underline underline-offset-4"
          >
            posting
          </Link>
          <a
            href={application.url}
            target="_blank"
            rel="noreferrer"
            className="text-accent underline underline-offset-4"
          >
            apply here ↗
          </a>
        </span>
      </div>
      <ReviewClient application={application} />
    </div>
  );
}
