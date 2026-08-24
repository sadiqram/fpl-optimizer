"use server";

import { apiFetch, ApiError } from "@/lib/backend";
import { getToken } from "@/lib/session";

export type PlayerSummary = { player_id: number; name: string; expected_points: number };
export type RecommendResult = {
  run_id: string;
  season: string;
  gameweek: number;
  model: string;
  as_of_date: string;
  squad_cost: number;
  squad_expected_points: number;
  starting_xi: PlayerSummary[];
  bench: PlayerSummary[];
  captain: PlayerSummary;
  vice_captain: PlayerSummary;
  dropped_players: number;
};

export type RecommendState = { error: string | null; result: RecommendResult | null };

export async function createRecommendationAction(_prev: RecommendState, formData: FormData): Promise<RecommendState> {
  const season = String(formData.get("season") ?? "").trim();
  const gameweek = Number(formData.get("gameweek"));
  const model = String(formData.get("model") ?? "poisson");
  if (!season || !Number.isInteger(gameweek) || gameweek <= 0) {
    return { error: "Season and a valid gameweek are required.", result: null };
  }

  const token = await getToken();
  try {
    const result = await apiFetch<RecommendResult>("/recommendations", {
      method: "POST",
      body: { season, gameweek, model },
      token,
    });
    return { error: null, result };
  } catch (err) {
    if (err instanceof ApiError) return { error: err.message, result: null };
    return { error: "Could not reach the server. Try again.", result: null };
  }
}
