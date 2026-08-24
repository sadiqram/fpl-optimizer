import Link from "next/link";
import { apiFetch } from "@/lib/backend";
import { getToken } from "@/lib/session";

type RecommendationRow = { run_id: string; season: string; gameweek: number; created_at: string };
type PlanRow = { run_id: string; season: string; gameweek: number; created_at: string };

export default async function DashboardPage() {
  const token = await getToken();
  const [recommendations, plans] = await Promise.all([
    apiFetch<RecommendationRow[]>("/recommendations", { token }),
    apiFetch<PlanRow[]>("/plans", { token }),
  ]);

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-6">Dashboard</h1>

      <div className="grid grid-cols-2 gap-6 mb-8">
        <QuickLink href="/squad" title="Sync squad" description="Pull your latest squad, bank, and free transfers." />
        <QuickLink href="/plan" title="Plan a gameweek" description="Rolling-horizon transfer and lineup plan." />
        <QuickLink href="/recommend" title="Fresh recommendation" description="Pick a squad from scratch (wildcard, backtest)." />
        <QuickLink href="/accuracy" title="Check accuracy" description="Predicted vs. actual, by model." />
      </div>

      <div className="grid grid-cols-2 gap-6">
        <HistoryCard title="Recent plans" emptyText="No plans yet." items={plans} hrefBase="/plan" />
        <HistoryCard title="Recent recommendations" emptyText="No recommendations yet." items={recommendations} hrefBase="/recommend" />
      </div>
    </div>
  );
}

function QuickLink({ href, title, description }: { href: string; title: string; description: string }) {
  return (
    <Link href={href} className="card p-5 block hover:border-accent/60 transition-colors">
      <p className="font-medium">{title}</p>
      <p className="text-sm text-muted mt-1">{description}</p>
    </Link>
  );
}

function HistoryCard({
  title, emptyText, items, hrefBase,
}: {
  title: string;
  emptyText: string;
  items: { run_id: string; season: string; gameweek: number; created_at: string }[];
  hrefBase: string;
}) {
  return (
    <div className="card p-5">
      <p className="label mb-3">{title}</p>
      {items.length === 0 ? (
        <p className="text-sm text-muted">{emptyText}</p>
      ) : (
        <ul className="space-y-1.5 text-sm">
          {items.slice(0, 5).map((item) => (
            <li key={item.run_id} className="flex justify-between">
              <Link href={hrefBase} className="hover:underline">
                {item.season} GW{item.gameweek}
              </Link>
              <span className="text-muted">{new Date(item.created_at).toLocaleDateString()}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
