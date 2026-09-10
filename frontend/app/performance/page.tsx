import { getDataStatus, getLatestAlerts, getModelFeedbackStats, getPerformance, getStats } from "../../lib/api";

export const dynamic = "force-dynamic";

function digits(v: number | undefined | null, n = 2) {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(n) : "—";
}

function SegmentCards({ segments, note }: { segments: Record<string, any>; note?: string }) {
  const rows = [
    { key: "all_time", label: "All time" },
    { key: "last_7", label: "Last 7 days" },
    { key: "last_30", label: "Last 30 days" },
    { key: "last_100", label: "Last 100 reads" },
  ];
  return (
    <div>
      <p className="text-sm text-slate-400">{note}</p>
      <div className="mt-4 grid gap-4 md:grid-cols-4">
        {rows.map(({ key, label }) => {
          const s = segments[key];
          if (!s) return null;
          const acc = Number(s.accuracy ?? 0);
          const roi = Number(s.roi_percent ?? s.roi_estimate ?? 0);
          return (
            <div key={key} className="card">
              <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
              <p className="mt-2 text-3xl font-black">{s.predictions ?? s.sample ?? 0}</p>
              <p className="mt-1 text-xs text-slate-400">reads settled</p>
              <div className="mt-3 space-y-1 text-sm">
                <div className="flex justify-between"><span className="text-slate-500">Accuracy</span><b className={acc >= 50 ? "text-emerald-300" : "text-rose-300"}>{digits(acc, 1)}%</b></div>
                <div className="flex justify-between"><span className="text-slate-500">ROI</span><b className={roi >= 0 ? "text-emerald-300" : "text-rose-300"}>{digits(roi, 2)}%</b></div>
                <div className="flex justify-between"><span className="text-slate-500">W / L</span><b>{s.wins ?? 0} / {s.losses ?? 0}</b></div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default async function Performance() {
  const [perf, stats, dataStatus, feedback, alerts] = await Promise.all([
    getPerformance(),
    getStats(),
    getDataStatus(),
    getModelFeedbackStats(),
    getLatestAlerts(),
  ]);
  const segments = perf.segments || {};
  const bySport = Array.isArray(perf.by_sport) ? perf.by_sport : [];
  const byMarket = Array.isArray(perf.by_market) ? perf.by_market : [];
  const calibration = Array.isArray(perf.calibration) ? perf.calibration : [];
  const proof = stats.market_proof || {};
  const dataQuality = stats.data_quality || {};
  const backtests = Array.isArray(stats.backtests) ? stats.backtests : [];
  const models = Array.isArray(stats.models) ? stats.models : [];
  const statusOf = (b: any) => b?.status || "unavailable";
  const labels: Record<string, string> = {
    available: "Live", stale: "Stale", empty: "Empty", unavailable: "No data",
  };

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <section className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="badge inline-block">Performance center</p>
          <h1 className="mt-4 text-4xl font-black">LIVE RECORD, inspected honestly.</h1>
          <p className="mt-2 max-w-3xl text-slate-400">
            All numbers come from published picks that actually settled. Backtests and historical evaluations are labeled separately and never mixed into the live record.
          </p>
        </div>
        <p className="text-sm text-slate-500">Odds snapshots tracked: {dataQuality.odds_snapshots || 0}</p>
      </section>

      <section className="responsible-note mt-6">
        <b>Nothing invented:</b> {perf.note || "Performance is computed from real settlements only."} Past results do not guarantee the next one.
      </section>

      <section className="mt-8">
        <h2 className="text-xl font-bold">Live tracked record</h2>
        <div className="mt-3"><SegmentCards segments={segments} note="Accuracy vs best league-average baseline; ROI/CLV from tracked odds only." /></div>
      </section>

      <section className="mt-8 rounded-2xl border border-sky-400/15 bg-sky-400/[0.04] p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-black uppercase tracking-wider text-sky-300">Model evidence</p>
            <h2 className="mt-1 text-xl font-bold">Backtests and model versions</h2>
            <p className="mt-1 text-sm text-slate-400">These are validation artifacts, not proof of the live betting record. They stay deliberately separated from the live cards above.</p>
          </div>
          <span className="rounded-full border border-sky-400/20 px-3 py-1 text-xs font-bold text-sky-200">{backtests.length} backtest{backtests.length === 1 ? "" : "s"}</span>
        </div>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div className="rounded-xl border border-white/10 bg-slate-950/60 p-4">
            <p className="text-xs font-black uppercase tracking-wider text-slate-500">Latest walk-forward evidence</p>
            {backtests.length ? (
              <div className="mt-3 space-y-2">
                {backtests.slice(0, 6).map((b: any) => (
                  <div key={b.id} className="rounded-lg border border-white/5 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <b className="text-slate-200">{b.sport} • {b.model_type}</b>
                      <span className="text-sm font-bold text-sky-300">{digits(Number(b.accuracy || 0) * 100, 1)}%</span>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">{b.sample_size || 0} samples • {b.split_strategy || "walk-forward"}</p>
                    <p className="mt-1 text-[11px] text-slate-600">Brier {digits(b.brier_score, 3)} • Log loss {digits(b.log_loss, 3)}</p>
                  </div>
                ))}
              </div>
            ) : <p className="mt-3 text-sm text-slate-500">No stored walk-forward backtests are available yet.</p>}
          </div>
          <div className="rounded-xl border border-white/10 bg-slate-950/60 p-4">
            <p className="text-xs font-black uppercase tracking-wider text-slate-500">Registered model versions</p>
            {models.length ? (
              <div className="mt-3 space-y-2">
                {models.slice(0, 8).map((m: any) => (
                  <div key={m.id} className="flex items-center justify-between gap-3 rounded-lg border border-white/5 p-3 text-sm">
                    <div><b className="text-slate-200">{m.sport}</b><p className="text-xs text-slate-500">{m.type} • {m.sample_size || 0} samples</p></div>
                    <span className={m.active ? "text-emerald-300" : "text-slate-500"}>{m.active ? "Active" : "Inactive"}</span>
                  </div>
                ))}
              </div>
            ) : <p className="mt-3 text-sm text-slate-500">No registered production model versions are active.</p>}
          </div>
        </div>
      </section>

      <section className="mt-8 grid gap-5 md:grid-cols-4">
        <div className="card md:col-span-2">
          <h2 className="text-xl font-bold">Profit & closing price</h2>
          <p className="mt-2 text-sm text-slate-400">{proof.note}</p>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl bg-slate-950 p-4"><p className="text-sm text-slate-500">Tracked 1u bets</p><p className="mt-1 text-3xl font-black">{proof.tracked_bets || 0}</p></div>
            <div className="rounded-xl bg-slate-950 p-4"><p className="text-sm text-slate-500">Profit units</p><p className={`mt-1 text-3xl font-black ${(proof.profit_units || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{digits(proof.profit_units)}</p></div>
            <div className="rounded-xl bg-slate-950 p-4"><p className="text-sm text-slate-500">ROI</p><p className={`mt-1 text-3xl font-black ${(proof.roi_percent || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{digits(proof.roi_percent, 1)}%</p></div>
            <div className="rounded-xl bg-slate-950 p-4"><p className="text-sm text-slate-500">Positive CLV</p><p className="mt-1 text-3xl font-black text-sky-300">{digits(proof.positive_clv_rate, 1)}%</p><p className="text-xs text-slate-500">{proof.clv_tracked || 0} tracked closes</p></div>
          </div>
        </div>

        <div className="card md:col-span-2">
          <h2 className="text-xl font-bold">Calibration bands</h2>
          <p className="mt-2 text-sm text-slate-400">Did reads at 70%+ confidence actually win ~70%? Brier and log loss measure how tight the probabilities are.</p>
          <div className="mt-4 space-y-3">
            {calibration.length ? calibration.map((b: any, i: number) => (
              <div key={i} className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                <div className="flex items-center justify-between"><b>{b.label || b.bucket}</b><span className="text-sm text-slate-400">{b.sample} picks • {digits(b.actual, 1)}% actual</span></div>
                <div className="mt-3 h-2 rounded-full bg-slate-800"><div className="h-2 rounded-full bg-emerald-400" style={{ width: `${Math.min(b.actual || 0, 100)}%` }} /></div>
                <p className="mt-2 text-sm text-slate-400">Avg confidence {digits(b.expected ?? b.predicted, 1)}% • Brier {digits(b.brier, 3)} • Log loss {digits(b.log_loss, 3)}</p>
              </div>
            )) : <p className="text-sm text-slate-400">Calibration needs settled reads across confidence bands. It appears once enough real settlements exist.</p>}
          </div>
        </div>
      </section>

      <section className="mt-8 grid gap-5 md:grid-cols-2">
        <div className="card">
          <h2 className="text-xl font-bold">By sport</h2>
          <table className="mt-4 w-full text-left text-sm">
            <thead><tr className="text-slate-400"><th>Sport</th><th>W/L</th><th>Accuracy</th><th>ROI</th></tr></thead>
            <tbody>
              {bySport.length ? bySport.map((r: any) => (
                <tr key={r.sport} className="border-t border-slate-800">
                  <td className="py-3">{r.sport}</td>
                  <td>{r.wins}-{r.losses}</td>
                  <td className="text-emerald-300">{digits(r.accuracy, 1)}%</td>
                  <td className={Number(r.roi_percent || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>{digits(r.roi_percent, 2)}%</td>
                </tr>
              )) : <tr><td className="py-3 text-slate-400" colSpan={4}>No settled reads yet.</td></tr>}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h2 className="text-xl font-bold">By market</h2>
          <table className="mt-4 w-full text-left text-sm">
            <thead><tr className="text-slate-400"><th>Market</th><th>W/L</th><th>Accuracy</th></tr></thead>
            <tbody>
              {byMarket.length ? byMarket.map((r: any) => (
                <tr key={r.market} className="border-t border-slate-800">
                  <td className="py-3">{r.market}</td>
                  <td>{r.wins}-{r.losses}</td>
                  <td className="text-emerald-300">{digits(r.accuracy, 1)}%</td>
                </tr>
              )) : <tr><td className="py-3 text-slate-400" colSpan={3}>No settled reads yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mt-8 grid gap-5 md:grid-cols-2">
        <div className="card">
          <h2 className="text-xl font-bold">Data freshness</h2>
          <p className="mt-1 text-sm text-slate-400">Honest freshness for every data source behind the read. "Live" means data flowed recently; everything else is visible as stale/missing.</p>
          <div className="mt-4 space-y-2 text-sm">
            {[["Fixtures", dataStatus.fixtures], ["Odds", dataStatus.odds], ["Settlements", dataStatus.predictions]].map(([label, block]: any) => (
              <div key={label} className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950 p-3">
                <span className="text-slate-300">{label}</span>
                <span className={`rounded-full px-2 py-0.5 text-xs font-bold ${
                  statusOf(block) === "available" ? "bg-emerald-400/10 text-emerald-300" :
                  statusOf(block) === "stale" ? "bg-amber-400/10 text-amber-300" :
                  "bg-slate-600/20 text-slate-400"
                }`}>
                  {labels[statusOf(block)]}
                </span>
                <span className="text-xs text-slate-500">{block?.as_of || block?.note || ""}</span>
              </div>
            ))}
            <p className="text-xs text-slate-600">Last checked {dataStatus.as_of || "recently"}</p>
          </div>
        </div>

        <div className="card">
          <h2 className="text-xl font-bold">Repeated failure patterns</h2>
          <p className="mt-1 text-sm text-slate-400">Only after many validated losses in one dimension does REEDS flag a candidate — and it is never applied automatically.</p>
          {feedback.total_feedback_records > 0 && (
            <p className="mt-2 text-xs text-slate-500">{feedback.total_feedback_records} post-match feedback records stored.</p>
          )}
          <div className="mt-4 space-y-3">
            {(feedback.flagged_patterns || []).length ? feedback.flagged_patterns.map((f: any, i: number) => (
              <div key={i} className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-3 text-sm">
                <p className="font-bold text-amber-300">{f.sport} • {f.dimension}</p>
                <p className="mt-1 text-xs text-slate-400">Sample {f.sample} • accuracy {f.accuracy}% vs avg confidence {f.avg_confidence}% • Brier {f.brier}</p>
                <p className="mt-1 text-xs text-amber-200/80">{f.candidate_issue}</p>
              </div>
            )) : <p className="text-sm text-slate-400">No recurring failure candidates yet — honesty by default, not by editorial choice.</p>}
            {(feedback.flagged_patterns || []).length > 0 && <p className="text-xs text-slate-600">{feedback.note}</p>}
          </div>
        </div>
      </section>

      <section className="mt-8 card">
        <h2 className="text-xl font-bold">Latest signals</h2>
        <p className="mt-1 text-sm text-slate-400">Recent events REEDS tracked — settlements, red cards, and version updates.</p>
        <div className="mt-4 space-y-2">
          {(alerts || []).length ? alerts.slice(0, 12).map((a: any, i: number) => (
            <div key={i} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950 p-3 text-sm">
              <div>
                <p className="font-bold text-slate-200">{a.title}</p>
                <p className="text-xs text-slate-500">{a.message}</p>
              </div>
              <span className="shrink-0 text-xs text-slate-500">{a.time_label || a.occurred_at || ""}</span>
            </div>
          )) : <p className="text-sm text-slate-400">Nothing to report yet — that is the honest answer.</p>}
        </div>
      </section>
    </main>
  );
}
