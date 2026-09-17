"use client";

import { useMemo, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { DIMENSIONS, type Dimension, type ScoreByDimension, type ScoreByRound } from "@/lib/types";

/**
 * Score against revision round. The counts are drawn as bars on their own
 * axis rather than tucked into a caption: with a handful of applications the
 * later rounds have far less data than round 0, and a line that thins out
 * without saying so is the misleading version of this chart.
 */

const SERIES_COLOR: Record<Dimension | "overall", string> = {
  overall: "#17160f",
  grounding: "#a8261c",
  coverage: "#1b4f8a",
  specificity: "#1f6b3d",
  density: "#8a5a10",
  voice: "#6c3a8a",
};

interface Row {
  round: number;
  overall: number | null;
  n: number;
  grounding?: number;
  coverage?: number;
  specificity?: number;
  density?: number;
  voice?: number;
}

export default function ScoreChart({
  byRound,
  byDimension,
}: {
  byRound: ScoreByRound[];
  byDimension: ScoreByDimension[];
}) {
  const [shown, setShown] = useState<Set<Dimension>>(new Set());

  const rows = useMemo<Row[]>(() => {
    const map = new Map<number, Row>();
    for (const entry of byRound) {
      map.set(entry.round_index, {
        round: entry.round_index,
        overall: entry.mean_overall,
        n: entry.applications,
      });
    }
    for (const entry of byDimension) {
      const row = map.get(entry.round_index);
      if (row) row[entry.dimension] = entry.mean_score;
    }
    return [...map.values()].sort((a, b) => a.round - b.round);
  }, [byRound, byDimension]);

  const maxN = Math.max(1, ...rows.map((r) => r.n));
  const thin = rows.filter((r) => r.n < 3);

  function toggle(dimension: Dimension) {
    setShown((prev) => {
      const next = new Set(prev);
      if (next.has(dimension)) next.delete(dimension);
      else next.add(dimension);
      return next;
    });
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="label">Show dimensions</span>
        {DIMENSIONS.map((dimension) => (
          <label key={dimension} className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={shown.has(dimension)}
              onChange={() => toggle(dimension)}
            />
            <span style={{ color: SERIES_COLOR[dimension] }}>{dimension}</span>
          </label>
        ))}
      </div>

      <div className="h-[26rem] w-full border border-rule bg-surface p-3">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 24, left: 0 }}>
            <CartesianGrid stroke="#e4e0d6" vertical={false} />
            <XAxis
              dataKey="round"
              tick={{ fontSize: 12, fill: "#6a675e" }}
              stroke="#cbc6b8"
              label={{
                value: "revision round",
                position: "insideBottom",
                offset: -14,
                fontSize: 11,
                fill: "#96938a",
              }}
            />
            <YAxis
              yAxisId="score"
              domain={[1, 5]}
              ticks={[1, 2, 3, 4, 5]}
              tick={{ fontSize: 12, fill: "#6a675e" }}
              stroke="#cbc6b8"
              width={32}
            />
            <YAxis
              yAxisId="count"
              orientation="right"
              domain={[0, maxN * 4]}
              allowDecimals={false}
              tick={{ fontSize: 11, fill: "#96938a" }}
              stroke="#e4e0d6"
              width={28}
            />
            <Tooltip
              contentStyle={{
                border: "1px solid #cbc6b8",
                borderRadius: 0,
                fontSize: 12,
                background: "#fff",
              }}
              formatter={(value, name) => {
                if (name === "applications") return [String(value ?? 0), "applications scored"];
                return [typeof value === "number" ? value.toFixed(2) : String(value ?? "—"), String(name)];
              }}
              labelFormatter={(round) => `round ${round}`}
            />
            <Legend
              verticalAlign="top"
              align="right"
              height={22}
              wrapperStyle={{ fontSize: 12 }}
            />
            <Bar
              yAxisId="count"
              dataKey="n"
              name="applications"
              fill="#e4e0d6"
              barSize={28}
              isAnimationActive={false}
            />
            <Line
              yAxisId="score"
              type="monotone"
              dataKey="overall"
              name="overall"
              stroke={SERIES_COLOR.overall}
              strokeWidth={2}
              dot={{ r: 3 }}
              isAnimationActive={false}
              connectNulls
            />
            {DIMENSIONS.filter((d) => shown.has(d)).map((dimension) => (
              <Line
                key={dimension}
                yAxisId="score"
                type="monotone"
                dataKey={dimension}
                name={dimension}
                stroke={SERIES_COLOR[dimension]}
                strokeWidth={1.25}
                strokeDasharray="3 2"
                dot={{ r: 2 }}
                isAnimationActive={false}
                connectNulls
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <table className="mt-4 border-collapse text-[13px]">
        <thead>
          <tr className="border-b border-rule-strong text-left">
            <th className="label pr-6 pb-1">round</th>
            <th className="label pr-6 pb-1 text-right">applications</th>
            <th className="label pr-6 pb-1 text-right">mean overall</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.round} className="border-b border-rule">
              <td className="py-1 pr-6">{row.round}</td>
              <td className="py-1 pr-6 text-right font-mono">{row.n}</td>
              <td className="py-1 pr-6 text-right font-mono">
                {row.overall?.toFixed(2) ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {thin.length > 0 ? (
        <p className="mt-3 max-w-prose border-l-2 border-major pl-3 text-muted">
          Round{thin.length === 1 ? "" : "s"} {thin.map((r) => r.round).join(", ")}{" "}
          {thin.length === 1 ? "has" : "have"} fewer than three applications behind{" "}
          {thin.length === 1 ? "it" : "them"}. Later rounds only exist for drafts the loop
          kept working on, so the tail is both noisy and selected for drafts that started
          badly. Read the trend as a hint, not a result.
        </p>
      ) : null}
    </div>
  );
}
