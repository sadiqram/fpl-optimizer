"use server";

import { apiFetch, ApiError } from "@/lib/backend";
import { getToken } from "@/lib/session";

export type BacktestRow = {
  gameweek: number;
  n_predictions_scored: number;
  mae: number;
  rmse: number;
  recommended_squad_points: number;
  hindsight_squad_points: number;
  squad_regret: number;
};

export type BacktestState = { error: string | null; rows: BacktestRow[] | null };

export async function runBacktestAction(_prev: BacktestState, formData: FormData): Promise<BacktestState> {
  const season = String(formData.get("season") ?? "").trim();
  const start_gameweek = Number(formData.get("start_gameweek"));
  const end_gameweek = Number(formData.get("end_gameweek"));
  const model = String(formData.get("model") ?? "poisson");
  if (!season || !start_gameweek || !end_gameweek) {
    return { error: "Season, start and end gameweek are required.", rows: null };
  }

  const token = await getToken();
  try {
    const rows = await apiFetch<BacktestRow[]>("/backtest", {
      method: "POST",
      body: { season, start_gameweek, end_gameweek, model },
      token,
    });
    if (rows.length === 0) return { error: "No gameweeks in that range had recorded outcomes to backtest against.", rows: null };
    return { error: null, rows };
  } catch (err) {
    if (err instanceof ApiError) return { error: err.message, rows: null };
    return { error: "Could not reach the server. Try again.", rows: null };
  }
}
