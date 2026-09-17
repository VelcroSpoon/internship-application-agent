import Link from "next/link";
import { ApiFailure, Command, Notice } from "@/components/States";
import { getApplications } from "@/lib/api";
import { STATUS_LABEL, shortDate } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function ApplicationsPage() {
  let applications;
  try {
    applications = await getApplications();
  } catch (error) {
    return <ApiFailure error={error} what="applications" />;
  }

  if (applications.length === 0) {
    return (
      <Notice title="No applications yet">
        <p>Pick something from the queue and start drafting. Nothing is drafted on a timer.</p>
        <Command>uv run python -m internship_agent loop run --posting N</Command>
      </Notice>
    );
  }

  return (
    <div className="px-6 py-4">
      <div className="mb-3 flex items-baseline gap-3">
        <h1 className="text-base font-semibold tracking-tight">Applications</h1>
        <span className="text-muted">{applications.length} in progress or decided</span>
      </div>
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="border-b border-rule-strong text-left">
            <th className="label w-[9rem] pb-1.5">status</th>
            <th className="label w-[13rem] pb-1.5">company</th>
            <th className="label pb-1.5">title</th>
            <th className="label w-[5rem] pb-1.5 text-right">rounds</th>
            <th className="label w-[9rem] pb-1.5 text-right">first → final</th>
            <th className="label w-[7rem] pb-1.5 text-right">updated</th>
          </tr>
        </thead>
        <tbody>
          {applications.map((app) => (
            <tr key={app.application_id} className="border-b border-rule hover:bg-accent-soft">
              <td className="py-1.5 pr-3">
                <span className={app.status === "awaiting_review" ? "font-medium" : "text-muted"}>
                  {STATUS_LABEL[app.status] ?? app.status}
                </span>
                {app.final_blockers ? (
                  <span className="ml-1.5 text-2xs font-semibold text-blocker">
                    {app.final_blockers} blocker{app.final_blockers === 1 ? "" : "s"}
                  </span>
                ) : null}
              </td>
              <td className="py-1.5 pr-3 truncate">{app.company}</td>
              <td className="py-1.5 pr-3">
                <Link
                  href={`/applications/${app.application_id}`}
                  className="hover:underline underline-offset-4"
                >
                  {app.title}
                </Link>
              </td>
              <td className="py-1.5 pr-3 text-right font-mono">{app.rounds}</td>
              <td className="py-1.5 pr-3 text-right font-mono">
                {app.first_overall !== null && app.final_overall !== null
                  ? `${app.first_overall.toFixed(2)} → ${app.final_overall.toFixed(2)}`
                  : "—"}
              </td>
              <td className="py-1.5 text-right text-muted">{shortDate(app.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
