import Link from "next/link";

export function PredictionCard({ p }: { p: any }) {
  const premiumLocked = p.is_premium;
  const matchDate = p.match_date
    ? new Intl.DateTimeFormat("en", { weekday: "short", month: "short", day: "numeric" }).format(new Date(p.match_date))
    : "TBA";
  return (
    <div className={`card relative overflow-hidden ${premiumLocked ? "premium-card" : ""}`}>
      {premiumLocked ? <div className="absolute right-0 top-0 h-20 w-20 rounded-bl-full bg-amber-300/10 blur-sm" /> : null}
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase text-slate-500">{p.sport} • {p.league} • {matchDate}</p>
          <h3 className="mt-1 text-lg font-bold">{p.home_team} vs {p.away_team}</h3>
        </div>
        <span className={premiumLocked ? "premium-lock" : "badge"}>{premiumLocked ? "🔒 " : ""}{p.confidence}% EDGE</span>
      </div>
      <div className="mt-4 rounded-xl bg-slate-950 p-4">
        <p className="text-sm text-slate-400">{p.market}</p>
        <p className="text-2xl font-black text-emerald-300">{p.pick}</p>
      </div>
      <div className="mt-3 rounded-xl border border-white/10 bg-white/5 p-3">
        <p className="text-xs font-bold uppercase tracking-wide text-emerald-300">Why this pick?</p>
        <p className="mt-1 text-sm text-slate-300">{p.reasoning}</p>
      </div>
      <p className="mt-3 text-xs text-slate-500">Risk: {p.risk_level} {p.is_premium ? "• Premium" : "• Free"} {p.version ? `• v${p.version}` : ""}</p>
      <div className="mt-4 flex items-center justify-between gap-3">
        <Link className="text-sm font-bold text-emerald-300" href={`/predictions/${p.id}`}>Open pick →</Link>
        {premiumLocked ? <span className="rounded-lg border border-amber-400/30 bg-amber-400/10 px-2 py-1 text-xs text-amber-200">Premium</span> : null}
      </div>
    </div>
  );
}
