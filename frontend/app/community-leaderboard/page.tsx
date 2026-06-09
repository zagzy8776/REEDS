import { getCommunityLeaderboard, getCommunityOverview } from "../../lib/api";
import Link from "next/link";
import { CommunityPostActions } from "../../components/CommunityPostActions";
import { WinSlipForm } from "../../components/WinSlipForm";

export const dynamic = "force-dynamic";

function resultLabel(post: any) {
  if (!post.is_settled) return "Pending";
  return post.was_correct ? "Won" : "Lost";
}

export default async function CommunityLeaderboard() {
  const [rows, overview] = await Promise.all([getCommunityLeaderboard(), getCommunityOverview()]);
  const top = rows.slice(0, 3);
  const weekly = overview.weekly_winners || [];
  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <section className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
        <div>
          <p className="badge inline-block">Community</p>
          <h1 className="mt-4 text-4xl font-black sm:text-5xl">Post your games. Follow good picks. Show the wins.</h1>
          <p className="mt-3 max-w-3xl text-slate-300">This is where tipsters drop picks and the room reacts. Tail a game, leave your take, rate the post, and if it wins, come back and show the slip.</p>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row">
            <Link href="/predictions/submit" className="rounded-xl bg-emerald-400 px-5 py-3 text-center font-black text-slate-950">+ Post a pick</Link>
            <Link href="/predictions" className="rounded-xl border border-white/10 bg-white/5 px-5 py-3 text-center font-bold">See AI picks</Link>
          </div>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">What the room is doing</p>
          <div className="mt-4 grid grid-cols-2 gap-3 text-center sm:grid-cols-4">
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{overview.total_posts || 0}</b><br /><span className="text-xs text-slate-500">Posts</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{overview.active_users_7d || 0}</b><br /><span className="text-xs text-slate-500">Active 7d</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{overview.community_hit_rate || 0}%</b><br /><span className="text-xs text-slate-500">Hit rate</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{overview.plays || 0}</b><br /><span className="text-xs text-slate-500">Games played</span></div>
          </div>
        </div>
      </section>

      <section className="mt-8 grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="card">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="badge inline-block">Winner board</p>
              <h2 className="mt-3 text-2xl font-black">Winners this week</h2>
              <p className="mt-2 text-sm text-slate-400">A quick look at who is hot this week. Post, win, and you move up.</p>
            </div>
            <span className="rounded-xl border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm font-bold text-amber-200">🏆 Live board</span>
          </div>
          <div className="mt-5 grid gap-3 md:grid-cols-2">
            {(weekly.length ? weekly : [{ username: "No weekly winner yet", posts: 0, settled: 0, wins: 0, win_rate: 0, profit_units: 0, weekly_score: 0, level: "Post and win to enter" }]).map((w: any, i: number) => (
              <div key={`${w.username}-${i}`} className="rounded-2xl border border-slate-800 bg-slate-950 p-4">
                <div className="flex items-center justify-between gap-3"><b>#{i + 1} {w.username}</b><span className="rounded-full bg-amber-400/10 px-2 py-1 text-xs text-amber-200">{w.level}</span></div>
                <div className="mt-3 grid grid-cols-4 gap-2 text-center text-xs">
                  <div className="rounded-xl bg-white/5 p-2"><b className="text-emerald-300">{w.wins}</b><br />Wins</div>
                  <div className="rounded-xl bg-white/5 p-2"><b>{w.win_rate}%</b><br />Rate</div>
                  <div className="rounded-xl bg-white/5 p-2"><b>{w.posts}</b><br />Posts</div>
                  <div className="rounded-xl bg-white/5 p-2"><b>{w.weekly_score}</b><br />Score</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="card">
          <h2 className="text-xl font-black">Post your winning slip</h2>
          <p className="mt-2 text-sm text-slate-400">Got paid from a pick here? Drop the win so people can see what landed.</p>
          <WinSlipForm posts={overview.recent_posts || []} />
        </div>
      </section>

      <section className="mt-8 grid gap-5 lg:grid-cols-[0.8fr_1.2fr]">
        <div className="card">
          <h2 className="text-xl font-bold">Hot community markets</h2>
          <p className="mt-2 text-sm text-slate-400">The markets people are talking about most.</p>
          <div className="mt-4 space-y-3">
            {(overview.top_markets || []).length ? overview.top_markets.map((m: any) => (
              <div key={m.market} className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                <div className="flex items-center justify-between"><b>{m.market}</b><span className="text-emerald-300">{m.count}</span></div>
              </div>
            )) : <p className="text-sm text-slate-400">No community market data yet.</p>}
          </div>
        </div>
        <div className="card">
          <h2 className="text-xl font-bold">Latest tipster games</h2>
          <p className="mt-2 text-sm text-slate-400">See a pick you like? Tail it, rate it, or leave a comment.</p>
          <div className="mt-4 grid gap-3">
            {(overview.recent_posts || []).length ? overview.recent_posts.slice(0, 6).map((p: any) => (
              <div key={p.id} className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                <div className="flex items-center justify-between gap-2"><Link href={`/fixtures/${p.fixture_id}`} className="font-bold hover:text-emerald-300">{p.username}</Link><span className={p.is_settled ? p.was_correct ? "text-emerald-300" : "text-rose-300" : "text-sky-300"}>{resultLabel(p)}</span></div>
                <p className="mt-2 text-sm text-white">{p.market}: {p.pick}</p>
                <p className="mt-1 line-clamp-2 text-xs text-slate-400">{p.analysis_text || "No note added yet."}</p>
                <CommunityPostActions post={p} />
              </div>
            )) : <p className="text-sm text-slate-400">No posts yet. Start the room with a pick.</p>}
          </div>
        </div>
      </section>

      <section className="mt-8 mobile-safe-grid">
        {(top.length ? top : [{ username: "Be first", settled: 0, wins: 0, win_rate: 0, current_streak: 0, best_streak: 0, profit_units: 0 }]).map((r: any, i: number) => (
              <div key={`${r.username}-${i}`} className="card relative overflow-hidden">
            <div className="absolute right-0 top-0 h-24 w-24 rounded-bl-full bg-emerald-400/10 blur-xl" />
            <p className="text-sm text-slate-400">#{i + 1} Tipster</p>
            <h2 className="mt-2 text-2xl font-black">{r.username}</h2>
                <p className="mt-2 inline-flex rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-bold text-emerald-200">{r.level || "Rookie"}</p>
                <div className="mt-3 flex flex-wrap gap-2">{(r.badges || []).length ? r.badges.map((b: string) => <span key={b} className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2 py-1 text-xs text-emerald-200">{b}</span>) : <span className="rounded-full border border-slate-700 px-2 py-1 text-xs text-slate-400">Building record</span>}</div>
            <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-xl bg-slate-950/70 p-3"><span className="text-slate-500">Wins</span><br /><b>{r.wins}</b></div>
              <div className="rounded-xl bg-slate-950/70 p-3"><span className="text-slate-500">Win rate</span><br /><b>{r.win_rate}%</b></div>
              <div className="rounded-xl bg-slate-950/70 p-3"><span className="text-slate-500">Streak</span><br /><b>{r.current_streak}</b></div>
              <div className="rounded-xl bg-slate-950/70 p-3"><span className="text-slate-500">Units</span><br /><b className={(r.profit_units || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>{r.profit_units}</b></div>
            </div>
                <p className="mt-3 text-xs text-slate-500">Favorite market: {r.favorite_market || "-"} • Rank score: {r.rank_score || 0}</p>
          </div>
        ))}
      </section>

      <div className="card mt-8 overflow-hidden">
        <div className="flex flex-col gap-2 border-b border-white/10 pb-4 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-xl font-black">Full leaderboard</h2>
            <p className="text-sm text-slate-400">Wins, streaks, units - all in one place.</p>
        </div>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead><tr className="text-slate-400"><th>Rank</th><th>User</th><th>Level</th><th>Posts</th><th>Settled</th><th>Pending</th><th>W/L</th><th>Win Rate</th><th>ROI</th><th>Best Streak</th><th>Units</th><th>Badges</th></tr></thead>
            <tbody>
              {rows.length ? rows.map((r: any, i: number) => (
                <tr key={r.username} className="border-t border-slate-800"><td className="py-3">#{i + 1}</td><td className="font-bold">{r.username}</td><td>{r.level || "Rookie"}</td><td>{r.total_posts}</td><td>{r.settled}</td><td>{r.pending}</td><td>{r.wins}-{r.losses}</td><td>{r.win_rate}%</td><td>{r.roi_percent}%</td><td>{r.best_streak}</td><td className={(r.profit_units || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>{r.profit_units}</td><td>{(r.badges || []).join(", ") || "-"}</td></tr>
              )) : <tr><td className="py-6 text-slate-400" colSpan={12}>No records yet. Post the first pick and get the board moving.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}