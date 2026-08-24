import { apiFetch } from "@/lib/backend";
import { getToken } from "@/lib/session";
import { TeamIdForm } from "./TeamIdForm";
import { IngestButton } from "./IngestButton";

type Me = { id: number; email: string; fpl_team_id: number | null };

export default async function SettingsPage() {
  const token = await getToken();
  const me = await apiFetch<Me>("/auth/me", { token });

  return (
    <div className="max-w-xl space-y-6">
      <h1 className="text-2xl font-semibold">Settings</h1>

      <section className="card p-6">
        <p className="font-medium mb-1">FPL team</p>
        <p className="text-sm text-muted mb-4">
          The team `squad`/`plan` sync against. Find it in the URL when viewing your team on
          the official site.
        </p>
        <TeamIdForm currentTeamId={me.fpl_team_id} />
      </section>

      <section className="card p-6">
        <p className="font-medium mb-1">Global data</p>
        <p className="text-sm text-muted mb-4">
          Players, teams, and fixtures refresh automatically every day. Trigger a manual sync
          if you need the latest prices or team news right now.
        </p>
        <IngestButton />
      </section>
    </div>
  );
}
