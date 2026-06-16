import Link from "next/link";
import { getPredictionHistory } from "../../../lib/api";

export const dynamic = "force-dynamic";

const DEFAULT_SPORTS = ["soccer", "basketball", "tennis", "cricket", "baseball", "hockey", "handball", "american_football", "volleyball", "rugby", "mma"];

function labelSport(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatDate(dateStr: string) {
  return new Intl.DateTimeFormat("en", { weekday: "short", month: "short", day: "numeric" }).format(new Date(dateStr));
}

export default async function PredictionHistory({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams;
  const picks = await getPredictionHistory(params);
  const sports = Array.from(new Set([...DEFAULT_SPORTS, ...picks.map((p: any) => p.sport)])).filter(Boolean);

  // Stats
  const settled = picks.filter((p: any) => p.result !== "pending");
  const wins = settled.filter((p: any) => p.result === "won").length;
  const losses = settled.filter((p: any) => p.result === "lost").length;
  const hitRate = settled.length > 0 ? ((wins / settled.length) * 100).toFixed(1) : "0.0";

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="badge inline-block">Track record</p>
          <h1 className="mt-4 text-4xl font-black sm:text-5xl">Prediction History</h1>
          <p className="mt-2 max-w-3xl text-slate-400">How our AI predictions performed over the last 7 days.</p>
        </div>
      </div>

      {/* Stats bar */}
      <section className="mt-6 grid gap-4 md:grid-cols-4">
        <div className="card">
          <p className="text-sm text-slate-400">Settled Picks</p>
          <p className="mt-2 text-3xl font-black">{settled.length}</p>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">Wins</p>
          <p className="mt-2 text-3xl font-black text-emerald-300">{wins}</p>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">Losses</p>
          <p className="mt-2 text-3xl font-black text-rose-300">{losses}</p>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">Hit Rate</p>
          <p className="mt-2 text-3xl font-black">{hitRate}%</p>
        </div>
      </section>

      {/* Filters */}
      <form className="mt-6 grid gap-3 rounded-2xl border border-slate-800 bg-slate-900/50 p-4 md:grid-cols-3">
        <select name="sport" defaultValue={params.sport || ""} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">All sports</option>
          {sports.map((x: any) => <option key={x} value={x}>{labelSport(String(x))}</option>)}
        </select>
        <select name="days" defaultValue={params.days || "7"} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="1">Last 24 hours</option>
          <option value="3">Last 3 days</option>
          <option value="7">Last 7 days</option>
          <option value="14">Last 14 days</option>
          <option value="30">Last 30 days</option>
        </select>
        <button className="rounded-xl bg-emerald-400 px-4 py-3 font-bold text-slate-950">Apply filters</button>
      </form>

      {picks.length === 0 && (
        <div className="card mt-8 text-center text-slate-400">
          <b className="text-white">No historical predictions found.</b>
          <p className="mt-2">Predictions appear here after matches are played and settled.</p>
        </div>
      )}

      {/* History list */}
      <div className="mt-8 space-y-3">
        {picks.map((p: any) => {
          const resultBadge = p.result === "won" ? "bg-emerald-500/20 text-emerald-300" : p.result === "lost" ? "bg-rose-500/20 text-rose-300" : "bg-slate-500/20 text-slate-400";
          const resultLabel = p.result === "won" ? "✓ WON" : p.result === "lost" ? "✗ LOST" : "⏳ Pending";
          return (
            <Link key={p.id} href={`/predictions/${p.id}`} className="block rounded-xl border border-slate-800 bg-slate-900/50 p-4 transition hover:border-slate-600 hover:bg-slate-900">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="text-xs uppercase text-slate-500">{labelSport(p.sport)} • {p.league}</p>
                  <h3 className="mt-1 font-bold">{p.home_team} vs {p.away_team}</h3>
                  <p className="mt-1 text-sm">
                    <span className="text-emerald-300">{p.market}: {p.pick}</span>
                    <span className="ml-2 text-slate-500">({p.confidence}% confidence)</span>
                  </p>
                  <p className="mt-0.5 text-xs text-slate-500">{formatDate(p.match_date)}</p>
                </div>
                <div className="flex items-center gap-3">
                  <span className={`rounded-full px-3 py-1 text-xs font-bold ${resultBadge}`}>{resultLabel}</span>
                  <span className="text-xs text-slate-500">{p.risk_level}</span>
                </div>
              </div>
            </Link>
          );
        })}
      </div>
    </main>
  );
}