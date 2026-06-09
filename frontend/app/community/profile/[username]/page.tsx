import { getUserProfile } from "../../../../lib/api";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function TipsterProfile({ params }: { params: Promise<{ username: string }> }) {
  const { username } = await params;
  const profile = await getUserProfile(username);

  if (!profile) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <div className="card text-center">
          <h1 className="text-2xl font-black">User not found</h1>
          <p className="mt-2 text-slate-400">No tipster with that name exists yet.</p>
          <Link href="/community-leaderboard" className="mt-4 inline-flex rounded-xl bg-emerald-400 px-4 py-2 text-sm font-bold text-slate-950">← Back to leaderboard</Link>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-2xl px-4 py-6 sm:px-6">
      <Link href="/community-leaderboard" className="mb-4 inline-block text-sm text-slate-400 hover:text-white">← Back to leaderboard</Link>

      {/* Profile Header */}
      <div className="card relative overflow-hidden">
        <div className="absolute right-0 top-0 h-32 w-32 rounded-bl-full bg-emerald-400/10 blur-xl" />
        <div className="flex items-center gap-4">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-400/20 text-2xl font-black text-emerald-300">
            {username[0]?.toUpperCase() || "?"}
          </div>
          <div>
            <h1 className="text-2xl font-black">{username}</h1>
            <p className="mt-1 inline-flex rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-bold text-emerald-200">{profile.level}</p>
          </div>
        </div>

        {profile.badges.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {profile.badges.map((b: string) => (
              <span key={b} className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-3 py-1 text-xs text-emerald-200">{b}</span>
            ))}
          </div>
        )}

        <div className="mt-5 grid grid-cols-3 gap-3 text-center text-sm">
          <div className="rounded-xl bg-slate-950/70 p-3"><b className="text-lg text-emerald-300">{profile.wins}</b><br /><span className="text-xs text-slate-500">Wins</span></div>
          <div className="rounded-xl bg-slate-950/70 p-3"><b className="text-lg">{profile.win_rate}%</b><br /><span className="text-xs text-slate-500">Win Rate</span></div>
          <div className="rounded-xl bg-slate-950/70 p-3"><b className="text-lg">{profile.current_streak}</b><br /><span className="text-xs text-slate-500">Streak</span></div>
        </div>

        <div className="mt-3 grid grid-cols-3 gap-3 text-center text-sm">
          <div className="rounded-xl bg-slate-950/70 p-3"><b className="text-lg">{profile.total_posts}</b><br /><span className="text-xs text-slate-500">Picks</span></div>
          <div className="rounded-xl bg-slate-950/70 p-3"><b className="text-lg">{profile.settled}</b><br /><span className="text-xs text-slate-500">Settled</span></div>
          <div className="rounded-xl bg-slate-950/70 p-3"><b className={`text-lg ${(profile.profit_units || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{profile.profit_units}</b><br /><span className="text-xs text-slate-500">Units</span></div>
        </div>
      </div>

      {/* Recent Picks Timeline */}
      <div className="mt-6">
        <h2 className="mb-3 text-lg font-black">Recent Picks</h2>
        <div className="space-y-3">
          {profile.recent_picks.map((pick: any) => (
            <div key={pick.id} className="rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-bold text-white">{pick.market}: {pick.pick}</span>
                <span className={`rounded-full px-2 py-1 text-xs font-bold ${
                  !pick.is_settled ? "bg-sky-400/10 text-sky-300" :
                  pick.was_correct ? "bg-emerald-400/10 text-emerald-300" : "bg-rose-400/10 text-rose-300"
                }`}>
                  {!pick.is_settled ? "⏳ Pending" : pick.was_correct ? "✅ Won" : "❌ Lost"}
                </span>
              </div>
              {pick.is_settled && pick.profit_units !== null && (
                <p className={`mt-1 text-sm ${pick.profit_units >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                  {pick.profit_units >= 0 ? "+" : ""}{pick.profit_units} units
                </p>
              )}
            </div>
          ))}
          {profile.recent_picks.length === 0 && (
            <p className="text-slate-400">No picks yet.</p>
          )}
        </div>
      </div>
    </main>
  );
}