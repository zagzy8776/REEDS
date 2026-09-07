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
        {sources.length ? <div className="mt-4 flex flex-wrap gap-2">{sources.map((source: string) => <span key={source} className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300">{source}</span>)}</div> : null}
      </section>

      {data.status === "preparing" ? (
        <section className="card mt-8 border border-amber-400/20 bg-amber-400/5">
          <h2 className="text-2xl font-black text-white">Analysis is being prepared</h2>
          <p className="mt-2 text-slate-400">REEDS has queued this exact fixture for analysis. Refresh shortly; the page will automatically show the published reads when they are ready.</p>
          <div className="mt-5 flex flex-wrap gap-3">
            <Link href={`/fixtures/${id}`} className="rounded-xl bg-emerald-400 px-4 py-2 font-black text-slate-950">Back to Match Hub</Link>
            <Link href="/predictions" className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-bold">View current AI board</Link>
          </div>
        </section>
      ) : (
        <section className="mt-8">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div><p className="badge inline-block">Published analysis</p><h2 className="mt-3 text-2xl font-black">What REEDS reads for this match</h2></div>
            <span className="text-sm text-slate-500">{picks.length} active read{picks.length === 1 ? "" : "s"}</span>
          </div>
          <div className="mt-5 grid gap-5 md:grid-cols-2">
            {picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="card text-slate-400">No published AI reads are available for this fixture yet.</div>}
          </div>
        </section>
      )}

      {data.responsible_note ? <section className="responsible-note mt-8">{data.responsible_note}</section> : null}
    </main>
  );
}
