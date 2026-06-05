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
          <p className="badge inline-block">Premium Football & Basketball Intel</p>
          <h1 className="mt-5 text-5xl font-black leading-tight">Daily value picks, risk-rated combos, and sharp match signals.</h1>
          <p className="mt-5 text-slate-300">LOYAL EDGE studies historical results, team form, scoring patterns, home/away strength, and market movement to publish clean picks customers can understand.</p>
          <div className="mt-8 flex gap-3">
            <Link className="rounded-xl bg-emerald-400 px-5 py-3 font-bold text-slate-950" href="/combo">Get 3-Leg Combo</Link>
            <Link className="rounded-xl border border-slate-700 px-5 py-3 font-bold" href="/predictions">View Picks</Link>
          </div>
          <div className="mt-8 grid gap-3 text-sm text-slate-300 sm:grid-cols-3">
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><b className="text-white">Football</b><br />1X2, goals, BTTS, score bands</div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><b className="text-white">Basketball</b><br />spread and points-total leans</div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4"><b className="text-white">Combos</b><br />3-leg slips filtered by EDGE score</div>
          </div>
        </div>
        <div className="card">
          <h2 className="text-xl font-bold">Today’s Top EDGE</h2>
          <div className="mt-4 space-y-3">{picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <p className="text-sm text-slate-400">Today’s picks are being prepared. Check back after the next model refresh.</p>}</div>
        </div>
      </section>
      <section className="mt-12 grid gap-5 md:grid-cols-3">
        <div className="card"><h3 className="font-bold text-emerald-300">Data Trained</h3><p className="mt-2 text-sm text-slate-300">Historical football and basketball records feed the model before public picks are released.</p></div>
        <div className="card"><h3 className="font-bold text-emerald-300">Owner Controlled</h3><p className="mt-2 text-sm text-slate-300">You control when the model trains, refreshes, and publishes customer-facing predictions.</p></div>
        <div className="card"><h3 className="font-bold text-emerald-300">Risk Filtered</h3><p className="mt-2 text-sm text-slate-300">Every pick is shown with confidence, market type, and risk level — no clutter.</p></div>
      </section>
    </main>
  );
}
