import Link from "next/link";

function Badge({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${className}`}>{children}</span>;
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
    analysis?: {
      probabilities?: { home_win?: number; draw?: number; away_win?: number; over25?: number; btts?: number };
      projection?: { score_band?: string; home_expected_goals?: number; away_expected_goals?: number; total_expected_goals?: number };
      factors?: Array<{ label: string; value: string | number; note?: string }>;
      value_bets?: { [key: string]: { edge: number; expected_value: number; kelly_stake: number; value_confidence: string } };
      value_note?: string;
      line_movement_warning?: boolean;
      market_efficiency_note?: string;
    };
    engine_meta?: {
      learning_feedback?: {
        adjustment?: number;
        segment_sample?: number;
        segment_accuracy?: number;
        guard?: string;
      };
    };
    value_betting?: { edge: number; expected_value: number; kelly_stake: number; value_confidence: string; value_note?: string };
    is_premium: boolean;
    version: number;
    status: string;
    published_at: string;
    is_published?: boolean;
    result: "pending" | "won" | "lost";
  };
}

export function PredictionCard({ p }: PredictionCardProps) {
  const getRiskColor = (risk: string) => {
    switch (risk.toLowerCase()) {
      case "low": return "bg-emerald-500/20 text-emerald-300 border-emerald-500/30";
      case "medium": return "bg-amber-500/20 text-amber-300 border-amber-500/30";
      case "high": return "bg-rose-500/20 text-rose-300 border-rose-500/30";
      default: return "bg-slate-500/20 text-slate-300 border-slate-500/30";
    }
  };

  const getConfidenceColor = (confidence: number) => confidence >= 70 ? "text-emerald-400" : confidence >= 60 ? "text-amber-400" : "text-rose-400";
  const hasValueBet = p.value_betting || (p.analysis?.value_bets && Object.keys(p.analysis.value_bets).length > 0);
  const hasLineMovementWarning = p.analysis?.line_movement_warning;
  const fixtureId = Number(p.fixture_id);
  const learning = p.engine_meta?.learning_feedback;
  const learningAdjustment = Number(learning?.adjustment || 0);

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 hover:border-emerald-400/30 transition-colors">
      <div className="flex items-start justify-between mb-4">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <Badge className="border border-slate-600 text-xs">{p.sport}</Badge>
            <span className="text-xs text-slate-500">{p.league}</span>
          </div>
          <h3 className="text-lg font-bold">{p.home_team} vs {p.away_team}</h3>
          <p className="text-xs text-slate-500 mt-1">{p.match_date}</p>
        </div>
        <div className="text-right">
          <div className={`text-2xl font-black ${getConfidenceColor(p.confidence)}`}>{p.confidence.toFixed(1)}%</div>
          <p className="text-xs text-slate-500">Confidence</p>
        </div>
      </div>

      <div className="mb-4 p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/20">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs text-emerald-400/70 uppercase tracking-wide mb-1">{p.market}</p>
            <p className="text-xl font-bold text-emerald-300">{p.pick}</p>
          </div>
          <div className="text-right">
            <p className="text-xs text-slate-500">Edge Score</p>
            <p className="text-lg font-bold text-emerald-400">{p.edge_score.toFixed(1)}%</p>
          </div>
        </div>
      </div>

      {hasValueBet && (
        <div className="mb-4 p-4 rounded-xl bg-amber-500/10 border border-amber-500/20">
          <p className="text-xs text-amber-400/70 uppercase tracking-wide mb-2">Value Betting Analysis</p>
          <div className="grid grid-cols-3 gap-4">
            <div><p className="text-xs text-slate-500">Edge vs Bookmaker</p><p className="text-lg font-bold text-amber-400">+{(p.value_betting?.edge || 0).toFixed(2)}%</p></div>
            <div><p className="text-xs text-slate-500">Expected Value</p><p className="text-lg font-bold text-amber-400">+{(p.value_betting?.expected_value || 0).toFixed(2)}%</p></div>
            <div><p className="text-xs text-slate-500">Kelly Stake</p><p className="text-lg font-bold text-amber-400">{(p.value_betting?.kelly_stake || 0).toFixed(2)}%</p></div>
          </div>
          {p.value_betting?.value_note && <p className="text-xs text-slate-500 mt-2">{p.value_betting.value_note}</p>}
        </div>
      )}

      {hasLineMovementWarning && (
        <div className="mb-4 p-3 rounded-xl bg-rose-500/10 border border-rose-500/20">
          <p className="text-xs text-rose-400 font-bold mb-1">⚠️ Line Movement Detected</p>
          <p className="text-xs text-rose-300">{p.analysis?.market_efficiency_note || "Significant odds movement detected. Market may have information not reflected in model."}</p>
        </div>
      )}

      {p.analysis?.probabilities && (
        <div className="mb-4">
          <p className="text-xs text-slate-500 mb-2">Model Probabilities</p>
          <div className="grid grid-cols-3 gap-2">
            {p.analysis.probabilities.home_win !== undefined && <div className="p-2 rounded-lg bg-slate-800/50"><p className="text-xs text-slate-500">Home Win</p><p className="text-sm font-bold text-slate-300">{(p.analysis.probabilities.home_win * 100).toFixed(1)}%</p></div>}
            {p.analysis.probabilities.draw !== undefined && <div className="p-2 rounded-lg bg-slate-800/50"><p className="text-xs text-slate-500">Draw</p><p className="text-sm font-bold text-slate-300">{(p.analysis.probabilities.draw * 100).toFixed(1)}%</p></div>}
            {p.analysis.probabilities.away_win !== undefined && <div className="p-2 rounded-lg bg-slate-800/50"><p className="text-xs text-slate-500">Away Win</p><p className="text-sm font-bold text-slate-300">{(p.analysis.probabilities.away_win * 100).toFixed(1)}%</p></div>}
          </div>
        </div>
      )}

      {p.analysis?.projection && (
        <div className="mb-4">
          <p className="text-xs text-slate-500 mb-2">Goal Projection</p>
          <div className="grid grid-cols-3 gap-2">
            <div className="p-2 rounded-lg bg-slate-800/50"><p className="text-xs text-slate-500">Home xG</p><p className="text-sm font-bold text-slate-300">{p.analysis.projection.home_expected_goals?.toFixed(2) || "N/A"}</p></div>
            <div className="p-2 rounded-lg bg-slate-800/50"><p className="text-xs text-slate-500">Away xG</p><p className="text-sm font-bold text-slate-300">{p.analysis.projection.away_expected_goals?.toFixed(2) || "N/A"}</p></div>
            <div className="p-2 rounded-lg bg-slate-800/50"><p className="text-xs text-slate-500">Total xG</p><p className="text-sm font-bold text-slate-300">{p.analysis.projection.total_expected_goals?.toFixed(2) || "N/A"}</p></div>
          </div>
        </div>
      )}

      {p.analysis?.factors && p.analysis.factors.length > 0 && (
        <div className="mb-4">
          <p className="text-xs text-slate-500 mb-2">Key Signals</p>
          <div className="space-y-2">
            {p.analysis.factors.slice(0, 3).map((factor, idx) => <div key={idx} className="flex items-center justify-between p-2 rounded-lg bg-slate-800/30"><div><p className="text-xs text-slate-400">{factor.label}</p>{factor.note && <p className="text-xs text-slate-600 mt-1">{factor.note}</p>}</div><p className="text-sm font-bold text-slate-300">{factor.value}</p></div>)}
          </div>
        </div>
      )}

      {p.reasoning && (
        <div className="mb-4 rounded-xl border border-sky-400/10 bg-sky-400/5 p-4">
          <p className="text-xs text-sky-300 uppercase tracking-wide mb-2">Why REEDS picked this</p>
          <p className="text-sm text-slate-300 leading-relaxed">{p.reasoning}</p>
        </div>
      )}

      {learning && (
        <div className="mb-4 rounded-xl border border-violet-400/10 bg-violet-400/5 p-3">
          <p className="text-xs text-violet-300 uppercase tracking-wide">Learning & calibration</p>
          <p className="mt-1 text-xs text-slate-400">
            {learning.segment_sample ? `${learning.segment_sample} settled reads in this sport/market are feeding calibration.` : "Settled reads are feeding calibration."}
            {learningAdjustment < 0 ? ` Confidence was reduced by ${Math.abs(learningAdjustment).toFixed(1)} points because recent results were weaker than the confidence level.` : " No negative calibration adjustment was required for this read."}
          </p>
        </div>
      )}

      <div className="flex items-center justify-between pt-4 border-t border-slate-800">
        <div className="flex items-center gap-2">
          <Badge className={getRiskColor(p.risk_level)}>{p.risk_level} Risk</Badge>
          {p.is_published === false && <Badge className="bg-amber-500/20 text-amber-300 border-amber-500/30">DRAFT</Badge>}
          {p.is_premium && <Badge className="bg-purple-500/20 text-purple-300 border-purple-500/30">Premium</Badge>}
        </div>
        <div className="flex items-center gap-2">
          <Badge className={p.result === "won" ? "border border-emerald-500/50 text-emerald-400" : p.result === "lost" ? "border border-rose-500/50 text-rose-400" : "border border-slate-500/50 text-slate-400"}>{p.result === "pending" ? "Pending" : p.result === "won" ? "Won" : "Lost"}</Badge>
          {Number.isFinite(fixtureId) && fixtureId > 0 && <Link href={`/fixtures/${fixtureId}/ai-reads`} className="rounded-full border border-sky-400/30 bg-sky-400/10 px-3 py-1.5 text-xs font-bold text-sky-200 hover:bg-sky-400/20">AI Reads</Link>}
        </div>
      </div>
    </div>
  );
}
