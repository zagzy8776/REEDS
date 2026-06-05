export function PredictionCard({ p }: { p: any }) {
  return (
    <div className="card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase text-slate-500">{p.sport} • {p.league}</p>
          <h3 className="mt-1 text-lg font-bold">{p.home_team} vs {p.away_team}</h3>
        </div>
        <span className="badge">{p.confidence}% EDGE</span>
      </div>
      <div className="mt-4 rounded-xl bg-slate-950 p-4">
        <p className="text-sm text-slate-400">{p.market}</p>
        <p className="text-2xl font-black text-emerald-300">{p.pick}</p>
      </div>
      <p className="mt-3 text-sm text-slate-300">{p.reasoning}</p>
      <p className="mt-3 text-xs text-slate-500">Risk: {p.risk_level} {p.is_premium ? "• Premium" : "• Free"}</p>
    </div>
  );
}
