"use server";

import { redirect } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/backend";
import { getToken } from "@/lib/session";

export type FormState = { error: string | null };

export async function connectTeamAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const raw = String(formData.get("fpl_team_id") ?? "").trim();
  const fpl_team_id = Number(raw);
  if (!raw || !Number.isInteger(fpl_team_id) || fpl_team_id <= 0) {
    return { error: "Enter a valid FPL team ID (the number in your team's URL)." };
  }

  const token = await getToken();
  try {
    await apiFetch("/users/me", { method: "PATCH", body: { fpl_team_id }, token });
  } catch (err) {
    if (err instanceof ApiError) return { error: err.message };
    return { error: "Could not reach the server. Try again." };
  }
  redirect("/squad");
}
