import Link from "next/link";
import { getAIReads } from "../../../lib/api";
import { PredictionCard } from "../../../components/PredictionCard";

export const dynamic = "force-dynamic";

function formatDate(value?: string) {
  if (!value) return "TBA";
  return new Intl.DateTimeFormat("en", { dateStyle: "full" }).format(new Date(value));
}

function formatOdds(value?: number | null) {
  return typeof value === "number" ? value.toFixed(2) : "-";
}

function formatSport(value?: string) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

export default async function FixtureDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const data = await getAIReads(id);
  if (!data?.fixture) {
    return <main className="mx-auto max-w-4xl px-6 py-10"><Link className="text-emerald-300" href="/fixtures">← Fixtures</Link><div className="card mt-6">Fixture unavailable.</div></main>;
  }

  const f = data.fixture;
  const picks = Array.isArray(data.predictions) ? data.predictions : [];
  const providerSources = Array.isArray(f.provider_sources) ? f.provider_sources : [];

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <div className="flex flex-wrap items-center gap-3">
        <Link className="text-sm font-bold text-emerald-300" href="/fixtures">← Match center</Link>
        <span className="text-slate-700">•</span>
        <Link className="text-sm font-bold text-sky-300" href={`/fixtures/${id}/ai-reads`}>AI Reads</Link>
      </div>

      <section className="card mt-6">
        <p className="badge inline-block">Match Hub</p>
        <h1 className="mt-4 break-words text-3xl font-black sm:text-4xl">{f.home_team} vs {f.away_team}</h1>
        <p className="mt-2 text-slate-400">{formatSport(f.sport)} • {f.league} • {formatDate(f.match_date)} • {String(f.api_status || "pending").replaceAll("_", " ")}</p>

        <div className="mt-6 grid gap-3 md:grid-cols-4">
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Score</span><br /><b>{f.home_score ?? "-"} - {f.away_score ?? "-"}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Home odds</span><br /><b>{formatOdds(f.home_odds)}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Draw odds</span><br /><b>{formatOdds(f.draw_odds)}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Away odds</span><br /><b>{formatOdds(f.away_odds)}</b></div>
        </div>

        {providerSources.length ? (
          <div className="mt-5 rounded-xl border border-white/10 bg-white/5 p-4">
            <p className="text-xs uppercase tracking-wide text-slate-500">Source provenance</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {providerSources.map((source: string) => <span key={source} className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300">{source}</span>)}
            </div>
          </div>
        ) : null}
      </section>

      <section className="mt-8">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div><p className="badge inline-block">AI Reads</p><h2 className="mt-3 text-2xl font-black">Analysis for this exact match</h2></div>
          <p className="text-sm text-slate-500">{data.status === "ready" ? `${picks.length} active read${picks.length === 1 ? "" : "s"}` : "Analysis status: preparing"}</p>
        </div>

        {data.status === "preparing" ? (
          <div className="card mt-5 border border-amber-400/20 bg-amber-400/5 text-slate-300">
            <h3 className="text-xl font-black text-white">AI analysis is being prepared.</h3>
            <p className="mt-2">This match has been queued for the Render prediction worker. Refresh this page shortly to see the finished analysis.</p>
          </div>
        ) : (
          <div className="mt-5">
            {data.status === "draft" ? (
              <div className="mb-4 rounded-xl border border-amber-400/25 bg-amber-400/5 p-4 text-slate-300">
                <b className="text-amber-300">Draft analysis — not yet public.</b>{" "}
                These reads are generated for this exact match but are provisional until empirical evidence unlocks tracked publication.
              </div>
            ) : null}
            <div className="grid gap-5 md:grid-cols-2">
              {picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="card text-slate-400">No published AI reads are available for this match yet.</div>}
            </div>
          </div>
        )}
      </section>

      {data.responsible_note ? <section className="responsible-note mt-8">{data.responsible_note}</section> : null}
    </main>
  );
}
