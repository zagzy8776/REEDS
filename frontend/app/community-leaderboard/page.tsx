import { getCommunityLeaderboard } from "../../lib/api";

export const dynamic = "force-dynamic";

export default async function CommunityLeaderboard() {
  const rows = await getCommunityLeaderboard();
  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <p className="badge inline-block">Community leaderboard</p>
      <h1 className="mt-4 text-4xl font-black">Tipsters trying to beat the AI</h1>
      <p className="mt-2 text-slate-400">Users are ranked only on settled picks. This keeps expert signal separate from noise and separate from AI model training data.</p>
      <div className="card mt-8 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead><tr className="text-slate-400"><th>Rank</th><th>User</th><th>Settled</th><th>Wins</th><th>Win Rate</th><th>Current Streak</th><th>Best Streak</th><th>Profit Units</th></tr></thead>
          <tbody>
            {rows.length ? rows.map((r: any, i: number) => (
              <tr key={r.username} className="border-t border-slate-800"><td className="py-3">#{i + 1}</td><td className="font-bold">{r.username}</td><td>{r.settled}</td><td>{r.wins}</td><td>{r.win_rate}%</td><td>{r.current_streak}</td><td>{r.best_streak}</td><td className={(r.profit_units || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>{r.profit_units}</td></tr>
            )) : <tr><td className="py-3 text-slate-400" colSpan={8}>No settled community picks yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </main>
  );
}