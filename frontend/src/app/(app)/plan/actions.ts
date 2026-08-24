"use server";

import { apiFetch, ApiError } from "@/lib/backend";
import { getToken } from "@/lib/session";

export type PlayerSummary = { player_id: number; name: string; expected_points: number | null };
export type ChipScenario = { delta: number; reasoning: string };
export type PlanResult = {
  run_id: string;
  season: string;
  gameweek: number;
  model: string;
  as_of_date: string;
  preset: Record<string, number>;
  transfers_in: string[];
  transfers_out: string[];
  hits_taken: number;
  hit_cost: number;
  expected_points_gain: number;
  starting_xi: PlayerSummary[];
  bench: PlayerSummary[];
  captain: PlayerSummary;
  vice_captain: PlayerSummary;
  chip_scenarios: Record<string, ChipScenario>;
};

export type PlanState = { error: string | null; result: PlanResult | null };

export async function createPlanAction(_prev: PlanState, formData: FormData): Promise<PlanState> {
  const season = String(formData.get("season") ?? "").trim();
  const gameweek = Number(formData.get("gameweek"));
  const model = String(formData.get("model") ?? "poisson");
  const preset = String(formData.get("preset") ?? "") || undefined;
  if (!season || !Number.isInteger(gameweek) || gameweek <= 0) {
    return { error: "Season and a valid gameweek are required.", result: null };
  }

  const token = await getToken();
  try {
    const result = await apiFetch<PlanResult>("/plans", {
      method: "POST",
      body: { season, gameweek, model, preset },
      token,
    });
    return { error: null, result };
  } catch (err) {
    if (err instanceof ApiError) return { error: err.message, result: null };
    return { error: "Could not reach the server. Try again.", result: null };
  }
}
