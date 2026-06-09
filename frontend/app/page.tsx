import Link from "next/link";
import { PredictionCard } from "../components/PredictionCard";
import { getFixtureStatus, getFixtures, getTodayPredictions } from "../lib/api";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [allPicks, status, fixtures] = await Promise.all([
    getTodayPredictions(),
    getFixtureStatus(),
    getFixtures({ scope: "upcoming", limit: "24" }),
  ]);
  const picks = allPicks.slice(0, 3);
  const sports = Array.from(new Set(fixtures.map((f: any) => f.sport))).filter(Boolean);
  const sportCounts = fixtures.reduce((acc: Record<string, number>, f: any) => {
    acc[f.sport] = (acc[f.sport] || 0) + 1;
    return acc;
  }, {});
  const showcase = fixtures.slice(0, 6);
  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <section className="grid gap-8 md:grid-cols-2 md:items-center">
        <div>
          <p className="badge inline-block">AI picks + real tipsters</p>
          <h1 className="mt-5 text-4xl font-black leading-tight sm:text-5xl">Today’s picks, hot tipsters, and games the room is watching.</h1>
          <p className="mt-5 text-slate-300">Check the AI board, see what other users are backing, post your own pick, and follow who is actually winning.</p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Link className="rounded-xl bg-emerald-400 px-5 py-3 font-bold text-slate-950" href="/combo">Get 3-leg combo</Link>
            <Link className="rounded-xl border border-slate-700 px-5 py-3 font-bold" href="/predictions">View Picks</Link>
            <Link className="rounded-xl border border-emerald-400/30 bg-emerald-400/10 px-5 py-3 font-bold text-emerald-200" href="/predictions/submit">+ Post Pick</Link>
          </div>
          <div className="mt-8 grid gap-3 text-sm text-slate-300 sm:grid-cols-3">
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><b className="text-white">Football</b><br />1X2, goals, BTTS, double chance</div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><b className="text-white">Basketball</b><br />moneyline, spread and totals</div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><b className="text-white">More sports</b><br />Cricket, tennis, NFL-style reads</div>
          </div>
        </div>
        <div className="card">
          <h2 className="text-xl font-bold">Top picks today</h2>
          <div className="mt-4 space-y-3">{picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="rounded-2xl border border-dashed border-emerald-400/30 bg-emerald-400/5 p-5"><p className="font-bold text-emerald-200">No AI picks yet.</p><p className="mt-2 text-sm text-slate-400">Post a community pick while the next refresh runs, or check who is active on the leaderboard.</p><Link href="/predictions/submit" className="mt-4 inline-flex rounded-xl bg-emerald-400 px-4 py-2 text-sm font-black text-slate-950">+ Post first pick</Link></div>}</div>
        </div>
      </section>
      <section className="mt-12 card">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="badge inline-block">Live sports coverage</p>
            <h2 className="mt-4 text-3xl font-black">Sports on the board right now</h2>
            <p className="mt-2 text-slate-400">Free-tier APIs are blended carefully so the site keeps showing games without burning limits.</p>
          </div>
          <Link href="/fixtures" className="rounded-xl border border-emerald-400/30 bg-emerald-400/10 px-4 py-2 text-sm font-black text-emerald-200">Open match center</Link>
        </div>
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {(sports.length ? sports : status?.sports || ["soccer", "basketball", "cricket"]).map((sport: string) => (
            <Link key={sport} href={`/fixtures?sport=${encodeURIComponent(sport)}`} className="rounded-2xl border border-white/10 bg-slate-950/70 p-4 hover:border-emerald-400/30">
              <p className="text-xs uppercase tracking-wide text-slate-500">Sport</p>
              <h3 className="mt-1 text-xl font-black capitalize">{sport.replaceAll("_", " ")}</h3>
              <p className="mt-1 text-sm text-emerald-300">{sportCounts[sport] || 0} upcoming shown</p>
            </Link>
          ))}
        </div>
        {showcase.length > 0 && (
          <div className="mt-5 grid gap-3 md:grid-cols-2">
            {showcase.map((f: any) => (
              <Link key={f.id} href={`/fixtures/${f.id}`} className="rounded-2xl border border-slate-800 bg-slate-950 p-4 hover:border-sky-400/30">
                <p className="text-xs uppercase tracking-wide text-slate-500">{f.sport} • {f.league}</p>
                <p className="mt-1 font-black">{f.home_team} vs {f.away_team}</p>
                <p className="mt-1 text-xs text-slate-500">{f.match_date} • {f.source}</p>
              </Link>
            ))}
          </div>
        )}
      </section>
      <section className="mt-12 grid gap-5 md:grid-cols-3">
        <div className="card"><h3 className="font-bold text-emerald-300">Better inputs</h3><p className="mt-2 text-sm text-slate-300">The picks use team form, league strength, odds, and match context - not random guesses.</p></div>
        <div className="card"><h3 className="font-bold text-emerald-300">Records stay visible</h3><p className="mt-2 text-sm text-slate-300">Old picks are kept so wins, losses, and changes can be checked later.</p></div>
        <div className="card"><h3 className="font-bold text-emerald-300">No fake guarantees</h3><p className="mt-2 text-sm text-slate-300">Every pick shows confidence and risk, but it is still sport. Stake wisely.</p></div>
      </section>
      <section className="mt-12 card">
        <p className="badge inline-block">How to read it</p>
        <h2 className="mt-4 text-3xl font-black">What EDGE Score means</h2>
        <p className="mt-3 text-slate-300">EDGE Score is the model’s way of saying how strong a pick looks after checking form, goals, odds, and market context. It is not a promise. The results page keeps track of how picks perform over time.</p>
      </section>
    </main>
  );
}
