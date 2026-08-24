import "server-only";
import { cookies } from "next/headers";

// The backend's JWT, held only as an httpOnly cookie set by a Server Function
// (auth/actions.ts) — never sent to client JS, never stored in localStorage. Every
// server-rendered page and every mutating Server Function reads it via getToken().
export const SESSION_COOKIE = "fpl_session";

export async function getToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}

export async function setToken(token: string): Promise<void> {
  const store = await cookies();
  store.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 14, // matches the backend's ACCESS_TOKEN_EXPIRE_MINUTES (api/auth.py)
  });
}

export async function clearToken(): Promise<void> {
  const store = await cookies();
  store.delete(SESSION_COOKIE);
}
