import { getCommunityLeaderboard, getCommunityExperts, getCommunityOverview } from "../../lib/api";
import Link from "next/link";
import { CommunityPostActions } from "../../components/CommunityPostActions";
import { WinSlipForm } from "../../components/WinSlipForm";

export const dynamic = "force-dynamic";

function resultLabel(post: any) {
  if (!post.is_settled) return "Pending";
  return post.was_correct ? "Won" : "Lost";
}

export default async function CommunityLeaderboard() {
  const [rows, experts, overview] = await Promise.all([
    getCommunityLeaderboard(),
    getCommunityExperts(),
    getCommunityOverview(),
  ]);
  const top = rows.slice(0, 3);
  const weekly = overview.weekly_winners || [];

  return (
    <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6">
      {/* Hero Section */}
      <section className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
        <div>
          <p className="badge inline-block">Community</p>
          <h1 className="mt-4 text-3xl font-black sm:text-5xl">Tipster Board</h1>
          <p className="mt-2 max-w-3xl text-slate-400">Follow the best pickers. Tail their games. Show your wins.</p>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row">
            <Link href="/predictions/submit" className="rounded-xl bg-emerald-400 px-5 py-3 text-center font-black text-slate-950">+ Post a pick</Link>
            <Link href="/community/win-wall" className="rounded-xl border border-white/10 bg-white/5 px-5 py-3 text-center font-bold">💰 Win Wall</Link>
          </div>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">Room stats</p>
          <div className="mt-4 grid grid-cols-2 gap-3 text-center sm:grid-cols-4">
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{overview.total_posts || 0}</b><br /><span className="text-xs text-slate-500">Picks</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{overview.active_users_7d || 0}</b><br /><span className="text-xs text-slate-500">Active 7d</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{overview.community_hit_rate || 0}%</b><br /><span className="text-xs text-slate-500">Hit rate</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{overview.plays || 0}</b><br /><span className="text-xs text-slate-500">Tails</span></div>
          </div>
        </div>
      </section>

      {/* 🔥 On Fire - Win Streak Spotlight */}
      {top.length > 0 && top[0].current_streak >= 2 && (
        <section className="mt-6">
          <div className="rounded-2xl border border-amber-400/30 bg-amber-400/5 p-4">
            <div className="flex items-center gap-2 text-amber-300">
              <span className="text-xl">🔥</span>
              <span className="font-black">On Fire</span>
              <span className="text-sm text-slate-400">— Tipsters with active win streaks</span>
            </div>
            <div className="mt-3 flex gap-3 overflow-x-auto pb-2">
              {top.filter((r: any) => r.current_streak >= 2).slice(0, 5).map((r: any) => (
                <Link key={r.username} href={`/community/profile/${r.username}`} className="min-w-[140px] rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-center hover:bg-amber-400/10">
                  <p className="font-bold text-white">{r.username}</p>
                  <p className="mt-1 text-sm text-amber-300">🔥 {r.current_streak} Streak</p>
                  <p className="text-xs text-slate-400">{r.win_rate}% win rate</p>
                </Link>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Weekly Winners */}
      <section className="mt-6 grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="card">
          <div className="flex items-end justify-between">
            <div>
              <p className="badge inline-block">This week</p>
              <h2 className="mt-3 text-xl font-black">Weekly Winners</h2>
            </div>
            <span className="rounded-xl border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm font-bold text-amber-200">🏆 Live</span>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {(weekly.length ? weekly : [{ username: "Be first to win this week", posts: 0, settled: 0, wins: 0, win_rate: 0, profit_units: 0, weekly_score: 0, level: "Post and win" }]).map((w: any, i: number) => (
              <div key={`${w.username}-${i}`} className="rounded-2xl border border-slate-800 bg-slate-950 p-4">
                <div className="flex items-center justify-between"><b>#{i + 1} {w.username}</b><span className="rounded-full bg-amber-400/10 px-2 py-1 text-xs text-amber-200">{w.level}</span></div>
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
          <h2 className="text-xl font-black">Post winning slip</h2>
          <p className="mt-2 text-sm text-slate-400">Won from a pick here? Show the room.</p>
          <WinSlipForm posts={overview.recent_posts || []} />
        </div>
      </section>

      {/* Verified Experts Section */}
      {experts.length > 0 && (
        <section className="mt-6">
          <div className="card">
            <div className="flex items-center gap-2">
              <span className="text-lg">⭐</span>
              <h2 className="text-xl font-black">Verified Experts</h2>
              <span className="rounded-full bg-emerald-400/10 px-2 py-1 text-xs text-emerald-300">60%+ win rate</span>
            </div>
            <p className="mt-1 text-sm text-slate-400">Tipsters with a proven track record. Minimum 10 settled picks.</p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {experts.slice(0, 6).map((e: any) => (
                <Link key={e.username} href={`/community/profile/${e.username}`} className="flex items-center justify-between rounded-2xl border border-emerald-400/20 bg-emerald-400/5 p-4 transition-all hover:bg-emerald-400/10">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-emerald-400/20 text-sm font-bold text-emerald-300">
                      {e.username[0]?.toUpperCase()}
                    </div>
                    <div>
                      <p className="font-bold">{e.username}</p>
                      <p className="text-xs text-slate-400">{e.level}</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-black text-emerald-300">{e.win_rate}%</p>
                    <p className="text-xs text-slate-500">{e.settled} picks</p>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Full Leaderboard - Mobile Glassmorphism */}
      <section className="mt-6">
        <div className="card overflow-hidden">
          <div className="flex items-end justify-between border-b border-white/10 pb-4">
            <div>
              <h2 className="text-xl font-black">Full Leaderboard</h2>
              <p className="mt-1 text-sm text-slate-400">All tipsters ranked by performance</p>
            </div>
            <Link href="/community/win-wall" className="text-sm text-emerald-300 hover:text-emerald-200">💰 Win Wall →</Link>
          </div>

          <div className="mt-4 space-y-2">
            {rows.length ? rows.map((r: any, i: number) => (
              <Link key={r.username} href={`/community/profile/${r.username}`} className="flex items-center gap-3 rounded-2xl border border-slate-800/50 bg-slate-900/30 p-4 backdrop-blur transition-all hover:border-slate-700 hover:bg-slate-900/50">
                <span className={`w-8 text-center font-black ${i < 3 ? "text-emerald-300" : "text-slate-500"}`}>
                  {i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : `#${i + 1}`}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-bold truncate">{r.username}</span>
                    <span className={`rounded-full px-2 py-0.5 text-xs font-bold ${
                      r.level_color === "amber" ? "bg-amber-400/10 text-amber-200" :
                      r.level_color === "emerald" ? "bg-emerald-400/10 text-emerald-200" :
                      r.level_color === "sky" ? "bg-sky-400/10 text-sky-200" :
                      r.level_color === "violet" ? "bg-violet-400/10 text-violet-200" :
                      "bg-slate-800 text-slate-400"
                    }`}>{r.level}</span>
                  </div>
                  <div className="mt-1 flex items-center gap-3 text-xs text-slate-500">
                    <span>{r.total_posts} picks</span>
                    <span>{r.wins}W-{r.losses}L</span>
                    {r.current_streak >= 2 && <span className="text-amber-300">🔥 {r.current_streak}</span>}
                  </div>
                </div>
                <div className="text-right">
                  <p className="font-black text-emerald-300">{r.win_rate}%</p>
                  <p className={`text-xs ${(r.profit_units || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                    {r.profit_units >= 0 ? "+" : ""}{r.profit_units}u
                  </p>
                </div>
              </Link>
            )) : (
              <p className="py-6 text-center text-slate-400">No tipsters yet. Be the first to post a pick.</p>
            )}
          </div>
        </div>
      </section>

      {/* Hot Markets */}
      {(overview.top_markets || []).length > 0 && (
        <section className="mt-6">
          <div className="card">
            <h2 className="text-xl font-black">Hot Markets</h2>
            <p className="mt-1 text-sm text-slate-400">What the room is picking most</p>
            <div className="mt-4 flex flex-wrap gap-2">
              {overview.top_markets.map((m: any) => (
                <span key={m.market} className="rounded-xl border border-slate-800 bg-slate-950 px-4 py-2 text-sm">
                  {m.market} <span className="ml-1 text-emerald-300">{m.count}</span>
                </span>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Latest Picks */}
      {(overview.recent_posts || []).length > 0 && (
        <section className="mt-6">
          <h2 className="mb-3 text-lg font-black">Latest Picks</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {overview.recent_posts.slice(0, 6).map((p: any) => (
              <div key={p.id} className="rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
                <div className="flex items-center justify-between gap-2">
                  <Link href={`/community/profile/${p.username}`} className="font-bold hover:text-emerald-300">{p.username}</Link>
                  <span className={p.is_settled ? p.was_correct ? "text-emerald-300" : "text-rose-300" : "text-sky-300"}>{resultLabel(p)}</span>
                </div>
                <p className="mt-2 text-sm text-white">{p.market}: {p.pick}</p>
                {p.analysis_text && <p className="mt-1 line-clamp-2 text-xs text-slate-400">{p.analysis_text}</p>}
                <CommunityPostActions post={p} />
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}