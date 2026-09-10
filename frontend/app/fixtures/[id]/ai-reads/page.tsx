import Link from "next/link";
import { getAIReads } from "../../../../lib/api";
import { PredictionCard } from "../../../../components/PredictionCard";

export const dynamic = "force-dynamic";

function formatDate(value?: string) {
  if (!value) return "TBA";
  return new Intl.DateTimeFormat("en", { dateStyle: "full" }).format(new Date(value));
}

function formatSport(value?: string) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function Timeline({ intelligence }: { intelligence?: any }) {
  const timeline = Array.isArray(intelligence?.timeline) ? intelligence.timeline : [];
  const revisions = Array.isArray(intelligence?.revisions) ? intelligence.revisions : [];
  const market = intelligence?.market;
  if (!timeline.length && !revisions.length && !market) return null;
  const stepTone: Record<string, string> = {
    kickoff: "text-sky-300",
    model_analysis: "text-emerald-300",
    odds_snapshot: "text-amber-300",
    publication: "text-emerald-400",
    live_event: "text-rose-300",
    final: "text-violet-300",
  };
  const marketLine = market?.fixture_line || {};
  const cols = [
    { key: "home_odds", label: "Home" },
    { key: "draw_odds", label: "Draw" },
    { key: "away_odds", label: "Away" },
  ];
  return (
    <section className="mt-10 grid gap-5 md:grid-cols-2">
      {timeline.length > 0 && (
        <div className="card">
          <h2 className="text-xl font-bold">Match intelligence timeline</h2>
          <p className="mt-1 text-sm text-slate-400">Every stage REEDS tracked for this fixture, in order.</p>
          <ol className="mt-4 space-y-3">
            {timeline.slice(0, 16).map((t: any, i: number) => (
              <li key={i} className="flex gap-3 text-sm">
                <span className={`mt-0.5 shrink-0 font-bold ${stepTone[t.type] || "text-slate-300"}`}>•</span>
                <div>
                  <p className="font-bold text-slate-200">{t.title}</p>
                  {t.detail ? <p className="text-xs text-slate-400">{t.detail}</p> : null}
                  <p className="text-[11px] text-slate-600">{t.ts || ""}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="space-y-5">
        {revisions.length > 0 && (
          <div className="card">
            <h2 className="text-xl font-bold">Read revisions</h2>
            <p className="mt-1 text-sm text-slate-400">Superseded reads are kept visible — REEDS changing its mind is never hidden.</p>
            <div className="mt-4 space-y-3">
              {revisions.slice(0, 4).map((rev: any, i: number) => (
                <div key={i} className={`rounded-xl border p-3 ${rev.latest ? "border-emerald-400/25 bg-emerald-400/5" : "border-slate-800 bg-slate-950/50"}`}>
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-bold text-slate-200">{rev.market}</p>
                    {rev.latest ? <span className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2 py-0.5 text-[10px] text-emerald-300">LATEST</span> : null}
                  </div>
                  <p className="mt-1 text-xs text-slate-400">
                    {(rev.versions || []).map((v: any) => v.pick).filter(Boolean).join(" → ")}
                  </p>
                  <div className="mt-2 space-y-1">
                    {(rev.versions || []).map((v: any, j: number) => (
                      <p key={j} className="text-[11px] text-slate-500">
                        v{v.version} • {v.pick} • {v.confidence?.toFixed(1)}% • {v.status}
                        {j > 0 && v.superseded_at ? ` • superseded ${v.superseded_at}` : ""}
                      </p>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {market && (
          <div className="card">
            <h2 className="text-xl font-bold">Market movement</h2>
            <p className="mt-1 text-sm text-slate-400">Opening → current quotes vs the fixture line. Prices move; both sides stay visible.</p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead><tr className="text-slate-400"><th>Side</th><th>Opening</th><th>Current</th><th>Closing</th></tr></thead>
                <tbody>
                  {cols.map(({ key, label }) => (
                    <tr key={key} className="border-t border-slate-800">
                      <td className="py-2 font-bold text-slate-200">{label}</td>
                      <td>{market.opening?.[key] ?? "—"}</td>
                      <td className="font-bold text-slate-100">{market.current?.[key] ?? (marketLine[key] ?? "—")}</td>
                      <td>{market.closing?.[key] ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {market.pick_side?.available && (
              <p className="mt-3 text-xs text-slate-400">
                Pick side market {market.pick_side.odds} → implied {market.pick_side.market_probability}% vs model {market.pick_side.model_probability}% (edge {market.pick_side.edge_pp >= 0 ? "+" : ""}{market.pick_side.edge_pp}pp)
              </p>
            )}
            {market.note ? <p className="mt-2 text-xs text-slate-500">{market.note}</p> : null}
          </div>
        )}
      </div>
    </section>
  );
}

export default async function FixtureAIReads({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const data = await getAIReads(id);

  if (!data?.fixture) {
    return <main className="mx-auto max-w-4xl px-6 py-10"><Link className="text-emerald-300" href="/fixtures">← Fixtures</Link><div className="card mt-6">AI Reads are unavailable for this fixture.</div></main>;
  }

  const f = data.fixture;
  const picks = Array.isArray(data.predictions) ? data.predictions : [];
  const sources = Array.isArray(f.provider_sources) ? f.provider_sources : [];

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <div className="flex flex-wrap gap-3">
        <Link href={`/fixtures/${id}`} className="text-sm font-bold text-emerald-300">← Match Hub</Link>
        <span className="text-slate-700">•</span>
        <Link href="/fixtures" className="text-sm font-bold text-sky-300">All fixtures</Link>
      </div>

      <section className="card mt-6">
        <p className="badge inline-block">AI Reads</p>
        <h1 className="mt-4 text-3xl font-black sm:text-4xl">{f.home_team} vs {f.away_team}</h1>
        <p className="mt-2 text-slate-400">{formatSport(f.sport)} • {f.league} • {formatDate(f.match_date)}</p>
        {f.status ? (
          <p className="mt-2 text-sm text-slate-300">
            {f.home_score != null && f.away_score != null
              ? <>Score {f.home_score} : {f.away_score}{f.is_live ? <> • {f.status} {f.elapsed ? `${f.elapsed}'` : ""}</> : ""}</>
              : <>Status: {f.status}</>}
          </p>
        ) : null}
        {sources.length ? <div className="mt-4 flex flex-wrap gap-2">{sources.map((source: string) => <span key={source} className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300">{source}</span>)}</div> : null}
      </section>

      {data.status === "preparing" ? (
        <section className="card mt-8 border border-amber-400/20 bg-amber-400/5">
          <h2 className="text-2xl font-black text-white">Analysis is being prepared</h2>
          <p className="mt-2 text-slate-400">REEDS has queued this exact fixture for analysis. Refresh shortly; the page will automatically show the reads when they are ready.</p>
          <div className="mt-5 flex flex-wrap gap-3">
            <Link href={`/fixtures/${id}`} className="rounded-xl bg-emerald-400 px-4 py-2 font-black text-slate-950">Back to Match Hub</Link>
            <Link href="/predictions" className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-bold">View current AI board</Link>
          </div>
        </section>
      ) : (
        <section className="mt-8">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="badge inline-block">{data.status === "draft" ? "Draft analysis" : "Published analysis"}</p>
              <h2 className="mt-3 text-2xl font-black">What REEDS reads for this match</h2>
            </div>
            <span className="text-sm text-slate-500">{picks.length} active read{picks.length === 1 ? "" : "s"}</span>
          </div>
          {data.status === "draft" && picks.length ? (
            <div className="mt-4 rounded-xl border border-amber-400/25 bg-amber-400/5 p-4 text-slate-300">
              <b className="text-amber-300">Draft analysis — not yet public.</b> These reads are
              generated for this exact match but are provisional until empirical evidence
              unlocks tracked publication.
            </div>
          ) : null}
          <div className="mt-5 grid gap-5 md:grid-cols-2">
            {picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="card text-slate-400">No published AI reads are available for this fixture yet.</div>}
          </div>
          <Timeline intelligence={data.intelligence} />
        </section>
      )}

      {data.responsible_note ? <section className="responsible-note mt-8">{data.responsible_note}</section> : null}
    </main>
  );
}