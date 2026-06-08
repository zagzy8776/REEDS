import Link from "next/link";
import { getFixture } from "../../../lib/api";
import { PredictionCard } from "../../../components/PredictionCard";

export const dynamic = "force-dynamic";

function formatDate(value?: string) {
  if (!value) return "TBA";
  return new Intl.DateTimeFormat("en", { dateStyle: "full" }).format(new Date(value));
}

function formatOdds(value?: number | null) {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

export default async function FixtureDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const data = await getFixture(id);
  if (!data?.fixture) {
    return <main className="mx-auto max-w-4xl px-6 py-10"><Link className="text-emerald-300" href="/fixtures">← Fixtures</Link><div className="card mt-6">Fixture unavailable.</div></main>;
  }
  const f = data.fixture;
  const picks = data.predictions || [];
  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <Link className="text-sm font-bold text-emerald-300" href="/fixtures">← Match center</Link>
      <section className="card mt-6">
        <p className="badge inline-block">Fixture intelligence</p>
        <h1 className="mt-4 text-4xl font-black">{f.home_team} vs {f.away_team}</h1>
        <p className="mt-2 text-slate-400">{f.sport} • {f.league} • {formatDate(f.match_date)} • {String(f.api_status || "pending").replaceAll("_", " ")}</p>
        <div className="mt-6 grid gap-3 md:grid-cols-4">
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Score</span><br /><b>{f.home_score ?? "—"} - {f.away_score ?? "—"}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Home odds</span><br /><b>{formatOdds(f.home_odds)}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Draw odds</span><br /><b>{formatOdds(f.draw_odds)}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Away odds</span><br /><b>{formatOdds(f.away_odds)}</b></div>
        </div>
      </section>
      <section className="mt-8">
        <div className="flex items-end justify-between gap-3">
          <div><p className="badge inline-block">AI reads</p><h2 className="mt-3 text-2xl font-black">Model angles for this fixture</h2></div>
          <p className="text-sm text-slate-500">{picks.length} active pick{picks.length === 1 ? "" : "s"}</p>
        </div>
        <div className="mt-5 grid gap-5 md:grid-cols-2">
          {picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="card text-slate-400">AI reads will appear after the next prediction refresh.</div>}
        </div>
      </section>
      <section className="mt-8 card">
        <h2 className="text-xl font-bold">Community sentiment</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {(data.community?.consensus || []).length ? data.community.consensus.map((c: any) => <div key={c.pick} className="rounded-xl bg-slate-950 p-4"><b>{c.pick}</b><p className="text-emerald-300">{c.percent}%</p><p className="text-xs text-slate-500">{c.count} picks</p></div>) : <p className="text-sm text-slate-400">No community picks yet.</p>}
        </div>
      </section>
    </main>
  );
}