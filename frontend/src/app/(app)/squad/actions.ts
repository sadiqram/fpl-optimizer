"use server";

import { revalidatePath } from "next/cache";
import { apiFetch, ApiError } from "@/lib/backend";
import { getToken } from "@/lib/session";

export type SyncState = { error: string | null; summary: Record<string, unknown> | null };

export async function syncSquadAction(_prev: SyncState, formData: FormData): Promise<SyncState> {
  const season = String(formData.get("season") ?? "").trim();
  if (!season) return { error: "Season is required (e.g. 2025-26).", summary: null };

  const token = await getToken();
  try {
    const summary = await apiFetch<Record<string, unknown>>("/squad/sync", {
      method: "POST",
      body: { season },
      token,
    });
    revalidatePath("/squad");
    revalidatePath("/dashboard");
    return { error: null, summary };
  } catch (err) {
    if (err instanceof ApiError) return { error: err.message, summary: null };
    return { error: "Could not reach the server. Try again.", summary: null };
  }
}
