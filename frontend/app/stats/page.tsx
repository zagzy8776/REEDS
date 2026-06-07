import { getStats } from "../../lib/api";

export const dynamic = "force-dynamic";

function percent(value: number | undefined | null) {
  return `${value || 0}%`;
}

function decimal(value: number | undefined | null, digits = 2) {
  return typeof value === "number" ? value.toFixed(digits) : "N/A";
}

export default async function Stats() {
  const stats = await getStats();
  const results = stats.results || { settled_picks: 0, wins: 0, losses: 0, hit_rate: 0, by_sport: [], by_market: [], confidence_buckets: [] };
  const proof = stats.market_proof || { tracked_bets: 0, profit_units: 0, roi_percent: 0, clv_tracked: 0, positive_clv_rate: 0, by_market: [], note: "ROI/CLV tracking is warming up." };
  const dataQuality = stats.data_quality || {};

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="badge inline-block">Transparent ledger</p>
          <h1 className="mt-4 text-4xl font-black">Performance Stats</h1>
          <p className="mt-2 max-w-3xl text-slate-400">{stats.note}</p>
        </div>
        <p className="text-sm text-slate-500">Odds snapshots tracked: {dataQuality.odds_snapshots || 0}</p>
      </div>

      <section className="responsible-note mt-6">
        <b>Responsible disclaimer:</b> Historical validation, ROI, CLV, and confidence buckets are evidence signals only. They do not guarantee future outcomes.
      </section>

      <section className="mt-8 grid gap-4 md:grid-cols-4">
        <div className="card"><p className="text-sm text-slate-400">Settled Picks</p><p className="mt-2 text-3xl font-black">{results.settled_picks || 0}</p></div>
        <div className="card"><p className="text-sm text-slate-400">Wins</p><p className="mt-2 text-3xl font-black text-emerald-300">{results.wins || 0}</p></div>
        <div className="card"><p className="text-sm text-slate-400">Losses</p><p className="mt-2 text-3xl font-black text-rose-300">{results.losses || 0}</p></div>
        <div className="card"><p className="text-sm text-slate-400">Hit Rate</p><p className="mt-2 text-3xl font-black">{percent(results.hit_rate)}</p></div>
      </section>

      <section className="mt-8 grid gap-5 md:grid-cols-4">
        <div className="card md:col-span-2">
          <h2 className="text-xl font-bold">ROI / CLV Proof</h2>
          <p className="mt-2 text-sm text-slate-400">{proof.note}</p>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl bg-slate-950 p-4"><p className="text-sm text-slate-500">Tracked 1u Bets</p><p className="mt-1 text-3xl font-black">{proof.tracked_bets}</p></div>
            <div className="rounded-xl bg-slate-950 p-4"><p className="text-sm text-slate-500">Profit Units</p><p className={`mt-1 text-3xl font-black ${(proof.profit_units || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{decimal(proof.profit_units)}</p></div>
            <div className="rounded-xl bg-slate-950 p-4"><p className="text-sm text-slate-500">ROI</p><p className={`mt-1 text-3xl font-black ${(proof.roi_percent || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{percent(proof.roi_percent)}</p></div>
            <div className="rounded-xl bg-slate-950 p-4"><p className="text-sm text-slate-500">Positive CLV</p><p className="mt-1 text-3xl font-black text-sky-300">{percent(proof.positive_clv_rate)}</p><p className="text-xs text-slate-500">{proof.clv_tracked || 0} tracked closes</p></div>
          </div>
        </div>

        <div className="card md:col-span-2">
          <h2 className="text-xl font-bold">Calibration by Confidence</h2>
          <p className="mt-2 text-sm text-slate-400">Higher buckets should outperform lower buckets over a meaningful sample. This protects against overconfident marketing claims.</p>
          <div className="mt-4 space-y-3">
            {(results.confidence_buckets || []).map((r: any) => (
              <div key={r.bucket} className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                <div className="flex items-center justify-between"><b>{r.bucket}</b><span className="text-sm text-slate-400">{r.total} picks</span></div>
                <div className="mt-3 h-2 rounded-full bg-slate-800"><div className="h-2 rounded-full bg-emerald-400" style={{ width: `${Math.min(r.hit_rate || 0, 100)}%` }} /></div>
                <p className="mt-2 text-sm text-slate-400">{r.wins} wins • {percent(r.hit_rate)} hit rate</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mt-8 card">
        <h2 className="text-xl font-bold">ROI / CLV by Market</h2>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead><tr className="text-slate-400"><th>Market</th><th>Bets</th><th>Profit</th><th>ROI</th><th>CLV Tracked</th><th>Positive CLV</th></tr></thead>
            <tbody>
              {(proof.by_market || []).length ? proof.by_market.map((r: any) => (
                <tr key={r.market} className="border-t border-slate-800"><td className="py-3">{r.market}</td><td>{r.bets}</td><td>{decimal(r.profit)}</td><td>{percent(r.roi_percent)}</td><td>{r.clv_total}</td><td>{percent(r.positive_clv_rate)}</td></tr>
              )) : <tr><td className="py-3 text-slate-400" colSpan={6}>No supported settled odds markets yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mt-8 card">
        <h2 className="text-xl font-bold">Walk-Forward Backtests</h2>
        <p className="mt-1 text-sm text-slate-400">Rolling time-series tests measure future-window performance using accuracy, Brier score, and log loss.</p>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm"><thead><tr className="text-slate-400"><th>Sport</th><th>Strategy</th><th>Accuracy</th><th>Brier</th><th>Log Loss</th><th>Rows</th></tr></thead><tbody>{(stats.backtests || []).length ? stats.backtests.map((b: any, i: number) => <tr key={i} className="border-t border-slate-800"><td className="py-3">{b.sport}</td><td>{b.strategy}</td><td>{Math.round((b.accuracy || 0) * 100)}%</td><td>{decimal(b.brier_score, 3)}</td><td>{decimal(b.log_loss, 3)}</td><td>{b.sample_size}</td></tr>) : <tr><td className="py-3 text-slate-400" colSpan={6}>No walk-forward backtests stored yet.</td></tr>}</tbody></table>
        </div>
      </section>

      <section className="mt-8 card">
        <h2 className="text-xl font-bold">Model Versions</h2>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm"><thead><tr className="text-slate-400"><th>Sport</th><th>Type</th><th>Validation Accuracy</th><th>Rows</th><th>Active</th></tr></thead><tbody>{(stats.models || []).map((m: any, i: number) => <tr key={i} className="border-t border-slate-800"><td className="py-3">{m.sport}</td><td>{m.type}</td><td>{Math.round((m.accuracy || 0) * 100)}%</td><td>{m.sample_size}</td><td>{m.active ? "Yes" : "No"}</td></tr>)}</tbody></table>
        </div>
      </section>

      <section className="mt-8 grid gap-6 md:grid-cols-2">
        <div className="card"><h2 className="text-xl font-bold">Results by Sport</h2><table className="mt-4 w-full text-left text-sm"><thead><tr className="text-slate-400"><th>Sport</th><th>W/L</th><th>Hit Rate</th></tr></thead><tbody>{(results.by_sport || []).length ? results.by_sport.map((r: any) => <tr key={r.sport} className="border-t border-slate-800"><td className="py-3">{r.sport}</td><td>{r.wins}-{(r.total || 0) - (r.wins || 0)}</td><td>{percent(r.hit_rate)}</td></tr>) : <tr><td className="py-3 text-slate-400" colSpan={3}>No settled picks yet.</td></tr>}</tbody></table></div>
        <div className="card"><h2 className="text-xl font-bold">Results by Market</h2><table className="mt-4 w-full text-left text-sm"><thead><tr className="text-slate-400"><th>Market</th><th>W/L</th><th>Hit Rate</th></tr></thead><tbody>{(results.by_market || []).length ? results.by_market.map((r: any) => <tr key={r.market} className="border-t border-slate-800"><td className="py-3">{r.market}</td><td>{r.wins}-{(r.total || 0) - (r.wins || 0)}</td><td>{percent(r.hit_rate)}</td></tr>) : <tr><td className="py-3 text-slate-400" colSpan={3}>No settled markets yet.</td></tr>}</tbody></table></div>
      </section>
    </main>
  );
}