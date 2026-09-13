import { PredictionCard } from "../../components/PredictionCard";
import { getTodayPredictions } from "../../lib/api";

export const dynamic = "force-dynamic";

const DEFAULT_SPORTS = ["soccer", "basketball", "tennis", "cricket", "american_football", "baseball", "hockey", "rugby", "volleyball", "handball", "mma", "motorsport"];

function labelSport(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function getTopPicks(picks: any[]): any[] {
  if (!picks.length) return [];
  // Find the pick with highest confidence per unique fixture+market combo
  // Then return the top ~3 by confidence
  const sorted = [...picks].sort((a: any, b: any) => b.confidence - a.confidence);
  return sorted.slice(0, Math.min(3, Math.ceil(picks.length * 0.25)));
}

export default async function Predictions({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams;
  const picks = await getTodayPredictions(params);
  const sports = Array.from(new Set([...DEFAULT_SPORTS, ...picks.map((p: any) => p.sport)])).filter(Boolean);
  const leagues = Array.from(new Set(picks.map((p: any) => p.league))).filter(Boolean);
  const markets = Array.from(new Set(picks.map((p: any) => p.market))).filter(Boolean);
  const topPicks = getTopPicks(picks);
  const topIds = new Set(topPicks.map((p: any) => p.id));

  // Sort: top picks first, then rest
  const sortedPicks = [...picks].sort((a: any, b: any) => {
    const aTop = topIds.has(a.id) ? 0 : 1;
    const bTop = topIds.has(b.id) ? 0 : 1;
    if (aTop !== bTop) return aTop - bTop;
    return b.confidence - a.confidence;
  });

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="badge inline-block">Live board</p>
          <h1 className="mt-4 text-4xl font-black sm:text-5xl">Today&rsquo;s AI picks</h1>
          <p className="mt-2 max-w-3xl text-slate-400">Filter the board, open a pick, then compare it with what the community is saying.</p>
        </div>
        {picks.length > 0 && (
          <p className="text-sm text-slate-500">{picks.length} public pick{picks.length === 1 ? "" : "s"}</p>
        )}
      </div>

      <form className="mt-6 grid gap-3 rounded-2xl border border-slate-800 bg-slate-900/50 p-4 md:grid-cols-6">
        <select name="sport" defaultValue={params.sport || ""} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">All sports</option>{sports.map((x: any) => <option key={x} value={x}>{labelSport(String(x))}</option>)}
        </select>
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
        <b>Play smart:</b> Picks are not guaranteed. Keep stakes small and do not chase losses.
      </section>

      {/* Top Picks Banner */}
      {topPicks.length > 0 && (
        <div className="mt-6 rounded-2xl border border-amber-400/30 bg-amber-400/5 p-4">
          <div className="flex items-center gap-2 text-amber-300">
            <span className="text-xl">&#9733;</span>
            <span className="font-black">Top Pick{picks.length > 1 ? "s" : ""}</span>
            <span className="text-sm text-slate-400">&mdash; Highest confidence picks today</span>
          </div>
        </div>
      )}

      <div className="mt-8 grid gap-5 md:grid-cols-2">
        {sortedPicks.length ? sortedPicks.map((p: any) => (
          <div key={p.id} className={topIds.has(p.id) ? "relative" : ""}>
            {topIds.has(p.id) && (
              <div className="absolute -top-2 -right-2 z-10 rounded-full bg-amber-400 px-3 py-1 text-xs font-black text-slate-950 shadow-lg shadow-amber-400/30">
                &#9733; Top Pick
              </div>
            )}
            <PredictionCard p={p} />
          </div>
        )) : (
          <div className="card text-slate-400">
            <b className="text-white">No AI picks showing yet.</b>
            <p className="mt-2">Clear the filters, check back soon, or post your own community pick.</p>
            <a className="mt-4 inline-flex rounded-xl bg-emerald-400 px-4 py-2 text-sm font-black text-slate-950" href="/predictions/submit">+ Post community pick</a>
          </div>
        )}
      </div>
    </main>
  );
}