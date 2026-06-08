import Link from "next/link";
import { getPrediction } from "../../../lib/api";
import { CommunityActions } from "../../../components/CommunityActions";

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
  const analysis = prediction.analysis || {};
  const factors = analysis.factors || [];
  const probabilities = analysis.probabilities || {};
  const projection = analysis.projection || {};

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <Link className="text-sm font-bold text-emerald-300" href="/predictions">← Back to all picks</Link>

      <section className={`card mt-6 overflow-hidden ${premiumLocked ? "premium-card" : ""}`}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500">{prediction.sport} • {prediction.league} • {formatDate(prediction.match_date)}</p>
             <h1 className="mt-3 text-3xl font-black sm:text-4xl">{prediction.home_team} vs {prediction.away_team}</h1>
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

      <section className="mt-6 card">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="badge inline-block">AI explanation</p>
            <h2 className="mt-3 text-2xl font-black">Why LOYAL EDGE likes this angle</h2>
            <p className="mt-2 max-w-3xl text-sm text-slate-400">{analysis.summary || "The model blends team form, goal profile, market context and risk filters."}</p>
          </div>
          <p className="rounded-xl border border-emerald-400/20 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-200">{analysis.market_logic || "Market logic available after the next model refresh."}</p>
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-3">
          {(factors.length ? factors : [
            { label: "Form", value: "—", note: "Recent team form signal" },
            { label: "Goal profile", value: "—", note: "Expected goals and scoring trend" },
            { label: "Risk filter", value: prediction.risk_level, note: "Volatility control" },
          ]).map((factor: any) => (
            <div key={factor.label} className="rounded-2xl border border-slate-800 bg-slate-950/70 p-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">{factor.label}</p>
              <p className="mt-2 text-2xl font-black text-white">{String(factor.value)}</p>
              <p className="mt-1 text-xs text-slate-400">{factor.note}</p>
            </div>
          ))}
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-4">
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Score band</span><br /><b>{projection.score_band || "—"}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Home xG</span><br /><b>{projection.home_expected_goals ?? "—"}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Away xG</span><br /><b>{projection.away_expected_goals ?? "—"}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Total xG</span><br /><b>{projection.total_expected_goals ?? "—"}</b></div>
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-5">
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Home</span><br /><b>{probabilities.home_win !== undefined ? `${Math.round(probabilities.home_win * 100)}%` : "—"}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Draw</span><br /><b>{probabilities.draw !== undefined ? `${Math.round(probabilities.draw * 100)}%` : "—"}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Away</span><br /><b>{probabilities.away_win !== undefined ? `${Math.round(probabilities.away_win * 100)}%` : "—"}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Over 2.5</span><br /><b>{probabilities.over25 !== undefined ? `${Math.round(probabilities.over25 * 100)}%` : "—"}</b></div>
          <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">BTTS</span><br /><b>{probabilities.btts !== undefined ? `${Math.round(probabilities.btts * 100)}%` : "—"}</b></div>
        </div>
      </section>

      <section className="mt-6 grid gap-6 lg:grid-cols-2">
        <div className="card">
          <h2 className="text-xl font-bold">Model analysis</h2>
          <p className="mt-3 text-slate-300">{prediction.engine_summary || "This prediction is generated from current fixture context, historical performance, team form, and calibrated market thresholds."}</p>
            <div className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
             <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Match status</span><br /><b>{prediction.status || "Active"}</b></div>
             <div className="rounded-xl bg-slate-950 p-4"><span className="text-slate-500">Signal type</span><br /><b>AI market read</b></div>
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

      <section className="mt-6 grid gap-6 lg:grid-cols-2">
        <div className="card">
          <h2 className="text-xl font-bold">Community consensus</h2>
          <p className="mt-2 text-sm text-slate-400">See how the room is leaning. Community posts are separate from AI picks, but visible side-by-side for comparison.</p>
          <div className="mt-4 space-y-3">
            {(prediction.community?.consensus || []).length ? prediction.community.consensus.map((c: any) => (
              <div key={c.pick} className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                <div className="flex items-center justify-between"><b>{c.pick}</b><span className="text-emerald-300">{c.percent}%</span></div>
                <p className="mt-1 text-xs text-slate-500">{c.count} community pick{c.count === 1 ? "" : "s"}</p>
              </div>
            )) : <p className="text-sm text-slate-400">No community picks for this fixture yet.</p>}
          </div>
        </div>

        <div className="card">
          <h2 className="text-xl font-bold">Tipster notes</h2>
          <div className="mt-4 max-h-80 space-y-3 overflow-y-auto">
            {(prediction.community?.entries || []).length ? prediction.community.entries.map((entry: any) => (
              <div key={entry.id} className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                <div className="flex items-center justify-between gap-3"><b>{entry.username}</b><span className="text-xs text-slate-500">{entry.market}: {entry.pick}</span></div>
                <p className="mt-2 text-sm text-slate-300">{entry.analysis_text || "No written analysis provided."}</p>
                <CommunityActions seed={entry.id % 7} />
              </div>
            )) : <p className="text-sm text-slate-400">Be the first to publish a community view on this match.</p>}
          </div>
          <Link href="/predictions/submit" className="mt-4 inline-flex rounded-xl bg-emerald-400 px-4 py-2 text-sm font-black text-slate-950">+ Add your take</Link>
        </div>
      </section>

      <section className="responsible-note mt-6">
        <b>Responsible play:</b> {prediction.responsible_note || "Predictions are probabilistic, not guarantees. Never stake more than you can afford to lose."}
      </section>
    </main>
  );
}