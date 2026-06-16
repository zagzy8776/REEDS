import Link from "next/link";
import { PredictionCard } from "../components/PredictionCard";
import { getFixtureStatus, getFixtures, getTodayPredictions, getStats } from "../lib/api";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [allPicks, status, fixtures, stats] = await Promise.all([
    getTodayPredictions(),
    getFixtureStatus(),
    getFixtures({ scope: "upcoming", limit: "24" }),
    getStats(),
  ]);
  const picks = allPicks.slice(0, 3);
  const sports = Array.from(new Set(fixtures.map((f: any) => f.sport))).filter(Boolean);
  const sportCounts = fixtures.reduce((acc: Record<string, number>, f: any) => {
    acc[f.sport] = (acc[f.sport] || 0) + 1;
    return acc;
  }, {});
  const showcase = fixtures.slice(0, 6);
  const results = stats.results || {};
  const hitRate = results.hit_rate || 0;
  const settled = results.settled_picks || 0;
  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      {/* Hero */}
      <section className="grid gap-8 md:grid-cols-2 md:items-center">
        <div>
          <p className="badge inline-block">Transparent AI predictions</p>
          <h1 className="mt-5 text-4xl font-black leading-tight sm:text-5xl">Sports predictions with proof, not promises.</h1>
          <p className="mt-5 text-slate-300">Every pick shows confidence, risk, and the model’s reasoning. Track every result on the history page. No hidden track record.</p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Link className="rounded-xl bg-emerald-400 px-5 py-3 font-bold text-slate-950" href="/predictions">View AI Picks</Link>
            <Link className="rounded-xl border border-slate-700 px-5 py-3 font-bold" href="/predictions/history">Track Record</Link>
            <Link className="rounded-xl border border-emerald-400/30 bg-emerald-400/10 px-5 py-3 font-bold text-emerald-200" href="/predictions/submit">+ Post Pick</Link>
          </div>
          {/* Live proof bar */}
          <div className="mt-8 grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">Settled picks</p>
              <p className="mt-1 text-2xl font-black">{settled}</p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">AI hit rate</p>
              <p className="mt-1 text-2xl font-black text-emerald-300">{hitRate}%</p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">Sports covered</p>
              <p className="mt-1 text-2xl font-black">{sports.length || status?.sports?.length || 0}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <h2 className="text-xl font-bold">Top picks right now</h2>
          <p className="mt-1 text-sm text-slate-400">Highest-confidence AI reads for today.</p>
          <div className="mt-4 space-y-3">{picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="rounded-2xl border border-dashed border-emerald-400/30 bg-emerald-400/5 p-5"><p className="font-bold text-emerald-200">Picks loading or no upcoming fixtures.</p><p className="mt-2 text-sm text-slate-400">The AI board refreshes as soon as new matches are scheduled.</p></div>}</div>
          {picks.length > 0 && <Link href="/predictions" className="mt-4 block text-center text-sm font-bold text-emerald-300">View all picks →</Link>}
        </div>
      </section>

      {/* Sports coverage */}
      <section className="mt-12 card">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="badge inline-block">Live coverage</p>
            <h2 className="mt-4 text-3xl font-black">Sports on the board</h2>
            <p className="mt-2 text-slate-400">Multi-sport coverage from free and paid feeds, blended so the board never goes dark.</p>
          </div>
          <Link href="/fixtures" className="rounded-xl border border-emerald-400/30 bg-emerald-400/10 px-4 py-2 text-sm font-black text-emerald-200">Open match center</Link>
        </div>
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {(sports.length ? sports : status?.sports || ["soccer", "basketball", "cricket"]).map((sport: string) => (
            <Link key={sport} href={`/fixtures?sport=${encodeURIComponent(sport)}`} className="rounded-2xl border border-white/10 bg-slate-950/70 p-4 hover:border-emerald-400/30">
              <p className="text-xs uppercase tracking-wide text-slate-500">Sport</p>
              <h3 className="mt-1 text-xl font-black capitalize">{sport.replaceAll("_", " ")}</h3>
              <p className="mt-1 text-sm text-emerald-300">{sportCounts[sport] || 0} upcoming</p>
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

      {/* Trust / transparency */}
      <section className="mt-12 grid gap-5 md:grid-cols-3">
        <div className="card">
          <h3 className="font-bold text-emerald-300">Full model reasoning</h3>
          <p className="mt-2 text-sm text-slate-300">Every pick shows the exact signals the model checked: form, goals, odds, streaks, H2H. No black boxes.</p>
        </div>
        <div className="card">
          <h3 className="font-bold text-emerald-300">Track record stays public</h3>
          <p className="mt-2 text-sm text-slate-300">History page shows every past prediction with its result. Wins, losses, and hit rate are visible to everyone.</p>
        </div>
        <div className="card">
          <h3 className="font-bold text-emerald-300">Risk shown upfront</h3>
          <p className="mt-2 text-sm text-slate-300">Confidence and risk level are on every card. We do not hide weak picks behind hype.</p>
        </div>
      </section>

      {/* CTA */}
      <section className="mt-12 card">
        <div className="grid gap-6 md:grid-cols-2 md:items-center">
          <div>
            <p className="badge inline-block">Community</p>
            <h2 className="mt-4 text-3xl font-black">Follow tipsters, copy wins.</h2>
            <p className="mt-3 text-slate-300">See which community users are hot, follow them, and copy their verified picks. Post your own and build a public track record.</p>
          </div>
          <div className="flex flex-col gap-3">
            <Link href="/community-leaderboard" className="rounded-xl bg-emerald-400 px-5 py-3 font-bold text-slate-950 text-center">Tipster Leaderboard</Link>
            <Link href="/community/win-wall" className="rounded-xl border border-slate-700 px-5 py-3 font-bold text-center">Win Wall</Link>
            <Link href="/predictions/submit" className="rounded-xl border border-emerald-400/30 bg-emerald-400/10 px-5 py-3 font-bold text-emerald-200 text-center">+ Post your pick</Link>
          </div>
        </div>
      </section>
    </main>
  );
}
