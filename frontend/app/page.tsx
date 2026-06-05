import Link from "next/link";
import { PredictionCard } from "../components/PredictionCard";
import { getTodayPredictions } from "../lib/api";

export const dynamic = "force-dynamic";

export default async function Home() {
  const picks = (await getTodayPredictions()).slice(0, 3);
  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <section className="grid gap-8 md:grid-cols-2 md:items-center">
        <div>
          <p className="badge inline-block">Professional Match Analysis</p>
          <h1 className="mt-5 text-5xl font-black leading-tight">Value picks powered by the LOYAL EDGE rating system.</h1>
          <p className="mt-5 text-slate-300">Form trends, match history, goal patterns, market movement, and risk filters combined into clean daily predictions.</p>
          <div className="mt-8 flex gap-3">
            <Link className="rounded-xl bg-emerald-400 px-5 py-3 font-bold text-slate-950" href="/combo">Get 3-Leg Combo</Link>
            <Link className="rounded-xl border border-slate-700 px-5 py-3 font-bold" href="/predictions">View Picks</Link>
          </div>
        </div>
        <div className="card">
          <h2 className="text-xl font-bold">Today’s Top EDGE</h2>
          <div className="mt-4 space-y-3">{picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <p className="text-sm text-slate-400">Predictions will appear after backend seed/train is complete.</p>}</div>
        </div>
      </section>
    </main>
  );
}
