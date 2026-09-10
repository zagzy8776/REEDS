import Link from "next/link";
import { PredictionCard } from "../components/PredictionCard";
import { getFixtureStatus, getFixtures, getTodayPredictions, getStats } from "../lib/api";

export const dynamic = "force-dynamic";

function Stat({ label, value, note }: { label: string; value: string | number; note: string }) {
  return <div className="rounded-2xl border border-white/[0.06] bg-slate-900/45 p-4"><p className="section-kicker">{label}</p><p className="mt-2 text-2xl font-black tracking-tight text-white">{value}</p><p className="mt-1 text-[11px] text-slate-600">{note}</p></div>;
}

export default async function Home() {
  const [allPicks, status, fixtures, stats] = await Promise.all([
    getTodayPredictions(),
    getFixtureStatus(),
    getFixtures({ scope: "all", limit: "24" }),
    getStats(),
  ]);
  const picks = allPicks.slice(0, 6);
  const sports = Array.from(new Set(fixtures.map((f: any) => f.sport))).filter(Boolean);
  const sportCounts = fixtures.reduce((acc: Record<string, number>, f: any) => { acc[f.sport] = (acc[f.sport] || 0) + 1; return acc; }, {});
  const results = stats.results || {};
  const settled = Number(results.settled_picks || 0);
  const hitRate = Number(results.hit_rate || 0);
  const strongReads = allPicks.filter((p: any) => p.verdict?.key === "strong_read").length;

  return (
    <main className="mx-auto max-w-7xl px-4 pb-12 pt-7 sm:px-6 sm:pt-10">
      <section className="grid gap-6 lg:grid-cols-[1.1fr_.9fr] lg:items-end">
        <div>
          <p className="badge inline-flex">Today's intelligence</p>
          <h1 className="mt-4 max-w-3xl text-4xl font-black leading-[1.03] tracking-[-.045em] text-white sm:text-6xl">Know what REEDS sees before the match starts.</h1>
          <p className="mt-5 max-w-2xl text-base leading-7 text-slate-400 sm:text-lg">Clear predictions, the reasons behind them, and a record you can check. No hype. No winner-only history.</p>
          <div className="mt-7 flex flex-wrap gap-2"><Link href="/predictions" className="rounded-xl bg-emerald-400 px-5 py-3 text-sm font-black text-slate-950 transition hover:bg-emerald-300">Explore today's reads</Link><Link href="/history" className="rounded-xl border border-white/10 bg-white/[0.03] px-5 py-3 text-sm font-bold text-slate-200 transition hover:bg-white/[0.06]">See the record</Link></div>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-2">
          <Stat label="Strong reads" value={strongReads} note="today" />
          <Stat label="Settled" value={settled || "—"} note={settled ? "published picks" : "record building"} />
          <Stat label="Hit rate" value={settled ? `${hitRate}%` : "—"} note={settled ? "live record" : "no settled sample yet"} />
          <Stat label="Fixtures" value={fixtures.length} note="on the board" />
        </div>
      </section>

      <section className="mt-10">
        <div className="flex items-end justify-between gap-4"><div><p className="section-kicker">The board</p><h2 className="section-title">Reads worth your attention</h2><p className="mt-2 max-w-xl text-sm text-slate-500">The headline is simple: what does REEDS lean toward, how confident is it, and why?</p></div><Link href="/predictions" className="hidden text-xs font-bold text-emerald-300 sm:block">View all reads →</Link></div>
        {picks.length ? <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">{picks.map((p: any) => <PredictionCard key={p.id} p={p} />)}</div> : <div className="mt-5 rounded-2xl border border-dashed border-white/10 bg-slate-900/40 p-8 text-center"><p className="font-bold text-slate-200">No published reads yet.</p><p className="mt-2 text-sm text-slate-500">REEDS will show a read when the available evidence meets its publishing rules.</p><Link href="/fixtures" className="mt-5 inline-flex rounded-xl border border-emerald-400/20 bg-emerald-400/10 px-4 py-2.5 text-xs font-bold text-emerald-300">Browse fixtures</Link></div>}
      </section>

      <section className="mt-12 grid gap-4 lg:grid-cols-[1.4fr_.6fr]">
        <div className="card">
          <div className="flex items-end justify-between gap-4"><div><p className="section-kicker">Coverage</p><h2 className="section-title">What's happening today</h2></div><Link href="/fixtures" className="text-xs font-bold text-slate-400 hover:text-white">All fixtures →</Link></div>
          <div className="mt-5 grid gap-2 sm:grid-cols-2">{fixtures.slice(0, 8).map((f: any) => <Link key={f.id} href={`/fixtures/${f.id}`} className="rounded-xl border border-white/[0.06] bg-slate-950/50 p-3 transition hover:border-emerald-400/20"><div className="flex items-center justify-between gap-2"><span className="text-[10px] uppercase tracking-wide text-slate-600">{f.sport}</span><span className="text-[10px] text-slate-600">{f.match_date}</span></div><p className="mt-2 truncate text-sm font-bold text-slate-200">{f.home_team} <span className="font-normal text-slate-600">vs</span> {f.away_team}</p><p className="mt-1 truncate text-[11px] text-slate-600">{f.league}</p></Link>)}</div>
        </div>
        <div className="card">
          <p className="section-kicker">Built for trust</p><h2 className="section-title">A prediction should survive scrutiny.</h2>
          <div className="mt-5 space-y-4 text-sm text-slate-400"><div><p className="font-bold text-slate-200">Before the match</p><p className="mt-1">See the read, confidence, evidence and market context.</p></div><div><p className="font-bold text-slate-200">After the match</p><p className="mt-1">Wins and losses stay visible, with an explanation of what held up.</p></div><div><p className="font-bold text-slate-200">Over time</p><p className="mt-1">Calibration and performance are measured separately from backtests.</p></div></div>
          <Link href="/how-it-works" className="mt-6 inline-flex text-xs font-bold text-emerald-300">How REEDS works →</Link>
        </div>
      </section>

      <section className="mt-12"><div className="flex items-end justify-between"><div><p className="section-kicker">Sports</p><h2 className="section-title">Coverage on the board</h2></div></div><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{(sports.length ? sports : status?.sports || []).map((sport: string) => <Link key={sport} href={`/fixtures?sport=${encodeURIComponent(sport)}`} className="rounded-2xl border border-white/[0.06] bg-slate-900/40 p-4 transition hover:border-emerald-400/20"><p className="text-xs font-bold capitalize text-slate-300">{sport.replaceAll("_", " ")}</p><p className="mt-1 text-[11px] text-slate-600">{sportCounts[sport] || 0} upcoming fixtures</p></Link>)}</div></section>
    </main>
  );
}
