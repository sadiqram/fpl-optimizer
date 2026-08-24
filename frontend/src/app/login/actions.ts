"use server";

import { redirect } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/backend";
import { setToken } from "@/lib/session";

export type FormState = { error: string | null };

export async function loginAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");

  try {
    const { access_token } = await apiFetch<{ access_token: string }>("/auth/login", {
      method: "POST",
      body: { email, password },
    });
    await setToken(access_token);
  } catch (err) {
    if (err instanceof ApiError) return { error: err.message };
    return { error: "Could not reach the server. Try again." };
  }
  redirect("/dashboard");
}
