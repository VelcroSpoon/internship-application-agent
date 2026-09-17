import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-6 my-8 max-w-2xl border-l-2 border-rule-strong pl-4">
      <p className="font-medium">No such page</p>
      <p className="mt-1.5 text-muted">
        <Link href="/" className="text-accent underline underline-offset-4">
          Back to the queue
        </Link>
      </p>
    </div>
  );
}
