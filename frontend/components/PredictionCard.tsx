import Link from "next/link";
import type { ReactNode } from "react";

type Verdict = { key?: string; label?: string; detail?: string };
type Metrics = { available?: boolean; odds?: number; market_probability?: number; model_probability?: number; edge_pp?: number };

function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "green" | "amber" | "red" | "blue" }) {
  const tones = {
    neutral: "border-white/10 bg-white/[0.04] text-slate-400",
    green: "border-emerald-400/20 bg-emerald-400/10 text-emerald-300",
    amber: "border-amber-400/20 bg-amber-400/10 text-amber-300",
    red: "border-rose-400/20 bg-rose-400/10 text-rose-300",
    blue: "border-sky-400/20 bg-sky-400/10 text-sky-300",
  };
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.08em] ${tones[tone]}`}>{children}</span>;
}

function verdictTone(key?: string): "green" | "blue" | "amber" | "neutral" {
  if (key === "strong_read") return "green";
  if (key === "reeds_value") return "blue";
  if (key === "analyzed_low_evidence") return "amber";
  return "neutral";
}

function verdictTitle(verdict?: Verdict) {
  if (verdict?.key === "strong_read") return "Strong read";
  if (verdict?.key === "reeds_value") return "REEDS value";
  if (verdict?.key === "watchlist_insufficient") return "Watchlist";
  return "Early read";
}

function confidenceLabel(value: number) {
  if (value >= 72) return "High confidence";
  if (value >= 60) return "Good confidence";
  if (value >= 50) return "Moderate confidence";
  return "Low confidence";
}

function riskTone(risk?: string): "green" | "amber" | "red" | "neutral" {
  const value = (risk || "").toLowerCase();
  if (value === "low") return "green";
  if (value === "medium") return "amber";
  if (value === "high") return "red";
  return "neutral";
}

function resultTone(result?: string): "green" | "red" | "neutral" {
  if (result === "won") return "green";
  if (result === "lost") return "red";
  return "neutral";
}

function readableReasoning(p: PredictionCardProps["p"]) {
  const factors = Array.isArray(p.analysis?.factors) ? p.analysis.factors : [];
  const reasoning = String(p.reasoning || "").trim();
  if (reasoning) return reasoning;
  if (factors.length) return String(factors[0]);
  if (p.verdict?.detail) return p.verdict.detail;
  return "REEDS has analysed the available match information and is keeping this read proportional to the evidence.";
}

function MatchShape({ analysis }: { analysis?: any }) {
  const probabilities = analysis?.probabilities;
  const projection = analysis?.projection;
  if (!probabilities && !projection) return null;
  const home = probabilities?.home_win != null ? Number(probabilities.home_win) * 100 : null;
  const draw = probabilities?.draw != null ? Number(probabilities.draw) * 100 : null;
  const away = probabilities?.away_win != null ? Number(probabilities.away_win) * 100 : null;
  const totalGoals = projection?.total_expected_goals;
  return (
    <div className="rounded-2xl border border-white/[0.07] bg-slate-950/60 p-4">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-slate-500">Match outlook</p>
        {totalGoals != null && <span className="text-xs font-semibold text-slate-400">~{Number(totalGoals).toFixed(1)} goals</span>}
      </div>
      {home != null && draw != null && away != null && (
        <div className="mt-3">
          <div className="flex h-2 overflow-hidden rounded-full bg-slate-800">
            <div className="bg-emerald-400" style={{ width: `${home}%` }} />
            <div className="bg-slate-500" style={{ width: `${draw}%` }} />
            <div className="bg-sky-400" style={{ width: `${away}%` }} />
          </div>
          <div className="mt-2 grid grid-cols-3 text-center">
            <div><p className="text-[10px] text-slate-500">Home</p><p className="text-sm font-bold">{home.toFixed(0)}%</p></div>
            <div><p className="text-[10px] text-slate-500">Draw</p><p className="text-sm font-bold">{draw.toFixed(0)}%</p></div>
            <div><p className="text-[10px] text-slate-500">Away</p><p className="text-sm font-bold">{away.toFixed(0)}%</p></div>
          </div>
        </div>
      )}
    </div>
  );
}

function MarketSnapshot({ metrics }: { metrics?: Metrics }) {
  if (!metrics?.available) return null;
  const edge = Number(metrics.edge_pp || 0);
  return (
    <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
      <div className="rounded-xl bg-slate-950/70 p-3"><p className="text-[10px] uppercase tracking-wide text-slate-600">Market</p><p className="mt-1 text-sm font-bold text-slate-300">{metrics.market_probability?.toFixed(0)}%</p>{metrics.odds != null && <p className="text-[10px] text-slate-600">odds {metrics.odds.toFixed(2)}</p>}</div>
      <div className="rounded-xl bg-slate-950/70 p-3"><p className="text-[10px] uppercase tracking-wide text-slate-600">REEDS sees</p><p className="mt-1 text-sm font-bold text-slate-200">{metrics.model_probability?.toFixed(0)}%</p></div>
      <div className="col-span-2 rounded-xl bg-slate-950/70 p-3 sm:col-span-1"><p className="text-[10px] uppercase tracking-wide text-slate-600">Difference</p><p className={`mt-1 text-sm font-bold ${edge >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{edge >= 0 ? "+" : ""}{edge.toFixed(1)} points</p></div>
    </div>
  );
}

function PostMatch({ pm }: { pm?: any }) {
  if (!pm) return null;
  const won = pm.result === "won";
  return (
    <div className={`mt-5 rounded-2xl border p-4 ${won ? "border-emerald-400/20 bg-emerald-400/[0.05]" : "border-rose-400/20 bg-rose-400/[0.05]"}`}>
      <div className="flex items-center justify-between gap-3"><p className={`text-[11px] font-bold uppercase tracking-[0.12em] ${won ? "text-emerald-300" : "text-rose-300"}`}>{won ? "REEDS got it right" : "REEDS missed this one"}</p><span className="text-xs text-slate-500">{pm.final_score || "Final"}</span></div>
      {pm.failed_signals?.length > 0 && <p className="mt-2 text-sm text-slate-300">What went wrong: {pm.failed_signals.slice(0, 2).join("; ")}</p>}
      {pm.successful_signals?.length > 0 && <p className="mt-2 text-sm text-slate-300">What held up: {pm.successful_signals.slice(0, 2).join("; ")}</p>}
    </div>
  );
}

export interface PredictionCardProps {
  p: {
    id: number; fixture_id: number; sport: string; league: string; match_date: string; home_team: string; away_team: string;
    market: string; pick: string; confidence: number; edge_score: number; risk_level: string; reasoning: string; analysis?: any;
    engine_meta?: any; value_betting?: any; is_premium: boolean; version: number; status: string; published_at: string;
    is_published?: boolean; result: "pending" | "won" | "lost"; final_score?: string | null; verdict?: Verdict;
    market_metrics?: Metrics; records?: Record<string, any> | null; post_match?: any;
  };
}

export function PredictionCard({ p }: PredictionCardProps) {
  const confidence = Math.max(0, Math.min(100, Number(p.confidence || 0)));
  const fixtureId = Number(p.fixture_id);
  const risk = (p.risk_level || "unknown").toLowerCase();
  const resultLabel = p.result === "won" ? `Won${p.final_score ? ` · ${p.final_score}` : ""}` : p.result === "lost" ? `Lost${p.final_score ? ` · ${p.final_score}` : ""}` : "Pending";
  const hasAdvanced = Boolean(p.analysis?.probabilities || p.analysis?.projection || p.analysis?.factors?.length || p.market_metrics?.available || p.records);
  const goalProjection = p.analysis?.projection?.total_expected_goals;
  const bttsRaw = p.analysis?.btts_probability ?? p.analysis?.probabilities?.btts;
  const over25Raw = p.analysis?.over_2_5_probability ?? p.analysis?.probabilities?.over_2_5;
  const btts = bttsRaw != null ? (Number(bttsRaw) <= 1 ? Number(bttsRaw) * 100 : Number(bttsRaw)) : null;
  const over25 = over25Raw != null ? (Number(over25Raw) <= 1 ? Number(over25Raw) * 100 : Number(over25Raw)) : null;

  return (
    <article className="prediction-card group overflow-hidden rounded-[1.35rem] border border-white/[0.08] bg-[#0b1220] shadow-[0_18px_60px_rgba(0,0,0,0.22)] transition duration-200 hover:-translate-y-0.5 hover:border-emerald-400/20">
      <div className="p-4 sm:p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2"><span className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">{p.sport}</span><span className="h-1 w-1 rounded-full bg-slate-700" /><span className="truncate text-xs text-slate-500">{p.league}</span></div>
            <h3 className="mt-3 text-base font-extrabold tracking-tight text-white sm:text-lg">{p.home_team}</h3><p className="text-xs font-medium text-slate-600">vs</p><h3 className="text-base font-extrabold tracking-tight text-white sm:text-lg">{p.away_team}</h3>
            <p className="mt-2 text-[11px] text-slate-600">{p.match_date}</p>
          </div>
          <div className="shrink-0 text-right"><p className="text-3xl font-black tracking-tight text-white">{confidence.toFixed(0)}%</p><p className="mt-1 text-[10px] font-semibold text-slate-500">{confidenceLabel(confidence)}</p></div>
        </div>

        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-slate-800"><div className={`h-full rounded-full ${confidence >= 72 ? "bg-emerald-400" : confidence >= 60 ? "bg-amber-400" : "bg-slate-500"}`} style={{ width: `${confidence}%` }} /></div>

        <div className="mt-4 flex flex-wrap items-center gap-2"><Pill tone={verdictTone(p.verdict?.key)}>{verdictTitle(p.verdict)}</Pill><Pill tone={riskTone(risk)}>{risk === "unknown" ? "Risk not rated" : `${risk} risk`}</Pill>{p.is_published === false && <Pill tone="amber">Early / draft</Pill>}{p.result !== "pending" && <Pill tone={resultTone(p.result)}>{resultLabel}</Pill>}</div>

        <div className="mt-5 rounded-2xl border border-emerald-400/15 bg-gradient-to-br from-emerald-400/[0.07] to-transparent p-4">
          <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-emerald-400/70">REEDS leans</p>
          <div className="mt-1 flex items-end justify-between gap-3"><div className="min-w-0"><p className="text-xl font-black leading-tight text-emerald-300">{p.pick}</p><p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-500">{readableReasoning(p)}</p></div>{fixtureId > 0 && <Link href={`/fixtures/${fixtureId}/ai-reads`} className="shrink-0 rounded-xl border border-emerald-300/20 bg-emerald-300/10 px-3 py-2 text-xs font-bold text-emerald-200 transition hover:bg-emerald-300/15">Full intelligence</Link>}</div>
        </div>

        {(goalProjection != null || btts != null || over25 != null) && <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">{goalProjection != null && <div className="rounded-xl bg-slate-950/60 px-3 py-2.5"><p className="text-[10px] uppercase tracking-wide text-slate-600">Expected goals</p><p className="mt-0.5 font-bold text-slate-300">{Number(goalProjection).toFixed(1)}</p></div>}{btts != null && <div className="rounded-xl bg-slate-950/60 px-3 py-2.5"><p className="text-[10px] uppercase tracking-wide text-slate-600">Both teams score</p><p className="mt-0.5 font-bold text-slate-300">{btts.toFixed(0)}%</p></div>}{over25 != null && <div className="rounded-xl bg-slate-950/60 px-3 py-2.5"><p className="text-[10px] uppercase tracking-wide text-slate-600">Over 2.5 goals</p><p className="mt-0.5 font-bold text-slate-300">{over25.toFixed(0)}%</p></div>}</div>}

        <MarketSnapshot metrics={p.market_metrics} />
        <PostMatch pm={p.post_match} />

        {hasAdvanced && <details className="mt-4 group/details"><summary className="flex cursor-pointer list-none items-center justify-between rounded-xl border border-white/[0.07] bg-slate-950/40 px-4 py-3 text-xs font-bold text-slate-400 transition hover:text-slate-200"><span>See the evidence</span><span className="text-slate-600 transition group-open/details:rotate-180">⌄</span></summary><div className="mt-2 space-y-3 rounded-2xl border border-white/[0.06] bg-slate-950/30 p-4"><MatchShape analysis={p.analysis} />{Array.isArray(p.analysis?.factors) && p.analysis.factors.length > 0 && <div><p className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-600">What influenced the read</p><ul className="mt-2 space-y-2">{p.analysis.factors.slice(0, 4).map((factor: any, index: number) => <li key={index} className="rounded-xl bg-slate-900/70 px-3 py-2 text-sm text-slate-300">{typeof factor === "string" ? factor : factor?.label || factor?.name || JSON.stringify(factor)}</li>)}</ul></div>}{p.analysis?.line_movement_warning && <div className="rounded-xl border border-rose-400/15 bg-rose-400/[0.05] p-3 text-sm text-rose-200">Market movement deserves attention before kickoff.</div>}{p.records && <div className="rounded-xl border border-white/[0.06] bg-slate-900/60 p-3 text-xs leading-relaxed text-slate-500">Track record evidence is separated from the live read so backtests and historical evaluations are never presented as live results.</div>}</div></details>}
      </div>
    </article>
  );
}
