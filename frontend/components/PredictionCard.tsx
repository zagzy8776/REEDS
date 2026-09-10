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

export interface PredictionCardProps {
  p: {
    id: number; fixture_id: number; sport: string; league: string; match_date: string; home_team: string; away_team: string;
    market: string; pick: string; confidence: number; edge_score: number; risk_level: string; reasoning: string; analysis?: any;
    engine_meta?: any; is_premium: boolean; version: number; status: string; published_at: string;
    is_published?: boolean; result: "pending" | "won" | "lost"; final_score?: string | null; verdict?: Verdict;
    market_metrics?: Metrics; records?: Record<string, any> | null; post_match?: any;
  };
}

export function PredictionCard({ p }: PredictionCardProps) {
  const confidence = Math.max(0, Math.min(100, Number(p.confidence || 0)));
  const risk = (p.risk_level || "unknown").toLowerCase();
  const cold =
    Boolean(p.analysis?.cold_start || p.engine_meta?.cold_start) ||
    p.analysis?.data_depth === "cold_start" ||
    p.engine_meta?.data_depth === "cold_start";
  const reasoning = String(p.reasoning || "").trim() || "REEDS analysed the available match information.";

  return (
    <article className="rounded-2xl border border-white/[0.08] bg-slate-900/50 p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">{p.sport} · {p.league}</p>
          <h3 className="mt-1 text-lg font-black text-white">{p.home_team} vs {p.away_team}</h3>
          <p className="mt-1 text-sm text-slate-400">{p.market}: <span className="font-bold text-emerald-300">{p.pick}</span></p>
        </div>
        <div className="shrink-0 text-right">
          <p className="text-3xl font-black text-white">{confidence.toFixed(0)}%</p>
          <p className="mt-1 text-[10px] font-semibold text-slate-500">{confidenceLabel(confidence)}</p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Pill>{verdictTitle(p.verdict)}</Pill>
        <Pill tone={riskTone(risk)}>{risk === "unknown" ? "Risk not rated" : `${risk} risk`}</Pill>
        {p.is_published === false && <Pill tone="amber">Early / draft</Pill>}
        {cold && <Pill tone="red">Thin data</Pill>}
        {p.result !== "pending" && <Pill tone={p.result === "won" ? "green" : "red"}>{p.result}</Pill>}
      </div>
      <div className="mt-4">
        <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-emerald-400/70">REEDS leans</p>
        <p className="mt-1 text-sm leading-relaxed text-slate-300">{reasoning}</p>
      </div>
      <div className="mt-4">
        <Link href={`/fixtures/${p.fixture_id}/ai-reads`} className="text-xs font-bold text-sky-300 hover:underline">
          Full intelligence →
        </Link>
      </div>
    </article>
  );
}
