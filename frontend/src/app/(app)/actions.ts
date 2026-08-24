"use server";

import { redirect } from "next/navigation";
import { clearToken } from "@/lib/session";

export async function logoutAction() {
  await clearToken();
  redirect("/login");
}
