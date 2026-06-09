import { API_URL, getUpcomingFixtures } from "../../../lib/api";

export const dynamic = "force-dynamic";

export default async function SubmitPrediction() {
  const fixtures = await getUpcomingFixtures();
  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
      <section className="grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
        <div>
          <p className="badge inline-block">+ Post pick</p>
          <h1 className="mt-4 text-4xl font-black sm:text-5xl">Drop your pick for the room.</h1>
          <p className="mt-3 text-slate-300">Choose a match, add your selection, and tell people why you like it. Keep it clean - no fake sure things.</p>
          <div className="mt-6 space-y-3 text-sm text-slate-300">
            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">🔥 Share codes, odds moves, team news, or your match read.</div>
            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">🏆 Wins and streaks update after picks settle.</div>
            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">🤝 No abuse, no spam, no “fixed game” talk.</div>
          </div>
        </div>

      <form action={`${API_URL}/api/community/predictions`} method="post" className="grid gap-4 rounded-2xl border border-white/10 bg-slate-900/70 p-5 shadow-xl backdrop-blur">
        <input name="username" required maxLength={80} placeholder="Username / tipster name" className="rounded-xl border border-slate-800 bg-slate-950 p-3" />
        <select name="fixture_id" required className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">Choose upcoming fixture</option>
          {fixtures.map((f: any) => <option key={f.id} value={f.id}>{f.match_date} • {f.league} • {f.home_team} vs {f.away_team}</option>)}
        </select>
        <select name="market" required className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="1X2">1X2 / Moneyline</option>
          <option value="Goals">Goals</option>
          <option value="BTTS">BTTS</option>
        </select>
        <input name="pick" required maxLength={120} placeholder="Pick e.g. Home Win, Draw, Over 2.5, BTTS Yes" className="rounded-xl border border-slate-800 bg-slate-950 p-3" />
        <textarea name="analysis_text" maxLength={1000} placeholder="Add your reason, code, team news, odds move, or anything useful..." className="min-h-40 rounded-xl border border-slate-800 bg-slate-950 p-3" />
        <button className="rounded-xl bg-emerald-400 px-5 py-3 font-bold text-slate-950">Post pick</button>
      </form>
      </section>
      <section className="responsible-note mt-5"><b>Play smart:</b> Community picks are opinions, not guarantees. Records can change fast.</section>
    </main>
  );
}