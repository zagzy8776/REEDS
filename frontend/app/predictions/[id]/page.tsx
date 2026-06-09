"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function formatDate(value?: string) {
  if (!value) return "TBA";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
}

export default function PredictionDetail() {
  const { id } = useParams();
  const [prediction, setPrediction] = useState<any>(null);
  const [tab, setTab] = useState<"ai" | "community">("ai");
  const [expertsOnly, setExpertsOnly] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API_URL}/api/predictions/${id}`)
      .then((r) => r.json())
      .then(setPrediction)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-emerald-400 border-t-transparent" />
          <p className="mt-3 text-slate-400">Loading pick...</p>
        </div>
      </main>
    );
  }

  if (!prediction) {
    return (
      <main className="mx-auto max-w-4xl px-6 py-10">
        <Link className="text-sm font-bold text-emerald-300" href="/predictions">← Back to picks</Link>
        <div className="card mt-6">
          <h1 className="text-3xl font-black">Pick not available</h1>
          <p className="mt-2 text-slate-400">It may have been removed or replaced.</p>
        </div>
      </main>
    );
  }

  const analysis = prediction.analysis || {};
  const factors = analysis.factors || [];
  const probabilities = analysis.probabilities || {};
  const community = prediction.community || {};
  const entries = community.entries || [];
  const consensus = community.consensus || [];

  // Filter entries by expert status if toggle is on
  const filteredEntries = expertsOnly
    ? entries.filter((e: any) => e.was_correct !== false && e.is_settled)
    : entries;

  return (
    <main className="mx-auto max-w-4xl px-4 py-6 sm:px-6">
      <Link className="mb-4 inline-block text-sm font-bold text-emerald-300" href="/predictions">← Back to picks</Link>

      {/* Match Header */}
      <div className="card relative overflow-hidden">
        <div className="absolute right-0 top-0 h-32 w-32 rounded-bl-full bg-emerald-400/10 blur-xl" />
        <p className="text-xs uppercase tracking-wide text-slate-500">{prediction.sport} • {prediction.league} • {formatDate(prediction.match_date)}</p>
        <h1 className="mt-3 text-3xl font-black sm:text-4xl">{prediction.home_team} vs {prediction.away_team}</h1>
        <div className="mt-3 flex items-center gap-3">
          <span className="rounded-full bg-emerald-400/10 px-3 py-1 text-sm font-bold text-emerald-300">{prediction.confidence}% EDGE</span>
          <span className="text-sm text-slate-400">{prediction.market} • {prediction.risk_level} risk</span>
        </div>

        {/* The Pick */}
        <div className="mt-5 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-5">
          <p className="text-sm text-emerald-100/80">The AI pick</p>
          <p className="mt-2 text-4xl font-black text-emerald-200">{prediction.pick}</p>
          <p className="mt-3 text-slate-200">{prediction.reasoning}</p>
        </div>
      </div>

      {/* Tab Switcher */}
      <div className="mt-6 flex gap-1 rounded-2xl border border-slate-800 bg-slate-900/50 p-1">
        <button
          onClick={() => setTab("ai")}
          className={`flex-1 rounded-xl py-3 text-center font-bold transition-colors ${
            tab === "ai" ? "bg-emerald-400 text-slate-950" : "text-slate-400 hover:text-white"
          }`}
        >
          🤖 AI Insights
        </button>
        <button
          onClick={() => setTab("community")}
          className={`flex-1 rounded-xl py-3 text-center font-bold transition-colors ${
            tab === "community" ? "bg-emerald-400 text-slate-950" : "text-slate-400 hover:text-white"
          }`}
        >
          👥 Community Slips ({entries.length})
        </button>
      </div>

      {/* Tab Content */}
      {tab === "ai" ? (
        <div className="mt-4 space-y-4">
          {/* Confidence & Edge */}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="card">
              <p className="text-sm text-slate-400">Confidence</p>
              <p className="mt-2 text-3xl font-black">{prediction.confidence}%</p>
              <div className="mt-2 h-2 rounded-full bg-slate-800">
                <div className="h-2 rounded-full bg-emerald-400 transition-all" style={{ width: `${prediction.confidence}%` }} />
              </div>
            </div>
            <div className="card">
              <p className="text-sm text-slate-400">EDGE Score</p>
              <p className="mt-2 text-3xl font-black text-emerald-300">{prediction.edge_score}</p>
              <p className="mt-2 text-xs text-slate-500">Higher edge = more value vs market</p>
            </div>
          </div>

          {/* Model Factors */}
          {factors.length > 0 && (
            <div className="card">
              <h2 className="text-lg font-black">Model Factors</h2>
              <div className="mt-3 grid gap-3 sm:grid-cols-3">
                {factors.map((f: any) => (
                  <div key={f.label} className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                    <p className="text-xs uppercase text-slate-500">{f.label}</p>
                    <p className="mt-1 text-xl font-black">{String(f.value)}</p>
                    <p className="mt-1 text-xs text-slate-400">{f.note}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Probabilities */}
          {Object.keys(probabilities).length > 0 && (
            <div className="card">
              <h2 className="text-lg font-black">Probabilities</h2>
              <div className="mt-3 grid grid-cols-5 gap-2 text-center text-sm">
                {[
                  { label: "Home", value: probabilities.home_win },
                  { label: "Draw", value: probabilities.draw },
                  { label: "Away", value: probabilities.away_win },
                  { label: "Over 2.5", value: probabilities.over25 },
                  { label: "BTTS", value: probabilities.btts },
                ].map((item) => (
                  <div key={item.label} className="rounded-xl bg-slate-950 p-3">
                    <p className="text-xs text-slate-500">{item.label}</p>
                    <p className="mt-1 font-black">{item.value !== undefined ? `${Math.round(item.value * 100)}%` : "-"}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Odds History */}
          {(prediction.odds_snapshots || []).length > 0 && (
            <div className="card">
              <h2 className="text-lg font-black">Odds History</h2>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead><tr className="text-slate-400"><th>Phase</th><th>Home</th><th>Draw</th><th>Away</th><th>Book</th></tr></thead>
                  <tbody>
                    {prediction.odds_snapshots.map((o: any, i: number) => (
                      <tr key={i} className="border-t border-slate-800">
                        <td className="py-2 capitalize">{o.phase}</td>
                        <td>{o.home_odds?.toFixed(2) || "-"}</td>
                        <td>{o.draw_odds?.toFixed(2) || "-"}</td>
                        <td>{o.away_odds?.toFixed(2) || "-"}</td>
                        <td className="text-slate-500">{o.bookmaker || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          {/* Consensus Meter */}
          {consensus.length > 0 && (
            <div className="card">
              <h2 className="text-lg font-black">Community Consensus</h2>
              <div className="mt-3 space-y-2">
                {consensus.map((c: any) => (
                  <div key={c.pick}>
                    <div className="flex items-center justify-between text-sm">
                      <span className="font-bold">{c.pick}</span>
                      <span className="text-emerald-300">{c.percent}%</span>
                    </div>
                    <div className="mt-1 h-3 rounded-full bg-slate-800">
                      <div className="h-3 rounded-full bg-emerald-400 transition-all" style={{ width: `${c.percent}%` }} />
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500">{c.count} pick{c.count === 1 ? "" : "s"}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Experts Toggle */}
          <div className="flex items-center justify-between rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
            <div>
              <p className="font-bold">Show Verified Experts Only</p>
              <p className="text-xs text-slate-400">Only show picks from users with 60%+ win rate</p>
            </div>
            <button
              onClick={() => setExpertsOnly(!expertsOnly)}
              className={`relative h-6 w-11 rounded-full transition-colors ${expertsOnly ? "bg-emerald-400" : "bg-slate-700"}`}
            >
              <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${expertsOnly ? "left-5.5" : "left-0.5"}`} />
            </button>
          </div>

          {/* Community Slips */}
          {filteredEntries.length > 0 ? (
            filteredEntries.map((entry: any) => (
              <div key={entry.id} className="card group transition-all hover:border-slate-700">
                <div className="flex items-center justify-between gap-3">
                  <Link href={`/community/profile/${entry.username}`} className="font-bold text-white hover:text-emerald-300">
                    {entry.username}
                  </Link>
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-slate-800 px-2 py-1 text-xs text-slate-400">{entry.market}</span>
                    <span className={`rounded-full px-2 py-1 text-xs font-bold ${
                      !entry.is_settled ? "bg-sky-400/10 text-sky-300" :
                      entry.was_correct ? "bg-emerald-400/10 text-emerald-300" : "bg-rose-400/10 text-rose-300"
                    }`}>
                      {entry.pick}
                    </span>
                  </div>
                </div>
                {entry.analysis_text && (
                  <p className="mt-3 text-sm text-slate-300">{entry.analysis_text}</p>
                )}
                <p className="mt-2 text-xs text-slate-500">
                  {entry.created_at ? new Date(entry.created_at).toLocaleDateString() : ""}
                  {entry.is_settled && (
                    <span className={entry.was_correct ? "text-emerald-300" : "text-rose-300"}>
                      {" • "}{entry.was_correct ? "✅ Won" : "❌ Lost"}
                    </span>
                  )}
                </p>
              </div>
            ))
          ) : (
            <div className="card text-center">
              <p className="text-slate-400">{expertsOnly ? "No expert picks on this match yet." : "No community picks yet."}</p>
              <Link href="/predictions/submit" className="mt-3 inline-flex rounded-xl bg-emerald-400 px-4 py-2 text-sm font-bold text-slate-950">+ Be the first</Link>
            </div>
          )}
        </div>
      )}

      <section className="responsible-note mt-6">
        <b>Play smart:</b> {prediction.responsible_note || "No pick is guaranteed. Never stake more than you can afford to lose."}
      </section>
    </main>
  );
}