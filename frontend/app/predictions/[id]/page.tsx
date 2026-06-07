import Link from "next/link";
import { getPrediction } from "../../../lib/api";

export const dynamic = "force-dynamic";

function formatDate(value?: string) {
  if (!value) return "TBA";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
}

function formatOdds(value?: number | null) {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

export default async function PredictionDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const prediction = await getPrediction(id);

  if (!prediction) {
    return (
      <main className="mx-auto max-w-4xl px-6 py-10">
        <Link className="text-sm font-bold text-emerald-300" href="/predictions">← Back to picks</Link>
        <div className="card mt-6">
          <h1 className="text-3xl font-black">Prediction unavailable</h1>
          <p className="mt-2 text-slate-400">This pick may have been unpublished or replaced by a newer version.</p>
        </div>
      </main>
    );
  }

  const premiumLocked = prediction.is_premium;
  const snapshots = prediction.odds_snapshots || [];

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <Link className="text-sm font-bold text-emerald-300" href="/predictions">← Back to all picks</Link>

      <section className={`card mt-6 overflow-hidden ${premiumLocked ? "premium-card" : ""}`}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500">{prediction.sport} • {prediction.league} • {formatDate(prediction.match_date)}</p>
            <h1 className="mt-3 text-4xl font-black">{prediction.home_team} vs {prediction.away_team}</h1>
            <p className="mt-2 text-slate-400">Market: {prediction.market} • Risk: {prediction.risk_level} • Version {prediction.version || 1}</p>
          </div>
          <div className="text-right">
            <span className={premiumLocked ? "premium-lock inline-flex" : "badge inline-flex"}>{premiumLocked ? "🔒 Premium • " : ""}{prediction.confidence}% EDGE</span>
            <p className="mt-2 text-sm text-slate-400">Status: {prediction.status || "active"}</p>
          </div>
        </div>

        <div className="mt-8 grid gap-5 md:grid-cols-3">
          <div className="rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-5 md:col-span-2">
            <p className="text-sm text-emerald-100/80">Recommended selection</p>
            <p className="mt-2 text-4xl font-black text-emerald-200">{prediction.pick}</p>
            <p className="mt-4 text-slate-200">{prediction.reasoning}</p>
          </div>
          <div className="rounded-2xl border border-slate-800 bg-slate-950/70 p-5">
            <p className="text-sm text-slate-400">Model confidence</p>
            <p className="mt-2 text-3xl font-black">{prediction.confidence}%</p>
            <p className="mt-4 text-sm text-slate-400">EDGE Score</p>
            <p className="mt-1 text-3xl font-black text-emerald-300">{prediction.edge_score}</p>
          </div>
        </div>
      </section>

      <section className="mt-6 grid gap-6 lg:grid-cols-2">
        <div className="card">
          <h2 className="text-xl font-bold">Model analysis</h2>
          <p className="mt-3 text-slate-300">{prediction.engine_summary || "This prediction is generated from current fixture context, historical performance, team form, and calibrated market thresholds."}</p>
          <div className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
            <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Fixture ID</span><br /><b>{prediction.fixture_id}</b></div>
            <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Model version</span><br /><b>{prediction.model_version_id || "Fallback engine"}</b></div>
            <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Published</span><br /><b>{prediction.published_at ? new Date(prediction.published_at).toLocaleString() : "Pending"}</b></div>
            <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Result</span><br /><b className="capitalize">{prediction.result}</b></div>
          </div>
        </div>

        <div className="card">
          <h2 className="text-xl font-bold">Odds snapshots</h2>
          <p className="mt-2 text-sm text-slate-400">Published and closing prices help track ROI and closing-line value over time.</p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead><tr className="text-slate-400"><th>Phase</th><th>Home</th><th>Draw</th><th>Away</th><th>Book</th></tr></thead>
              <tbody>
                {snapshots.length ? snapshots.map((o: any, index: number) => (
                  <tr key={`${o.phase}-${index}`} className="border-t border-slate-800">
                    <td className="py-3 capitalize">{o.phase}</td><td>{formatOdds(o.home_odds)}</td><td>{formatOdds(o.draw_odds)}</td><td>{formatOdds(o.away_odds)}</td><td>{o.bookmaker || "fixture"}</td>
                  </tr>
                )) : <tr><td className="py-3 text-slate-400" colSpan={5}>No odds snapshots stored for this pick yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="responsible-note mt-6">
        <b>Responsible play:</b> {prediction.responsible_note || "Predictions are probabilistic, not guarantees. Never stake more than you can afford to lose."}
      </section>
    </main>
  );
}