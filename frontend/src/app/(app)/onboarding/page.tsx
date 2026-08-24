"use client";

import { useActionState } from "react";
import { connectTeamAction, type FormState } from "./actions";

const initialState: FormState = { error: null };

export default function OnboardingPage() {
  const [state, formAction, pending] = useActionState(connectTeamAction, initialState);

  return (
    <div className="max-w-lg">
      <h1 className="text-2xl font-semibold mb-1">Connect your FPL team</h1>
      <p className="text-sm text-muted mb-6">
        Find your team ID in the URL when viewing your team on the official site:{" "}
        <code className="text-xs bg-surface-muted px-1 py-0.5 rounded">
          fantasy.premierleague.com/entry/&lt;team_id&gt;/event/&lt;gw&gt;
        </code>
      </p>
      <form action={formAction} className="card p-6 space-y-4">
        <div>
          <label className="label" htmlFor="fpl_team_id">FPL team ID</label>
          <input className="input" id="fpl_team_id" name="fpl_team_id" type="number" required autoFocus placeholder="1234567" />
        </div>
        {state.error && <p className="text-sm text-danger">{state.error}</p>}
        <button className="btn btn-primary" type="submit" disabled={pending}>
          {pending ? "Connecting…" : "Connect team"}
        </button>
      </form>
    </div>
  );
}
