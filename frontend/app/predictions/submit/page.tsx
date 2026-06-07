import { API_URL, getUpcomingFixtures } from "../../../lib/api";

export const dynamic = "force-dynamic";

export default async function SubmitPrediction() {
  const fixtures = await getUpcomingFixtures();
  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <p className="badge inline-block">Community OSINT</p>
      <h1 className="mt-4 text-4xl font-black">Submit your match prediction</h1>
      <p className="mt-2 text-slate-400">Community picks are stored separately from LOYAL EDGE AI predictions and are ranked through settled performance over time.</p>

      <form action={`${API_URL}/api/community/predictions`} method="post" className="mt-8 grid gap-4 rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
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
        <textarea name="analysis_text" maxLength={1000} placeholder="2-line reasoning: form, injuries, odds move, local insight..." className="min-h-32 rounded-xl border border-slate-800 bg-slate-950 p-3" />
        <button className="rounded-xl bg-emerald-400 px-5 py-3 font-bold text-slate-950">Publish community pick</button>
      </form>
      <section className="responsible-note mt-5"><b>Responsible note:</b> Community posts are opinions, not guarantees. Rankings are historical and can change quickly.</section>
    </main>
  );
}