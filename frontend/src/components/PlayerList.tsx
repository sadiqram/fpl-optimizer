type PlayerSummary = { player_id: number; name: string; expected_points: number | null };

export function PlayerList({
  title,
  players,
  captainId,
  viceCaptainId,
}: {
  title: string;
  players: PlayerSummary[];
  captainId?: number;
  viceCaptainId?: number;
}) {
  return (
    <div>
      <p className="label mb-2">{title}</p>
      <ul className="space-y-1">
        {players.map((p) => (
          <li key={p.player_id} className="flex items-center justify-between text-sm py-1 px-2 rounded-lg hover:bg-surface-muted">
            <span>
              {p.name}
              {p.player_id === captainId && <span className="ml-1.5 text-accent font-semibold">(C)</span>}
              {p.player_id === viceCaptainId && <span className="ml-1.5 text-accent font-semibold">(VC)</span>}
            </span>
            <span className="text-muted tabular-nums">
              {p.expected_points === null ? "?" : p.expected_points.toFixed(1)} xPts
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
