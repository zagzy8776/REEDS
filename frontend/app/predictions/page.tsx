import { PredictionCard } from "../../components/PredictionCard";
import { getTodayPredictions } from "../../lib/api";

export const dynamic = "force-dynamic";

export default async function Predictions({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams;
  const picks = await getTodayPredictions(params);
  const leagues = Array.from(new Set(picks.map((p: any) => p.league))).filter(Boolean);
  const markets = Array.from(new Set(picks.map((p: any) => p.market))).filter(Boolean);
  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="badge inline-block">Live board</p>
          <h1 className="mt-4 text-4xl font-black">Today’s EDGE Picks</h1>
          <p className="mt-2 max-w-3xl text-slate-400">Football and basketball selections published after model refresh. Filter by league, market, risk, and minimum confidence to focus the card.</p>
        </div>
        <p className="text-sm text-slate-500">{picks.length} public pick{picks.length === 1 ? "" : "s"}</p>
      </div>

      <form className="mt-6 grid gap-3 rounded-2xl border border-slate-800 bg-slate-900/50 p-4 md:grid-cols-5">
        <select name="league" defaultValue={params.league || ""} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">All leagues</option>{leagues.map((x: any) => <option key={x} value={x}>{x}</option>)}
        </select>
        <select name="market" defaultValue={params.market || ""} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">All markets</option>{markets.map((x: any) => <option key={x} value={x}>{x}</option>)}
        </select>
        <select name="risk" defaultValue={params.risk || ""} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">All risk</option><option>Low</option><option>Medium</option><option>High</option>
        </select>
        <input name="min_confidence" type="number" min="0" max="100" defaultValue={params.min_confidence || ""} placeholder="Min confidence" className="rounded-xl border border-slate-800 bg-slate-950 p-3" />
        <button className="rounded-xl bg-emerald-400 px-4 py-3 font-bold text-slate-950">Apply filters</button>
      </form>

      <section className="responsible-note mt-5">
        <b>Responsible disclaimer:</b> Picks are model probabilities, not certainties or financial advice. Use small, predefined stakes and avoid chasing losses.
      </section>

      <div className="mt-8 grid gap-5 md:grid-cols-2">
        {picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="card text-slate-400">No public picks match these filters.</div>}
      </div>
    </main>
  );
}
