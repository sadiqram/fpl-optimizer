"use client";

import { useActionState } from "react";
import { runBacktestAction, type BacktestState } from "./actions";

const initialState: BacktestState = { error: null, rows: null };
const MODELS = ["poisson", "naive", "gbm"];

export default function BacktestPage() {
  const [state, formAction, pending] = useActionState(runBacktestAction, initialState);

  const totals = state.rows?.reduce(
    (acc, r) => ({
      mae: acc.mae + r.mae,
      recommended: acc.recommended + r.recommended_squad_points,
      hindsight: acc.hindsight + r.hindsight_squad_points,
      regret: acc.regret + r.squad_regret,
    }),
    { mae: 0, recommended: 0, hindsight: 0, regret: 0 }
  );

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-1">Backtest</h1>
      <p className="text-sm text-muted mb-6">
        Replay a historical season gameweek-by-gameweek through the live prediction and
        optimization pipeline (Architecture §4.8).
      </p>

      <form action={formAction} className="card p-6 flex flex-wrap items-end gap-3 mb-6">
        <div>
          <label className="label" htmlFor="season">Season</label>
          <input className="input" id="season" name="season" placeholder="2024-25" required defaultValue="2024-25" />
        </div>
        <div>
          <label className="label" htmlFor="start_gameweek">Start GW</label>
          <input className="input w-24" id="start_gameweek" name="start_gameweek" type="number" min={1} required defaultValue={10} />
        </div>
        <div>
          <label className="label" htmlFor="end_gameweek">End GW</label>
          <input className="input w-24" id="end_gameweek" name="end_gameweek" type="number" min={1} required defaultValue={15} />
        </div>
        <div>
          <label className="label" htmlFor="model">Model</label>
          <select className="input" id="model" name="model" defaultValue="poisson">
            {MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <button className="btn btn-primary" type="submit" disabled={pending}>
          {pending ? "Backtesting…" : "Run backtest"}
        </button>
      </form>

      {state.error && <p className="text-sm text-danger mb-4">{state.error}</p>}

      {state.rows && totals && (
        <div className="card p-6 overflow-x-auto">
          <div className="flex gap-8 mb-4 text-sm">
            <div><span className="text-muted">Mean MAE </span>{(totals.mae / state.rows.length).toFixed(3)}</div>
            <div><span className="text-muted">Recommended pts </span>{totals.recommended.toFixed(0)}</div>
            <div><span className="text-muted">Hindsight-optimal pts </span>{totals.hindsight.toFixed(0)}</div>
            <div><span className="text-muted">Total regret </span>{totals.regret.toFixed(0)}</div>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted border-b border-border">
                <th className="py-2 pr-4">GW</th>
                <th className="py-2 pr-4">n</th>
                <th className="py-2 pr-4">MAE</th>
                <th className="py-2 pr-4">RMSE</th>
                <th className="py-2 pr-4">Recommended</th>
                <th className="py-2 pr-4">Hindsight</th>
                <th className="py-2 pr-4">Regret</th>
              </tr>
            </thead>
            <tbody>
              {state.rows.map((r) => (
                <tr key={r.gameweek} className="border-b border-border/60">
                  <td className="py-1.5 pr-4 tabular-nums">{r.gameweek}</td>
                  <td className="py-1.5 pr-4 tabular-nums">{r.n_predictions_scored}</td>
                  <td className="py-1.5 pr-4 tabular-nums">{r.mae.toFixed(3)}</td>
                  <td className="py-1.5 pr-4 tabular-nums">{r.rmse.toFixed(3)}</td>
                  <td className="py-1.5 pr-4 tabular-nums">{r.recommended_squad_points.toFixed(0)}</td>
                  <td className="py-1.5 pr-4 tabular-nums">{r.hindsight_squad_points.toFixed(0)}</td>
                  <td className="py-1.5 pr-4 tabular-nums">{r.squad_regret.toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
