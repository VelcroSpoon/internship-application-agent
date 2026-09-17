import QueueTable from "@/components/QueueTable";
import { ApiFailure, Command, Notice } from "@/components/States";
import { getQueue } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function QueuePage() {
  let items;
  try {
    items = await getQueue();
  } catch (error) {
    return <ApiFailure error={error} what="queue" />;
  }

  if (items.length === 0) {
    return (
      <Notice title="Nothing in the queue">
        <p>
          Either nothing has been screened yet, or nothing scored above the threshold in
          your criteria file. Both are normal. The nightly run adds to this on its own.
        </p>
        <Command>uv run python -m internship_agent screener run</Command>
      </Notice>
    );
  }

  return (
    <div className="px-6 py-4">
      <div className="mb-3 flex items-baseline gap-3">
        <h1 className="text-base font-semibold tracking-tight">Queue</h1>
        <span className="text-muted">
          {items.length} posting{items.length === 1 ? "" : "s"} above threshold, none applied to
        </span>
      </div>
      <QueueTable items={items} />
    </div>
  );
}
