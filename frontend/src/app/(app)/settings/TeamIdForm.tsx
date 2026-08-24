"use client";

import { useActionState } from "react";
import { updateTeamIdAction, type SettingsState } from "./actions";

const initialState: SettingsState = { error: null, message: null };

export function TeamIdForm({ currentTeamId }: { currentTeamId: number | null }) {
  const [state, formAction, pending] = useActionState(updateTeamIdAction, initialState);

  return (
    <form action={formAction} className="flex items-end gap-3">
      <div>
        <label className="label" htmlFor="fpl_team_id">FPL team ID</label>
        <input
          className="input w-48"
          id="fpl_team_id"
          name="fpl_team_id"
          type="number"
          required
          defaultValue={currentTeamId ?? undefined}
        />
      </div>
      <button className="btn btn-primary" type="submit" disabled={pending}>
        {pending ? "Saving…" : "Save"}
      </button>
      {state.error && <p className="text-sm text-danger">{state.error}</p>}
      {state.message && <p className="text-sm text-success">{state.message}</p>}
    </form>
  );
}
