import Link from "next/link";
import { PredictionCard } from "../../../components/PredictionCard";
import { getPredictionHistory, getStats } from "../../../lib/api";

export const dynamic = "force-dynamic";

function resultClass(result: string) {
  if (result === "won") return "border-emerald-400/30 bg-emerald-400/10 text-emerald-200";
  if (result === "lost") return "border-rose-400/30 bg-rose-400/10 text-rose-200";
  return "border-sky-400/30 bg-sky-400/10 text-sky-200";
}

function resultLabel(result: string) {
  if (result === "won") return "Won";
  if (result === "lost") return "Lost";
  return "Pending";
}

export default async function PredictionHistory({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams;
  const [history, stats] = await Promise.all([
    getPredictionHistory({ days: params.days || "14", limit: params.limit || "100", sport: params.sport || "" }),
    getStats(),
  ]);
  const results = stats.results || { settled_picks: 0, wins: 0, losses: 0, hit_rate: 0 };
  const sports = Array.from(new Set(history.map((p: any) => p.sport))).filter(Boolean);

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <section className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
        <div>
          <p className="badge inline-block">Public track record</p>
          <h1 className="mt-4 text-4xl font-black sm:text-5xl">Every AI pick stays visible.</h1>
          <p className="mt-3 max-w-3xl text-slate-300">
            Wins, losses, and pending picks are shown together so the record builds in public. No winner-only screenshots.
          </p>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row">
            <Link href="/predictions?min_confidence=65" className="rounded-xl bg-emerald-400 px-5 py-3 text-center font-black text-slate-950">High-confidence board</Link>
            <Link href="/stats" className="rounded-xl border border-white/10 bg-white/5 px-5 py-3 text-center font-bold">Full stats</Link>
          </div>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">Settled public picks</p>
          <div className="mt-4 grid grid-cols-2 gap-3 text-center sm:grid-cols-4 lg:grid-cols-2">
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{results.settled_picks || 0}</b><br /><span className="text-xs text-slate-500">Settled</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{results.hit_rate || 0}%</b><br /><span className="text-xs text-slate-500">Hit rate</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{results.wins || 0}</b><br /><span className="text-xs text-slate-500">Wins</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-rose-300">{results.losses || 0}</b><br /><span className="text-xs text-slate-500">Losses</span></div>
          </div>
        </div>
      </section>

      <section className="responsible-note mt-6">
        <b>Transparent by design:</b> A young board may show pending picks before settled history exists. That is normal and more honest than inventing proof.
      </section>

      <form className="mt-6 grid gap-3 rounded-2xl border border-white/10 bg-slate-900/50 p-4 md:grid-cols-4">
        <select name="sport" defaultValue={params.sport || ""} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">All sports</option>{sports.map((x: any) => <option key={x} value={x}>{String(x).replaceAll("_", " ")}</option>)}
        </select>
        <select name="days" defaultValue={params.days || "14"} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="7">Last 7 days</option>
          <option value="14">Last 14 days</option>
          <option value="30">Last 30 days</option>
          <option value="90">Last 90 days</option>
        </select>
        <input name="limit" type="number" min="25" max="200" defaultValue={params.limit || "100"} className="rounded-xl border border-slate-800 bg-slate-950 p-3" />
        <button className="rounded-xl bg-emerald-400 px-4 py-3 font-bold text-slate-950">Apply filters</button>
      </form>

      <section className="mt-8 grid gap-5 md:grid-cols-2">
        {history.length ? history.map((p: any) => (
          <div key={`${p.id}-${p.version || 1}`} className="relative">
            <div className={`absolute -right-2 -top-2 z-10 rounded-full border px-3 py-1 text-xs font-black ${resultClass(p.result)}`}>
              {resultLabel(p.result)}
            </div>
            <PredictionCard p={p} />
          </div>
        )) : (
          <div className="card border-dashed border-emerald-400/30 bg-emerald-400/5 text-slate-300 md:col-span-2">
            <h2 className="text-2xl font-black text-white">No public history yet.</h2>
            <p className="mt-2">Once the AI board publishes picks and fixtures settle, the full record will appear here.</p>
            <Link href="/predictions" className="mt-4 inline-flex rounded-xl bg-emerald-400 px-4 py-2 text-sm font-black text-slate-950">View today’s picks</Link>
          </div>
        )}
      </section>
    </main>
  );
}