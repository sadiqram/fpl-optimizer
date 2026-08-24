"use client";

import { useMemo, useState } from "react";

export type AccuracyRow = {
  season: string;
  gameweek: number;
  model_version: string;
  evaluated_at: string;
  n: number;
  mae: number;
  rmse: number;
};

// Fixed categorical order — assigned by model identity, never cycled (dataviz skill: color
// follows the entity, never its rank). Any model not in this list falls back to the last slot
// rather than inventing a new hue.
const SERIES_ORDER = ["naive", "poisson", "gbm"];
const SERIES_VAR = ["--series-1", "--series-2", "--series-3"];

function colorFor(model: string): string {
  const idx = SERIES_ORDER.indexOf(model);
  return `var(${SERIES_VAR[idx === -1 ? SERIES_VAR.length - 1 : idx]})`;
}

const WIDTH = 640;
const HEIGHT = 260;
const PAD = { top: 16, right: 56, bottom: 28, left: 40 };

export function AccuracyView({ rows }: { rows: AccuracyRow[] }) {
  const seasons = useMemo(() => Array.from(new Set(rows.map((r) => r.season))).sort().reverse(), [rows]);
  const [season, setSeason] = useState(seasons[0] ?? "");
  const seasonRows = rows.filter((r) => r.season === season);

  const models = Array.from(new Set(seasonRows.map((r) => r.model_version))).sort(
    (a, b) => SERIES_ORDER.indexOf(a) - SERIES_ORDER.indexOf(b)
  );
  const gameweeks = Array.from(new Set(seasonRows.map((r) => r.gameweek))).sort((a, b) => a - b);
  const maxMae = Math.max(0.1, ...seasonRows.map((r) => r.mae));

  const x = (gw: number) =>
    PAD.left + (gameweeks.length <= 1 ? 0 : ((gw - gameweeks[0]) / (gameweeks[gameweeks.length - 1] - gameweeks[0])) * (WIDTH - PAD.left - PAD.right));
  const y = (mae: number) => HEIGHT - PAD.bottom - (mae / maxMae) * (HEIGHT - PAD.top - PAD.bottom);

  if (rows.length === 0) {
    return <p className="text-sm text-muted">No evaluations logged yet — run `evaluate` after a gameweek resolves.</p>;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex gap-1">
          {seasons.map((s) => (
            <button
              key={s}
              onClick={() => setSeason(s)}
              className={`text-xs px-2.5 py-1 rounded-full border ${s === season ? "bg-accent text-accent-foreground border-accent" : "border-border text-muted"}`}
            >
              {s}
            </button>
          ))}
        </div>
        <div className="flex gap-4 text-xs">
          {models.map((m) => (
            <span key={m} className="flex items-center gap-1.5 text-muted">
              <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: colorFor(m) }} />
              {m}
            </span>
          ))}
        </div>
      </div>

      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full h-auto" role="img" aria-label="Mean absolute error by gameweek, per model">
        {[0, 0.5, 1].map((f) => (
          <line
            key={f}
            x1={PAD.left} x2={WIDTH - PAD.right}
            y1={y(maxMae * f)} y2={y(maxMae * f)}
            stroke="var(--border)" strokeWidth={1}
          />
        ))}
        {[0, 0.5, 1].map((f) => (
          <text key={f} x={PAD.left - 8} y={y(maxMae * f)} textAnchor="end" dominantBaseline="middle" className="fill-muted" fontSize={10}>
            {(maxMae * f).toFixed(2)}
          </text>
        ))}

        {models.map((model) => {
          const points = seasonRows
            .filter((r) => r.model_version === model)
            .sort((a, b) => a.gameweek - b.gameweek);
          if (points.length === 0) return null;
          const d = points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.gameweek)} ${y(p.mae)}`).join(" ");
          const last = points[points.length - 1];
          return (
            <g key={model}>
              <path d={d} fill="none" stroke={colorFor(model)} strokeWidth={2} strokeLinecap="round" />
              {points.map((p) => (
                <circle key={p.gameweek} cx={x(p.gameweek)} cy={y(p.mae)} r={3} fill="var(--surface)" stroke={colorFor(model)} strokeWidth={2} />
              ))}
              <text x={x(last.gameweek) + 6} y={y(last.mae)} dominantBaseline="middle" fontSize={10} fill={colorFor(model)}>
                {last.mae.toFixed(2)}
              </text>
            </g>
          );
        })}

        {gameweeks.map((gw) => (
          <text key={gw} x={x(gw)} y={HEIGHT - PAD.bottom + 16} textAnchor="middle" className="fill-muted" fontSize={10}>
            GW{gw}
          </text>
        ))}
      </svg>

      <div className="overflow-x-auto mt-6">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted border-b border-border">
              <th className="py-2 pr-4">GW</th>
              <th className="py-2 pr-4">Model</th>
              <th className="py-2 pr-4">n</th>
              <th className="py-2 pr-4">MAE</th>
              <th className="py-2 pr-4">RMSE</th>
            </tr>
          </thead>
          <tbody>
            {seasonRows
              .slice()
              .sort((a, b) => b.gameweek - a.gameweek || a.model_version.localeCompare(b.model_version))
              .map((r) => (
                <tr key={`${r.gameweek}-${r.model_version}`} className="border-b border-border/60">
                  <td className="py-1.5 pr-4 tabular-nums">{r.gameweek}</td>
                  <td className="py-1.5 pr-4">
                    <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: colorFor(r.model_version) }} />
                    {r.model_version}
                  </td>
                  <td className="py-1.5 pr-4 tabular-nums">{r.n}</td>
                  <td className="py-1.5 pr-4 tabular-nums">{r.mae.toFixed(3)}</td>
                  <td className="py-1.5 pr-4 tabular-nums">{r.rmse.toFixed(3)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
