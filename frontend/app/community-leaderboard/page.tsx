import { getCommunityLeaderboard } from "../../lib/api";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function CommunityLeaderboard() {
  const rows = await getCommunityLeaderboard();
  const top = rows.slice(0, 3);
  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <section className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
        <div>
          <p className="badge inline-block">Community arena</p>
          <h1 className="mt-4 text-4xl font-black sm:text-5xl">Tipster room, streaks, wins, and public football talk.</h1>
          <p className="mt-3 max-w-3xl text-slate-300">This is where users post picks, explain angles, react to each other, and build a visible record. AI picks and community posts stay separated so users can compare both signals clearly.</p>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row">
            <Link href="/predictions/submit" className="rounded-xl bg-emerald-400 px-5 py-3 text-center font-black text-slate-950">+ Post a pick</Link>
            <Link href="/predictions" className="rounded-xl border border-white/10 bg-white/5 px-5 py-3 text-center font-bold">Compare AI board</Link>
          </div>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">Live community status</p>
          <div className="mt-4 grid grid-cols-3 gap-3 text-center">
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{rows.length}</b><br /><span className="text-xs text-slate-500">Ranked</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{top[0]?.win_rate || 0}%</b><br /><span className="text-xs text-slate-500">Top rate</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{top[0]?.best_streak || 0}</b><br /><span className="text-xs text-slate-500">Best run</span></div>
          </div>
        </div>
      </section>

      <section className="mt-8 mobile-safe-grid">
        {(top.length ? top : [{ username: "Be first", settled: 0, wins: 0, win_rate: 0, current_streak: 0, best_streak: 0, profit_units: 0 }]).map((r: any, i: number) => (
          <div key={`${r.username}-${i}`} className="card relative overflow-hidden">
            <div className="absolute right-0 top-0 h-24 w-24 rounded-bl-full bg-emerald-400/10 blur-xl" />
            <p className="text-sm text-slate-400">#{i + 1} Tipster</p>
            <h2 className="mt-2 text-2xl font-black">{r.username}</h2>
            <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-xl bg-slate-950/70 p-3"><span className="text-slate-500">Wins</span><br /><b>{r.wins}</b></div>
              <div className="rounded-xl bg-slate-950/70 p-3"><span className="text-slate-500">Win rate</span><br /><b>{r.win_rate}%</b></div>
              <div className="rounded-xl bg-slate-950/70 p-3"><span className="text-slate-500">Streak</span><br /><b>{r.current_streak}</b></div>
              <div className="rounded-xl bg-slate-950/70 p-3"><span className="text-slate-500">Units</span><br /><b className={(r.profit_units || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>{r.profit_units}</b></div>
            </div>
          </div>
        ))}
      </section>

      <div className="card mt-8 overflow-hidden">
        <div className="flex flex-col gap-2 border-b border-white/10 pb-4 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-xl font-black">Full leaderboard</h2>
          <p className="text-sm text-slate-400">Ranked by settled results, streaks, and profit units.</p>
        </div>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead><tr className="text-slate-400"><th>Rank</th><th>User</th><th>Settled</th><th>Wins</th><th>Win Rate</th><th>Current Streak</th><th>Best Streak</th><th>Profit Units</th></tr></thead>
            <tbody>
              {rows.length ? rows.map((r: any, i: number) => (
                <tr key={r.username} className="border-t border-slate-800"><td className="py-3">#{i + 1}</td><td className="font-bold">{r.username}</td><td>{r.settled}</td><td>{r.wins}</td><td>{r.win_rate}%</td><td>{r.current_streak}</td><td>{r.best_streak}</td><td className={(r.profit_units || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>{r.profit_units}</td></tr>
              )) : <tr><td className="py-6 text-slate-400" colSpan={8}>No settled records yet. The room is open — post the first pick and start the leaderboard.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}