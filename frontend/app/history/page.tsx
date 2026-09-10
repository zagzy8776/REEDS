import Link from "next/link";
import { PredictionCard } from "../../components/PredictionCard";
import { getPredictionHistoryPaginated, getStats } from "../../lib/api";

export const dynamic = "force-dynamic";

const RESULT_TONE: Record<string, string> = {
  won: "text-emerald-300 border-emerald-400/40",
  lost: "text-rose-300 border-rose-400/40",
  pending: "text-sky-300 border-sky-400/30",
};

async function buildHref(searchParams: Record<string, string | undefined>, patch: Record<string, string | undefined>) {
  const next = { ...searchParams, ...patch };
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(next)) {
    if (v) qs.set(k, v);
  }
  return `/history${qs.size ? `?${qs.toString()}` : ""}`;
}

export default async function TrackRecord({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams;
  const page = Number(params.page || "1");
  const [data, stats] = await Promise.all([
    getPredictionHistoryPaginated({
      days: params.days || "45",
      limit: "20",
      page: String(page),
      sport: params.sport || "",
      league: params.league || "",
      market: params.market || "",
      risk: params.risk || "",
      result: params.result || "",
      q: params.q || "",
    }),
    getStats(),
  ]);
  const items = Array.isArray(data.items) ? data.items : [];
  const results = stats.results || { settled_picks: 0, wins: 0, losses: 0, hit_rate: 0 };
  const prevHref = await buildHref(params, { page: String(Math.max(1, page - 1)) });
  const nextHref = await buildHref(params, { page: String(page + 1) });
  const recordChips = [
    { label: "LIVE RECORD", t: "Published reads that actually settled in real time. This is the number that matters.", tone: "text-emerald-300" },
    { label: "BACKTEST", t: "Simulated on historical data before going live. Not proof of live picks.", tone: "text-sky-300" },
    { label: "HISTORICAL EVALUATION", t: "Walk-forward out-of-sample evaluation rows. Also not the live record.", tone: "text-violet-300" },
  ];

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <section className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
        <div>
          <p className="badge inline-block">Public track record</p>
          <h1 className="mt-4 text-4xl font-black sm:text-5xl">Every read stays on the record.</h1>
          <p className="mt-3 max-w-3xl text-slate-300">
            Wins, losses, and pending picks are shown together. Filter by market, risk, and result — the numbers are not cherry-picked.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link href="/performance" className="rounded-xl bg-emerald-400 px-5 py-3 font-black text-slate-950">Performance center</Link>
            <Link href="/how-it-works" className="rounded-xl border border-white/10 bg-white/5 px-5 py-3 font-bold">How the record works</Link>
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
        <b>How the categories stay honest:</b>{" "}
        {recordChips.map((chip) => (
          <span key={chip.label} className="mr-3 inline-flex items-center gap-1"><b className={chip.tone}>{chip.label}</b> <span className="text-sky-200/80">— {chip.t}</span></span>
        ))}
      </section>

      <form className="mt-6 grid gap-3 rounded-2xl border border-white/10 bg-slate-900/50 p-4 md:grid-cols-6">
        <input name="q" defaultValue={params.q || ""} placeholder="Search teams…" className="rounded-xl border border-slate-800 bg-slate-950 p-3 md:col-span-2" />
        <select name="market" defaultValue={params.market || ""} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">All markets</option>
          {["1X2", "Double Chance", "Over/Under 2.5", "BTTS", "Total Points", "Spread"].map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select name="risk" defaultValue={params.risk || ""} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">All risk</option>
          <option value="low">Low risk</option>
          <option value="medium">Medium risk</option>
          <option value="high">High risk</option>
        </select>
        <select name="result" defaultValue={params.result || ""} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">All results</option>
          <option value="won">Won</option>
          <option value="lost">Lost</option>
          <option value="pending">Pending</option>
        </select>
        <select name="days" defaultValue={params.days || "45"} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
          <option value="45">Last 45 days</option>
          <option value="90">Last 90 days</option>
        </select>
        <button className="rounded-xl bg-emerald-400 px-4 py-3 font-bold text-slate-950 md:col-span-6 lg:col-span-1">Apply</button>
      </form>

      <section className="mt-8 space-y-4">
        {items.length ? items.map((p: any) => (
          <div key={`${p.id}-${p.version || 1}`} className="relative">
            <div className={`absolute -right-2 -top-2 z-10 rounded-full border bg-slate-950 px-3 py-1 text-xs font-black ${RESULT_TONE[p.result] || RESULT_TONE.pending}`}>
              {p.result === "won" ? "WON" : p.result === "lost" ? "LOST" : "PENDING"}
            </div>
            <PredictionCard p={p} />
          </div>
        )) : (
          <div className="card border-dashed border-emerald-400/30 bg-emerald-400/5 text-slate-300">
            <h2 className="text-2xl font-black text-white">No reads match those filters.</h2>
            <p className="mt-2">Once the AI board publishes reads and fixtures settle, the full public record will appear here.</p>
            <Link href="/predictions" className="mt-4 inline-flex rounded-xl bg-emerald-400 px-4 py-2 text-sm font-black text-slate-950">View today's reads</Link>
          </div>
        )}
      </section>

      <div className="mt-8 flex items-center justify-between text-sm">
        <Link href={prevHref} className={`rounded-lg border border-white/10 px-4 py-2 font-bold ${page <= 1 ? "pointer-events-none opacity-40" : ""}`}>← Previous</Link>
        <p className="text-slate-500">{data.total || 0} reads in this filter set</p>
        <Link href={nextHref} className={`rounded-lg border border-white/10 px-4 py-2 font-bold ${!data.has_more ? "pointer-events-none opacity-40" : ""}`}>Next →</Link>
      </div>
    </main>
  );
}