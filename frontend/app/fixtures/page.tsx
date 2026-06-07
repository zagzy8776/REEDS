import Link from "next/link";
import { getFixtures } from "../../lib/api";

export const dynamic = "force-dynamic";

function formatDate(value?: string) {
  if (!value) return "TBA";
  return new Intl.DateTimeFormat("en", { weekday: "short", month: "short", day: "numeric" }).format(new Date(value));
}

function formatOdds(value?: number | null) {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

export default async function Fixtures({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams;
  const fixtures = await getFixtures({ ...params, limit: params.limit || "300" });
  const sports = Array.from(new Set(fixtures.map((f: any) => f.sport))).filter(Boolean);
  const leagues = Array.from(new Set(fixtures.map((f: any) => f.league))).filter(Boolean);
  const withOdds = fixtures.filter((f: any) => f.has_odds).length;
  const grouped = fixtures.reduce((acc: Record<string, any[]>, f: any) => {
    const key = String(f.match_date || "TBA");
    acc[key] = acc[key] || [];
    acc[key].push(f);
    return acc;
  }, {});

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <section className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
        <div>
          <p className="badge inline-block">Fixture center</p>
          <h1 className="mt-4 text-4xl font-black sm:text-5xl">All upcoming fixtures from the live feed.</h1>
          <p className="mt-3 max-w-3xl text-slate-300">Browse the matches coming into LOYAL EDGE, check which games already have market prices, then open the AI board or post your own community read.</p>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row">
            <Link href="/predictions" className="rounded-xl bg-emerald-400 px-5 py-3 text-center font-black text-slate-950">View AI picks</Link>
            <Link href="/predictions/submit" className="rounded-xl border border-white/10 bg-white/5 px-5 py-3 text-center font-bold">+ Post community pick</Link>
          </div>
        </div>
        <div className="card">
          <p className="text-sm text-slate-400">Live feed status</p>
          <div className="mt-4 grid grid-cols-3 gap-3 text-center">
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{fixtures.length}</b><br /><span className="text-xs text-slate-500">Fixtures</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{leagues.length}</b><br /><span className="text-xs text-slate-500">Leagues</span></div>
            <div className="rounded-2xl bg-slate-950/70 p-3"><b className="text-2xl text-emerald-300">{withOdds}</b><br /><span className="text-xs text-slate-500">With odds</span></div>
          </div>
        </div>
      </section>

      <form className="mt-6 grid gap-3 rounded-2xl border border-white/10 bg-slate-900/50 p-4 md:grid-cols-4">
        <select name="sport" defaultValue={params.sport || ""} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">All sports</option>{sports.map((x: any) => <option key={x} value={x}>{x}</option>)}
        </select>
        <select name="league" defaultValue={params.league || ""} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
          <option value="">All leagues</option>{leagues.map((x: any) => <option key={x} value={x}>{x}</option>)}
        </select>
        <input name="limit" type="number" min="25" max="500" defaultValue={params.limit || "300"} className="rounded-xl border border-slate-800 bg-slate-950 p-3" />
        <button className="rounded-xl bg-emerald-400 px-4 py-3 font-bold text-slate-950">Refresh board</button>
      </form>

      <section className="mt-8 space-y-6">
        {fixtures.length ? Object.entries(grouped).map(([day, rows]: [string, any[]]) => (
          <div key={day} className="card">
            <div className="flex flex-col gap-2 border-b border-white/10 pb-4 sm:flex-row sm:items-center sm:justify-between">
              <h2 className="text-xl font-black">{formatDate(day)}</h2>
              <p className="text-sm text-slate-400">{rows.length} match{rows.length === 1 ? "" : "es"}</p>
            </div>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              {rows.map((f: any) => (
                <div key={f.id} className="rounded-2xl border border-white/10 bg-slate-950/70 p-4">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <p className="text-xs uppercase tracking-wide text-slate-500">{f.sport} • {f.league}</p>
                      <h3 className="mt-1 text-lg font-black">{f.home_team} vs {f.away_team}</h3>
                    </div>
                    <span className={f.has_odds ? "badge w-fit" : "rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-400"}>{f.has_odds ? "Odds live" : "Odds pending"}</span>
                  </div>
                  <div className="mt-4 grid grid-cols-3 gap-2 text-center text-sm">
                    <div className="rounded-xl bg-slate-900 p-3"><span className="text-slate-500">Home</span><br /><b>{formatOdds(f.home_odds)}</b></div>
                    <div className="rounded-xl bg-slate-900 p-3"><span className="text-slate-500">Draw</span><br /><b>{formatOdds(f.draw_odds)}</b></div>
                    <div className="rounded-xl bg-slate-900 p-3"><span className="text-slate-500">Away</span><br /><b>{formatOdds(f.away_odds)}</b></div>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2 text-xs font-bold">
                    <Link href={`/predictions?league=${encodeURIComponent(f.league)}`} className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-emerald-200">AI reads</Link>
                    <Link href={`/predictions/submit`} className="rounded-full border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200">+ Community pick</Link>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )) : (
          <div className="card border-dashed border-emerald-400/30 bg-emerald-400/5 text-slate-300">
            <h2 className="text-2xl font-black text-white">No fixtures are showing yet.</h2>
            <p className="mt-2">Once the backend live ingestion runs with your API keys on Render, upcoming fixtures will appear here automatically.</p>
          </div>
        )}
      </section>
    </main>
  );
}