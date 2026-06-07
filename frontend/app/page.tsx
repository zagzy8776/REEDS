import Link from "next/link";
import { PredictionCard } from "../components/PredictionCard";
import { getTodayPredictions } from "../lib/api";

export const dynamic = "force-dynamic";

export default async function Home() {
  const picks = (await getTodayPredictions()).slice(0, 3);
  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <section className="grid gap-8 md:grid-cols-2 md:items-center">
        <div>
          <p className="badge inline-block">AI picks + community signal</p>
          <h1 className="mt-5 text-4xl font-black leading-tight sm:text-5xl">A live sports prediction network — AI picks, tipster posts, likes, streaks, and daily action.</h1>
          <p className="mt-5 text-slate-300">Open the board, see today’s strongest AI reads, compare community opinions, post your own pick, and track who is actually winning over time.</p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Link className="rounded-xl bg-emerald-400 px-5 py-3 font-bold text-slate-950" href="/combo">Get 3-Leg Combo</Link>
            <Link className="rounded-xl border border-slate-700 px-5 py-3 font-bold" href="/predictions">View Picks</Link>
            <Link className="rounded-xl border border-emerald-400/30 bg-emerald-400/10 px-5 py-3 font-bold text-emerald-200" href="/predictions/submit">+ Post Pick</Link>
          </div>
          <div className="mt-8 grid gap-3 text-sm text-slate-300 sm:grid-cols-3">
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><b className="text-white">Football</b><br />1X2, goals, BTTS, score bands</div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><b className="text-white">Basketball</b><br />spread and points-total leans</div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><b className="text-white">Combos</b><br />3-leg slips filtered by EDGE score</div>
          </div>
        </div>
        <div className="card">
          <h2 className="text-xl font-bold">Today’s Top EDGE</h2>
          <div className="mt-4 space-y-3">{picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="rounded-2xl border border-dashed border-emerald-400/30 bg-emerald-400/5 p-5"><p className="font-bold text-emerald-200">The AI board is warming up.</p><p className="mt-2 text-sm text-slate-400">Post a community pick while the next AI refresh runs, or check the leaderboard for active tipsters.</p><Link href="/predictions/submit" className="mt-4 inline-flex rounded-xl bg-emerald-400 px-4 py-2 text-sm font-black text-slate-950">+ Add first action</Link></div>}</div>
        </div>
      </section>
      <section className="mt-12 grid gap-5 md:grid-cols-3">
        <div className="card"><h3 className="font-bold text-emerald-300">Train/Serve Integrity</h3><p className="mt-2 text-sm text-slate-300">Live predictions now rebuild team form, Elo, league strength, and odds signals instead of relying on blind defaults.</p></div>
        <div className="card"><h3 className="font-bold text-emerald-300">Ledger-Based Picks</h3><p className="mt-2 text-sm text-slate-300">Published predictions are versioned so old records can be audited instead of silently overwritten.</p></div>
        <div className="card"><h3 className="font-bold text-emerald-300">Risk Filtered</h3><p className="mt-2 text-sm text-slate-300">Every pick is shown with confidence, market type, risk level, and responsible probability wording.</p></div>
      </section>
      <section className="mt-12 card">
        <p className="badge inline-block">Methodology</p>
        <h2 className="mt-4 text-3xl font-black">What EDGE Score means</h2>
        <p className="mt-3 text-slate-300">EDGE Score is a model confidence signal blended with form, scoring profile, and market context. It is not a guarantee. The platform tracks backtests, settled public picks, confidence buckets, and model versions so performance can be judged over time.</p>
      </section>
    </main>
  );
}
