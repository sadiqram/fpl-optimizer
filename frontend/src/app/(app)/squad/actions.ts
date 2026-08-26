"use server";

import { revalidatePath } from "next/cache";
import { apiFetch, ApiError } from "@/lib/backend";
import { getToken } from "@/lib/session";

export type SquadPlayer = {
  player_id: number;
  web_name: string;
  position: string;
  team_short_name: string;
  team_name: string;
  is_starting: number;
  is_captain: number;
  is_vice_captain: number;
  purchase_price: number;
  current_price: number | null;
};

export type SyncState = {
  error: string | null;
  summary: Record<string, unknown> | null;
  squad: SquadPlayer[] | null;
};

export async function syncSquadAction(_prev: SyncState, formData: FormData): Promise<SyncState> {
  const season = String(formData.get("season") ?? "").trim();
  if (!season) return { error: "Season is required (e.g. 2025-26).", summary: null, squad: null };

  const token = await getToken();
  try {
    const summary = await apiFetch<Record<string, unknown>>("/squad/sync", {
      method: "POST",
      body: { season },
      token,
    });
    const gameweek = Number(summary.gameweek);
    const detail = await apiFetch<{ owned_squad: SquadPlayer[] }>(`/squad/${season}/${gameweek}`, { token });
    revalidatePath("/squad");
    revalidatePath("/dashboard");
    return { error: null, summary, squad: detail.owned_squad };
  } catch (err) {
    if (err instanceof ApiError) return { error: err.message, summary: null, squad: null };
    return { error: "Could not reach the server. Try again.", summary: null, squad: null };
  }
}
