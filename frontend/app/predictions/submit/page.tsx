import { API_URL, getUpcomingFixtures } from "../../../lib/api";

export const dynamic = "force-dynamic";

export default async function SubmitPrediction() {
  const fixtures = await getUpcomingFixtures();
  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
      <section className="grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
        <div>
          <p className="badge inline-block">+ Community post</p>
          <h1 className="mt-4 text-4xl font-black sm:text-5xl">Drop your pick, code, angle, or match read.</h1>
          <p className="mt-3 text-slate-300">Make the room active: post your selection, explain why, and build your public record. Keep it respectful and avoid fake guarantees.</p>
          <div className="mt-6 space-y-3 text-sm text-slate-300">
            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">🔥 Share short codes, match notes, odds angles, injury thoughts, or local insight.</div>
            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">🏆 Wins and streaks appear after picks are settled.</div>
            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">🤝 Community posts are opinions — no abuse, no spam, no “fixed game” claims.</div>
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
        <input name="pick" required maxLength={120} placeholder="Pick e.g. Home Win, Draw, Over 2.5 Goals, BTTS Yes" className="rounded-xl border border-slate-800 bg-slate-950 p-3" />
        <textarea name="analysis_text" maxLength={1000} placeholder="Write anything useful: short code, match read, form angle, lineup thought, odds move, or community note..." className="min-h-40 rounded-xl border border-slate-800 bg-slate-950 p-3" />
        <button className="rounded-xl bg-emerald-400 px-5 py-3 font-bold text-slate-950">Publish to community</button>
      </form>
      </section>
      <section className="responsible-note mt-5"><b>Responsible note:</b> Community posts are opinions, not guarantees. Rankings are historical and can change quickly.</section>
    </main>
  );
}