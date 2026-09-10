import Link from "next/link";

function Badge({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${className}`}>{children}</span>;
}

function VerdictBadge({ verdict }: { verdict?: { key?: string; label?: string } }) {
  if (!verdict?.label) return null;
  const tone: Record<string, string> = {
    strong_read: "border-emerald-400/40 bg-emerald-500/15 text-emerald-300",
    reeds_value: "border-sky-400/40 bg-sky-500/15 text-sky-300",
    analyzed_low_evidence: "border-slate-500/40 bg-slate-600/20 text-slate-300",
    watchlist_insufficient: "border-white/10 bg-white/5 text-slate-400",
  };
  return (
    <Badge className={`border ${tone[verdict.key || ""] || tone.analyzed_low_evidence} text-[10px] tracking-wide`}>
      {verdict.label}
    </Badge>
  );
}

function ConfidenceMeter({ confidence }: { confidence: number }) {
  const color = confidence >= 72 ? "bg-emerald-400" : confidence >= 60 ? "bg-amber-400" : "bg-rose-400";
  return (
    <div className="w-24">
      <div className="h-1.5 w-full rounded-full bg-slate-800">
        <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${Math.min(confidence, 100)}%` }} />
      </div>
    </div>
  );
}

function RecordsBlock({ records }: { records?: Record<string, any> }) {
  const entries = [
    records?.live_record,
    records?.backtest,
    records?.historical_evaluation,
  ].filter(Boolean);
  if (!records || entries.length === 0) return null;
  const accent: Record<string, string> = {
    "LIVE RECORD": "border-emerald-400/20 bg-emerald-400/5 text-emerald-200",
    BACKTEST: "border-sky-400/20 bg-sky-400/5 text-sky-200",
    "HISTORICAL EVALUATION": "border-violet-400/20 bg-violet-400/5 text-violet-200",
  };
  return (
    <details className="group mt-4 rounded-xl border border-slate-800 bg-slate-950/50 p-3">
      <summary className="cursor-pointer list-none text-xs font-bold text-slate-400 group-open:text-emerald-300">
        TRANSPARENCY: how every record is classified
        <span className="ml-2 text-slate-600 group-open:hidden">▸</span>
        <span className="ml-2 hidden text-slate-600 group-open:inline">▾</span>
      </summary>
      <div className="mt-3 space-y-2">
        {entries.map((e) => (
          <div key={e.label} className={`rounded-lg border p-3 ${accent[e.label] || "border-slate-700 bg-slate-900"}`}>
            <div className="flex items-center justify-between gap-2">
              <p className="text-[11px] font-black uppercase tracking-wide">{e.label}</p>
              {typeof e.accuracy === "number" && (
                <p className="text-xs font-mono">
                  {e.wins != null ? `${e.wins} wins / ${(e.sample ?? e.sample_size) ?? "?"} → ` : ""}
                  {e.accuracy}%
                </p>
              )}
            </div>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">{e.note}</p>
            {(e.model_type || e.split) && (
              <p className="mt-1 text-[11px] text-slate-500">{e.model_type} • {e.split}</p>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

function MarketMetrics({ metrics }: { metrics?: { available?: boolean; side?: string; odds?: number; market_probability?: number; model_probability?: number; edge_pp?: number } }) {
  if (!metrics?.available) return null;
  const edge = metrics.edge_pp ?? 0;
  const tone = edge >= 0 ? "text-emerald-300" : "text-rose-300";
  return (
    <div className="mt-3 grid grid-cols-3 gap-2 text-center">
      <div className="rounded-lg bg-slate-800/50 p-2">
        <p className="text-[10px] uppercase tracking-wide text-slate-500">Market (1 / odds)</p>
        <p className="mt-0.5 text-sm font-bold text-slate-300">
          {metrics.odds?.toFixed(2)} → {metrics.market_probability?.toFixed(1)}%
        </p>
      </div>
      <div className="rounded-lg bg-slate-800/50 p-2">
        <p className="text-[10px] uppercase tracking-wide text-slate-500">REEDS model</p>
        <p className="mt-0.5 text-sm font-bold text-slate-300">{metrics.model_probability?.toFixed(1)}%</p>
      </div>
      <div className="rounded-lg bg-slate-800/50 p-2">
        <p className="text-[10px] uppercase tracking-wide text-slate-500">Edge vs market</p>
        <p className={`mt-0.5 text-sm font-bold ${tone}`}>{edge >= 0 ? "+" : ""}{edge.toFixed(1)}pp</p>
      </div>
    </div>
  );
}

function PostMatch({ pm }: { pm?: any }) {
  if (!pm) return null;
  const won = pm.result === "won";
  return (
    <div className={`mt-4 rounded-xl border p-3 text-sm ${won ? "border-emerald-400/25 bg-emerald-400/5" : "border-rose-400/25 bg-rose-400/5"}`}>
      <p className={`text-xs font-black uppercase tracking-wide ${won ? "text-emerald-300" : "text-rose-300"}`}>{pm.section_title}</p>
      <p className="mt-1 text-slate-300">{pm.final_score} • expected {pm.expected?.probability}% • prob. error {pm.probability_error >= 0 ? "+" : ""}{Math.round((pm.probability_error || 0) * 100)}pp</p>
      {pm.failed_signals?.length > 0 && (
        <div className="mt-2">
          <p className="text-xs font-bold text-rose-300/80">Signals that failed:</p>
          <ul className="mt-1 list-inside list-disc space-y-0.5 text-xs text-slate-400">
            {pm.failed_signals.slice(0, 4).map((s: string, i: number) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}
      {pm.successful_signals?.length > 0 && (
        <div className="mt-2">
          <p className="text-xs font-bold text-emerald-300/80">Signals that held:</p>
          <ul className="mt-1 list-inside list-disc space-y-0.5 text-xs text-slate-400">
            {pm.successful_signals.slice(0, 4).map((s: string, i: number) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function getRiskTone(risk: string) {
  const map: Record<string, string> = {
    low: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    medium: "border-amber-500/30 bg-amber-500/10 text-amber-300",
    high: "border-rose-500/30 bg-rose-500/10 text-rose-300",
  };
  return map[(risk || "").toLowerCase()] || "border-slate-500/30 bg-slate-500/10 text-slate-300";
}

function ResultBadge({ p }: { p: { result?: string; final_score?: string | null } }) {
  if (p.result === "won") return <Badge className="border border-emerald-400/50 text-emerald-300">WON {p.final_score}</Badge>;
  if (p.result === "lost") return <Badge className="border border-rose-400/50 text-rose-300">LOST {p.final_score}</Badge>;
  return <Badge className="border border-slate-500/50 text-slate-400">PENDING</Badge>;
}

interface PredictionCardProps {
  p: {
    id: number;
    fixture_id: number;
    sport: string;
    league: string;
    match_date: string;
    home_team: string;
    away_team: string;
    market: string;
    pick: string;
    confidence: number;
    edge_score: number;
    risk_level: string;
    reasoning: string;
    analysis?: any;
    engine_meta?: any;
    value_betting?: any;
    is_premium: boolean;
    version: number;
    status: string;
    published_at: string;
    is_published?: boolean;
    result: "pending" | "won" | "lost";
    final_score?: string | null;
    verdict?: { key?: string; label?: string; detail?: string };
    market_metrics?: { available?: boolean; side?: string; odds?: number; market_probability?: number; model_probability?: number; edge_pp?: number };
    records?: Record<string, any> | null;
    post_match?: any;
  };
}

export function PredictionCard({ p }: PredictionCardProps) {
  const confidence = Number(p.confidence || 0);
  const hasValueBet = p.value_betting?.value_note || (Array.isArray(p.analysis?.value_bets) ? (p.analysis.value_bets as any[]).length > 0 : p.analysis?.value_bets && Object.keys(p.analysis.value_bets).length > 0);
  const hasLineMovementWarning = Boolean(p.analysis?.line_movement_warning);
  const factors = Array.isArray(p.analysis?.factors) ? p.analysis.factors : [];
  const learning = p.engine_meta?.learning_feedback;
  const fixtureId = Number(p.fixture_id);

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 transition-colors hover:border-emerald-400/30">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge className="border border-slate-600">{p.sport}</Badge>
            <span className="truncate text-xs text-slate-500">{p.league}</span>
            {p.is_published === false && <Badge className="border border-amber-400/30 bg-amber-400/10 text-amber-300">DRAFT</Badge>}
            {p.is_premium && <Badge className="border border-purple-400/30 bg-purple-400/10 text-purple-300">Premium</Badge>}
          </div>
          <h3 className="mt-2 truncate text-lg font-bold">{p.home_team} vs {p.away_team}</h3>
          <p className="mt-0.5 text-xs text-slate-500">{p.match_date}</p>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-2xl font-black text-slate-100">{confidence.toFixed(1)}%</div>
          <div className="mt-1 flex justify-end"><ConfidenceMeter confidence={confidence} /></div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <VerdictBadge verdict={p.verdict} />
        <Badge className={getRiskTone(p.risk_level)}>{p.risk_level} risk</Badge>
        <ResultBadge p={p} />
      </div>
      {p.verdict?.detail && <p className="mt-2 text-xs leading-relaxed text-slate-400">{p.verdict.detail}</p>}

      <div className="mt-4 rounded-xl border border-emerald-400/20 bg-emerald-400/5 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] uppercase tracking-wide text-emerald-400/70">{p.market}</p>
            <p className="truncate text-lg font-bold text-emerald-300">{p.pick}</p>
            <p className="mt-0.5 text-[11px] text-slate-500">Model edge score: {p.edge_score?.toFixed(1)}%</p>
          </div>
          {fixtureId > 0 && (
            <Link href={`/fixtures/${fixtureId}/ai-reads`} className="shrink-0 rounded-full border border-sky-400/30 bg-sky-400/10 px-3 py-1.5 text-xs font-bold text-sky-200 hover:bg-sky-400/20">
              AI Reads
            </Link>
          )}
        </div>
      </div>

      <MarketMetrics metrics={p.market_metrics} />
      <PostMatch pm={p.post_match} />

      <details className="mt-4 rounded-xl border border-slate-800 bg-slate-950/40 p-3">
        <summary className="cursor-pointer text-xs font-bold text-slate-400 hover:text-emerald-300">
          View analysis & full reasoning
        </summary>
        <div className="mt-3 space-y-4">
          {hasLineMovementWarning && (
            <div className="rounded-lg border border-rose-400/20 bg-rose-400/5 p-3">
              <p className="text-xs font-bold text-rose-300">Line movement detected</p>
              <p className="mt-1 text-xs text-rose-200/80">{p.analysis?.market_efficiency_note || "Significant odds movement detected. The market may carry information the model did not use."}</p>
            </div>
          )}

          {p.analysis?.probabilities && (
            <div>
              <p className="mb-2 text-xs text-slate-500">Model probabilities</p>
              <div className="grid grid-cols-3 gap-2 text-center">
                {p.analysis.probabilities.home_win !== undefined && <div className="rounded-lg bg-slate-800/50 p-2"><p className="text-[10px] text-slate-500">Home</p><p className="text-sm font-bold text-slate-300">{(p.analysis.probabilities.home_win * 100).toFixed(1)}%</p></div>}
                {p.analysis.probabilities.draw !== undefined && <div className="rounded-lg bg-slate-800/50 p-2"><p className="text-[10px] text-slate-500">Draw</p><p className="text-sm font-bold text-slate-300">{(p.analysis.probabilities.draw * 100).toFixed(1)}%</p></div>}
                {p.analysis.probabilities.away_win !== undefined && <div className="rounded-lg bg-slate-800/50 p-2"><p className="text-[10px] text-slate-500">Away</p><p className="text-sm font-bold text-slate-300">{(p.analysis.probabilities.away_win * 100).toFixed(1)}%</p></div>}
              </div>
            </div>
          )}

          {p.analysis?.projection && (
            <div>
              <p className="mb-2 text-xs text-slate-500">Goal / points projection</p>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="rounded-lg bg-slate-800/50 p-2"><p className="text-[10px] text-slate-500">Home</p><p className="text-sm font-bold text-slate-300">{p.analysis.projection.home_expected_goals?.toFixed(2) ?? "N/A"}</p></div>
                <div className="rounded-lg bg-slate-800/50 p-2"><p className="text-[10px] text-slate-500">Away</p><p className="text-sm font-bold text-slate-300">{p.analysis.projection.away_expected_goals?.toFixed(2) ?? "N/A"}</p></div>
                <div className="rounded-lg bg-slate-800/50 p-2"><p className="text-[10px] text-slate-500">Total</p><p className="text-sm font-bold text-slate-300">{p.analysis.projection.total_expected_goals?.toFixed(2) ?? "N/A"}</p></div>
              </div>
            </div>
          )}

          {factors.length > 0 && (
            <div>
              <p className="mb-2 text-xs text-slate-500">Key signals the model checked</p>
              <div className="space-y-2">
                {factors.slice(0, 6).map((factor: any, idx: number) => (
                  <div key={idx} className="flex items-center justify-between gap-3 rounded-lg bg-slate-800/30 p-2">
                    <div className="min-w-0"><p className="truncate text-xs text-slate-400">{factor.label}</p>{factor.note && <p className="mt-0.5 text-[11px] text-slate-600">{factor.note}</p>}</div>
                    <p className="shrink-0 text-sm font-bold text-slate-300">{factor.value}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {hasValueBet && (
            <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-3">
              <p className="text-xs font-bold text-amber-300">Value analysis</p>
              {p.value_betting && (
                <div className="mt-2 grid grid-cols-3 gap-2 text-center">
                  <div><p className="text-[10px] text-slate-500">Edge</p><p className="text-sm font-bold text-amber-300">+{(p.value_betting.edge || 0).toFixed(2)}%</p></div>
                  <div><p className="text-[10px] text-slate-500">EV</p><p className="text-sm font-bold text-amber-300">+{(p.value_betting.expected_value || 0).toFixed(2)}%</p></div>
                  <div><p className="text-[10px] text-slate-500">Kelly stake</p><p className="text-sm font-bold text-amber-300">{(p.value_betting.kelly_stake || 0).toFixed(2)}%</p></div>
                </div>
              )}
              <p className="mt-2 text-xs text-slate-400">{p.value_betting?.value_note || p.analysis?.value_note}</p>
            </div>
          )}

          {p.reasoning && (
            <div className="rounded-lg border border-sky-400/10 bg-sky-400/5 p-3">
              <p className="text-xs text-sky-300 uppercase tracking-wide">Why REEDS picked this</p>
              <p className="mt-1 text-sm leading-relaxed text-slate-300">{p.reasoning}</p>
            </div>
          )}

          {learning && (
            <div className="rounded-lg border border-violet-400/10 bg-violet-400/5 p-3">
              <p className="text-xs text-violet-300 uppercase tracking-wide">Learning & calibration</p>
              <p className="mt-1 text-xs text-slate-400">
                {learning.segment_sample ? `${learning.segment_sample} settled reads in this sport/market are feeding calibration.` : "Settled reads are feeding calibration."}
                {Number(learning.adjustment || 0) < 0 ? ` Confidence was reduced by ${Math.abs(Number(learning.adjustment)).toFixed(1)} points after weaker-than-expected results.` : " No negative calibration adjustment was required for this read."}
              </p>
            </div>
          )}
        </div>
      </details>

      <RecordsBlock records={p.records as Record<string, any>} />

      <div className="mt-4 flex items-center justify-between border-t border-slate-800 pt-3 text-xs">
        <div className="flex items-center gap-2">
          {p.status === "superseded" && <Badge className="border border-slate-600 text-slate-400">REVISED</Badge>}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-600">v{p.version}</span>
          {p.id > 0 && <Link href={`/predictions/${p.id}`} className="font-bold text-emerald-300 hover:underline">Details →</Link>}
        </div>
      </div>
    </div>
  );
}