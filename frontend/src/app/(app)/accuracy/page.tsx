import { apiFetch } from "@/lib/backend";
import { getToken } from "@/lib/session";
import { AccuracyView, type AccuracyRow } from "@/components/AccuracyChart";

export default async function AccuracyPage() {
  const token = await getToken();
  const rows = await apiFetch<AccuracyRow[]>("/accuracy-log", { token });

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-1">Accuracy</h1>
      <p className="text-sm text-muted mb-6">
        Predicted vs. actual mean absolute error by gameweek, per model — the running log
        `evaluate` builds up over a season (Architecture §4.8).
      </p>
      <div className="card p-6">
        <AccuracyView rows={rows} />
      </div>
    </div>
  );
}
