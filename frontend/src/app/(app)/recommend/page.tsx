"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { PlayerList } from "@/components/PlayerList";
import { createRecommendationAction, type RecommendState } from "./actions";

const initialState: RecommendState = { error: null, result: null };
const MODELS = ["poisson", "naive", "gbm"];

export default function RecommendPage() {
  const [state, formAction, pending] = useActionState(createRecommendationAction, initialState);
  const [season, setSeason] = useState("2026-27");
  const [gameweek, setGameweek] = useState("1");
  const [model, setModel] = useState("poisson");

  // React 19's form-action reset restores controlled <input>s but not <select>s (the browser's
  // native post-submit form.reset() clobbers the <select> DOM node and React never re-syncs it).
  // Force a remount with the current value once the action settles.
  const [selectKey, setSelectKey] = useState(0);
  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    setSelectKey((k) => k + 1);
  }, [state]);

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold mb-1">Recommend</h1>
      <p className="text-sm text-muted mb-6">
        A fresh single-gameweek squad pick from predicted points — the right tool for a
        wildcard or a from-scratch build. For transfers against your existing squad, use Plan.
      </p>

      <form action={formAction} className="card p-6 flex flex-wrap items-end gap-3 mb-6">
        <div>
          <label className="label" htmlFor="season">Season</label>
          <input
            className="input" id="season" name="season" placeholder="2026-27" required
            value={season} onChange={(e) => setSeason(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="gameweek">Gameweek</label>
          <input
            className="input w-24" id="gameweek" name="gameweek" type="number" min={1} required
            value={gameweek} onChange={(e) => setGameweek(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="model">Model</label>
          <select key={selectKey} className="input" id="model" name="model" value={model} onChange={(e) => setModel(e.target.value)}>
            {MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <button className="btn btn-primary" type="submit" disabled={pending}>
          {pending ? "Running…" : "Run recommendation"}
        </button>
      </form>

      {state.error && <p className="text-sm text-danger mb-4">{state.error}</p>}

      {state.result && (
        <div className="card p-6">
          <div className="flex items-baseline justify-between mb-4">
            <div>
              <p className="text-sm text-muted">
                {state.result.season} GW{state.result.gameweek} · {state.result.model} · as of {state.result.as_of_date}
              </p>
              <p className="text-lg font-medium mt-1">{state.result.squad_expected_points.toFixed(1)} expected points</p>
            </div>
            <p className="text-sm text-muted">£{(state.result.squad_cost / 10).toFixed(1)}m</p>
          </div>
          <div className="grid grid-cols-2 gap-6">
            <PlayerList
              title="Starting XI"
              players={state.result.starting_xi}
              captainId={state.result.captain.player_id}
              viceCaptainId={state.result.vice_captain.player_id}
            />
            <PlayerList title="Bench" players={state.result.bench} />
          </div>
        </div>
      )}
    </div>
  );
}
