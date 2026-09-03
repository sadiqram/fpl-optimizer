"use client";

import { useActionState, useState } from "react";
import { syncSquadAction, type SquadPlayer, type SyncState } from "./actions";

const initialState: SyncState = { error: null, summary: null, squad: null };
const POSITION_ORDER = ["GKP", "DEF", "MID", "FWD"];

export default function SquadPage() {
  const [state, formAction, pending] = useActionState(syncSquadAction, initialState);
  const [season, setSeason] = useState("2026-27");

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-semibold mb-1">Squad</h1>
      <p className="text-sm text-muted mb-6">
        Pull your current squad, selling prices, free transfers, and chip status from the FPL API.
      </p>

      <form action={formAction} className="card p-6 flex items-end gap-3 mb-6">
        <div className="flex-1">
          <label className="label" htmlFor="season">Season</label>
          <input
            className="input" id="season" name="season" placeholder="2026-27" required
            value={season} onChange={(e) => setSeason(e.target.value)}
          />
        </div>
        <button className="btn btn-primary" type="submit" disabled={pending}>
          {pending ? "Syncing…" : "Sync from FPL"}
        </button>
      </form>

      {state.error && <p className="text-sm text-danger mb-4">{state.error}</p>}

      {state.summary && (
        <div className="card p-6 grid grid-cols-2 gap-4 text-sm mb-6">
          <Stat label="Gameweek" value={String(state.summary.gameweek)} />
          <Stat label="Players" value={String(state.summary.player_count)} />
          <Stat label="Bank" value={`£${(Number(state.summary.bank ?? 0) / 10).toFixed(1)}m`} />
          <Stat label="Free transfers" value={String(state.summary.free_transfers)} />
          <div className="col-span-2">
            <p className="label mb-1">Chips available</p>
            <p>{(state.summary.chips_available as string[])?.join(", ") || "none"}</p>
          </div>
        </div>
      )}

      {state.squad && state.squad.length > 0 && <SquadView squad={state.squad} />}
    </div>
  );
}

function SquadView({ squad }: { squad: SquadPlayer[] }) {
  const starting = squad.filter((p) => p.is_starting);
  const bench = squad
    .filter((p) => !p.is_starting)
    .sort((a, b) => a.player_id - b.player_id);
  const totalValue = squad.reduce((sum, p) => sum + (p.current_price ?? p.purchase_price), 0);

  return (
    <div className="card p-6">
      <div className="flex items-baseline justify-between mb-4">
        <p className="label">Starting XI</p>
        <p className="text-sm text-muted">Squad value £{(totalValue / 10).toFixed(1)}m</p>
      </div>
      <div className="space-y-4 mb-6">
        {POSITION_ORDER.map((pos) => {
          const players = starting.filter((p) => p.position === pos);
          if (players.length === 0) return null;
          return (
            <div key={pos} className="flex flex-wrap gap-3 justify-center">
              {players.map((p) => <PlayerCard key={p.player_id} player={p} />)}
            </div>
          );
        })}
      </div>

      <p className="label mb-3">Bench</p>
      <div className="flex flex-wrap gap-3">
        {bench.map((p) => <PlayerCard key={p.player_id} player={p} />)}
      </div>
    </div>
  );
}

function PlayerCard({ player }: { player: SquadPlayer }) {
  const price = player.current_price ?? player.purchase_price;
  return (
    <div className="rounded-lg bg-surface-muted px-3 py-2 text-center w-28">
      <p className="text-sm font-medium truncate">
        {player.web_name}
        {player.is_captain === 1 && <span className="ml-1 text-accent font-semibold">(C)</span>}
        {player.is_vice_captain === 1 && <span className="ml-1 text-accent font-semibold">(VC)</span>}
      </p>
      <p className="text-xs text-muted mt-0.5">{player.team_short_name} · {player.position}</p>
      <p className="text-xs text-muted tabular-nums mt-0.5">£{(price / 10).toFixed(1)}m</p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="label mb-1">{label}</p>
      <p className="text-lg font-medium">{value}</p>
    </div>
  );
}
