import Link from "next/link";
import { redirect } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/backend";
import { getToken } from "@/lib/session";
import { logoutAction } from "./actions";

type Me = { id: number; email: string; fpl_team_id: number | null };

const NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/squad", label: "Squad" },
  { href: "/recommend", label: "Recommend" },
  { href: "/plan", label: "Plan" },
  { href: "/backtest", label: "Backtest" },
  { href: "/accuracy", label: "Accuracy" },
  { href: "/settings", label: "Settings" },
];

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const token = await getToken();
  let me: Me;
  try {
    me = await apiFetch<Me>("/auth/me", { token });
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) redirect("/login");
    throw err;
  }

  return (
    <div className="min-h-screen flex">
      <aside className="w-56 shrink-0 border-r border-border bg-surface flex flex-col">
        <div className="px-5 py-5 border-b border-border">
          <p className="font-semibold">fpl-optimizer</p>
          <p className="text-xs text-muted mt-0.5 truncate">{me.email}</p>
        </div>
        <nav className="flex-1 px-2 py-3 space-y-0.5">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="block rounded-lg px-3 py-2 text-sm text-foreground/85 hover:bg-surface-muted transition-colors"
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <form action={logoutAction} className="px-3 py-3 border-t border-border">
          <button className="btn btn-secondary w-full text-sm" type="submit">
            Sign out
          </button>
        </form>
      </aside>
      <main className="flex-1 min-w-0 px-8 py-8">
        {me.fpl_team_id === null && (
          <div className="card mb-6 px-4 py-3 text-sm border-accent/40">
            No FPL team connected yet.{" "}
            <Link className="text-accent underline" href="/onboarding">
              Connect one
            </Link>{" "}
            to sync your squad.
          </div>
        )}
        {children}
      </main>
    </div>
  );
}
