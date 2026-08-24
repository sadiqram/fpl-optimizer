"use server";

import { revalidatePath } from "next/cache";
import { apiFetch, ApiError } from "@/lib/backend";
import { getToken } from "@/lib/session";

export type SettingsState = { error: string | null; message: string | null };

export async function updateTeamIdAction(_prev: SettingsState, formData: FormData): Promise<SettingsState> {
  const raw = String(formData.get("fpl_team_id") ?? "").trim();
  const fpl_team_id = Number(raw);
  if (!raw || !Number.isInteger(fpl_team_id) || fpl_team_id <= 0) {
    return { error: "Enter a valid FPL team ID.", message: null };
  }
  const token = await getToken();
  try {
    await apiFetch("/users/me", { method: "PATCH", body: { fpl_team_id }, token });
  } catch (err) {
    return { error: err instanceof ApiError ? err.message : "Could not reach the server.", message: null };
  }
  revalidatePath("/settings");
  revalidatePath("/(app)", "layout");
  return { error: null, message: "Team connected." };
}

export async function triggerIngestAction(_prev: SettingsState, _formData: FormData): Promise<SettingsState> {
  const token = await getToken();
  try {
    const summary = await apiFetch<{ players: number; teams: number; fixtures: number }>("/ingest", {
      method: "POST",
      token,
    });
    return { error: null, message: `Synced ${summary.players} players, ${summary.teams} teams, ${summary.fixtures} fixtures.` };
  } catch (err) {
    return { error: err instanceof ApiError ? err.message : "Could not reach the server.", message: null };
  }
}
