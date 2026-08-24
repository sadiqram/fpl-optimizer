"use client";

import { useActionState } from "react";
import { triggerIngestAction, type SettingsState } from "./actions";

const initialState: SettingsState = { error: null, message: null };

export function IngestButton() {
  const [state, formAction, pending] = useActionState(triggerIngestAction, initialState);

  return (
    <form action={formAction} className="flex items-center gap-3">
      <button className="btn btn-secondary" type="submit" disabled={pending}>
        {pending ? "Syncing…" : "Sync now"}
      </button>
      {state.error && <p className="text-sm text-danger">{state.error}</p>}
      {state.message && <p className="text-sm text-success">{state.message}</p>}
    </form>
  );
}
