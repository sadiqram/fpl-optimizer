"use client";

import { useActionState } from "react";
import { syncSquadAction, type SyncState } from "./actions";

const initialState: SyncState = { error: null, summary: null };

export default function SquadPage() {
  const [state, formAction, pending] = useActionState(syncSquadAction, initialState);

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold mb-1">Squad</h1>
      <p className="text-sm text-muted mb-6">
        Pull your current squad, selling prices, free transfers, and chip status from the FPL API.
      </p>

      <form action={formAction} className="card p-6 flex items-end gap-3 mb-6">
        <div className="flex-1">
          <label className="label" htmlFor="season">Season</label>
          <input className="input" id="season" name="season" placeholder="2026-27" required defaultValue="2026-27" />
        </div>
        <button className="btn btn-primary" type="submit" disabled={pending}>
          {pending ? "Syncing…" : "Sync from FPL"}
        </button>
      </form>

      {state.error && <p className="text-sm text-danger mb-4">{state.error}</p>}

      {state.summary && (
        <div className="card p-6 grid grid-cols-2 gap-4 text-sm">
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
