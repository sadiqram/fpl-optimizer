"use client";

import { useActionState } from "react";
import { PlayerList } from "@/components/PlayerList";
import { createPlanAction, type PlanState } from "./actions";

const initialState: PlanState = { error: null, result: null };
const MODELS = ["poisson", "naive", "gbm"];
const PRESETS = ["balanced", "safe", "aggressive", "value_conscious"];
const CHIP_LABELS: Record<string, string> = {
  wildcard: "Wildcard", freehit: "Free Hit", bboost: "Bench Boost", "3xc": "Triple Captain",
};

export default function PlanPage() {
  const [state, formAction, pending] = useActionState(createPlanAction, initialState);

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold mb-1">Plan</h1>
      <p className="text-sm text-muted mb-6">
        Rolling-horizon transfer and lineup plan against your synced squad. Requires syncing
        your squad first.
      </p>

      <form action={formAction} className="card p-6 flex flex-wrap items-end gap-3 mb-6">
        <div>
          <label className="label" htmlFor="season">Season</label>
          <input className="input" id="season" name="season" placeholder="2026-27" required defaultValue="2026-27" />
        </div>
        <div>
          <label className="label" htmlFor="gameweek">Gameweek</label>
          <input className="input w-24" id="gameweek" name="gameweek" type="number" min={1} required defaultValue={1} />
        </div>
        <div>
          <label className="label" htmlFor="model">Model</label>
          <select className="input" id="model" name="model" defaultValue="poisson">
            {MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="preset">Preset</label>
          <select className="input" id="preset" name="preset" defaultValue="balanced">
            {PRESETS.map((p) => <option key={p} value={p}>{p.replace("_", "-")}</option>)}
          </select>
        </div>
        <button className="btn btn-primary" type="submit" disabled={pending}>
          {pending ? "Planning…" : "Run plan"}
        </button>
      </form>

      {state.error && <p className="text-sm text-danger mb-4">{state.error}</p>}

      {state.result && (
        <div className="space-y-6">
          <div className="card p-6">
            <p className="text-sm text-muted mb-3">
              {state.result.season} GW{state.result.gameweek} · {state.result.model} · as of {state.result.as_of_date}
            </p>
            {state.result.transfers_in.length > 0 ? (
              <div className="space-y-1 text-sm">
                {state.result.transfers_out.map((out, i) => (
                  <p key={i}>
                    OUT <span className="font-medium">{out}</span> → IN{" "}
                    <span className="font-medium">{state.result!.transfers_in[i]}</span>
                  </p>
                ))}
                <p className="text-muted mt-2">
                  {state.result.hits_taken} hit(s), −{state.result.hit_cost} pts · net gain{" "}
                  {state.result.expected_points_gain >= 0 ? "+" : ""}
                  {state.result.expected_points_gain.toFixed(1)} pts
                </p>
              </div>
            ) : (
              <p className="text-sm">Hold — no transfer clears the hit threshold this week.</p>
            )}
          </div>

          <div className="card p-6 grid grid-cols-2 gap-6">
            <PlayerList
              title="Starting XI"
              players={state.result.starting_xi}
              captainId={state.result.captain.player_id}
              viceCaptainId={state.result.vice_captain.player_id}
            />
            <PlayerList title="Bench" players={state.result.bench} />
          </div>

          {Object.keys(state.result.chip_scenarios).length > 0 && (
            <div className="card p-6">
              <p className="label mb-3">Chip opportunities</p>
              <ul className="space-y-2 text-sm">
                {Object.entries(state.result.chip_scenarios).map(([chip, scenario]) => (
                  <li key={chip} className="flex justify-between">
                    <span>{CHIP_LABELS[chip] ?? chip}</span>
                    <span className="text-muted">
                      {scenario.delta >= 0 ? "+" : ""}
                      {scenario.delta.toFixed(1)} pts — {scenario.reasoning}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
