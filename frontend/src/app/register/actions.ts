"use server";

import { redirect } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/backend";
import { setToken } from "@/lib/session";

export type FormState = { error: string | null };

export async function registerAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");

  if (password.length < 8) {
    return { error: "Password must be at least 8 characters." };
  }

  try {
    const { access_token } = await apiFetch<{ access_token: string }>("/auth/register", {
      method: "POST",
      body: { email, password },
    });
    await setToken(access_token);
  } catch (err) {
    if (err instanceof ApiError) return { error: err.message };
    return { error: "Could not reach the server. Try again." };
  }
  redirect("/onboarding");
}
