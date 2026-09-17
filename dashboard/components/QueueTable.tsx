"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ageDays, ageLabel, sourceLabel } from "@/lib/format";
import type { QueueItem } from "@/lib/types";

type SortKey = "fit" | "age";

/**
 * The view I open most, so it has to read in about ten seconds: score first,
 * reason next to it, everything else quiet. Sorting is client-side because the
 * whole queue is already here and a round trip per click would be slower than
 * reading it.
 */
export default function QueueTable({ items }: { items: QueueItem[] }) {
  const router = useRouter();
  const [key, setKey] = useState<SortKey>("fit");
  const [asc, setAsc] = useState(false);

  const sorted = useMemo(() => {
    const dir = asc ? 1 : -1;
    return [...items].sort((a, b) =>
      key === "fit"
        ? (a.fit_score - b.fit_score) * dir
        : (ageDays(a) - ageDays(b)) * dir,
    );
  }, [items, key, asc]);

  function sortBy(next: SortKey) {
    if (next === key) setAsc(!asc);
    else {
      setKey(next);
      setAsc(next === "age"); // newest-first and best-first are both "desc"
    }
  }

  const arrow = (col: SortKey) => (key === col ? (asc ? " ↑" : " ↓") : "");

  return (
    <table className="w-full border-collapse text-[13px]">
      <thead>
        <tr className="border-b border-rule-strong text-left">
          <Th className="w-[3.5rem] text-right">
            <button onClick={() => sortBy("fit")} className="hover:text-ink">
              fit{arrow("fit")}
            </button>
          </Th>
          <Th className="w-[13rem]">company</Th>
          <Th className="w-[26rem]">title</Th>
          <Th>why</Th>
          <Th className="w-[5rem] text-right">
            <button onClick={() => sortBy("age")} className="hover:text-ink">
              age{arrow("age")}
            </button>
          </Th>
          <Th className="w-[7rem]">source</Th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((item) => (
          <tr
            key={item.posting_id}
            onClick={() => router.push(`/postings/${item.posting_id}`)}
            className="cursor-pointer border-b border-rule align-baseline hover:bg-accent-soft"
          >
            <td className="py-1.5 pr-3 text-right font-mono font-semibold">
              {item.fit_score.toFixed(0)}
            </td>
            <td className="py-1.5 pr-3 truncate">{item.company}</td>
            <td className="py-1.5 pr-3">
              <Link
                href={`/postings/${item.posting_id}`}
                className="hover:underline underline-offset-4"
                onClick={(e) => e.stopPropagation()}
              >
                {item.title}
              </Link>
              {item.location ? (
                <span className="text-faint"> · {item.location}</span>
              ) : null}
            </td>
            <td className="py-1.5 pr-3 text-muted">{item.reason}</td>
            <td className="py-1.5 pr-3 text-right text-muted">{ageLabel(item)}</td>
            <td className="py-1.5 text-faint">{sourceLabel(item.source)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Th({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <th className={`label pr-3 pb-1.5 font-semibold ${className}`}>{children}</th>;
}
