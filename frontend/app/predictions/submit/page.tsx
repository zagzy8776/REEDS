"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Fixture {
  id: number;
  match_date: string;
  league: string;
  home_team: string;
  away_team: string;
}

const MARKETS = ["1X2", "Goals", "BTTS"];

export default function SubmitPick() {
  const router = useRouter();
  const [fixtures, setFixtures] = useState<Fixture[]>([]);
  const [selectedFixture, setSelectedFixture] = useState<Fixture | null>(null);
  const [step, setStep] = useState(0); // 0=pick match, 1=pick selection, 2=add note
  const [market, setMarket] = useState("1X2");
  const [pick, setPick] = useState("");
  const [analysis, setAnalysis] = useState("");
  const [username, setUsername] = useState("");
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${API_URL}/api/fixtures/upcoming?limit=50`)
      .then((r) => r.json())
      .then(setFixtures)
      .catch(() => {});
  }, []);

  useEffect(() => {
    const saved = localStorage.getItem("loyal_edge_username");
    if (saved) setUsername(saved);
  }, []);

  const handleSubmit = async () => {
    if (!selectedFixture || !pick || !username) return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API_URL}/api/community/predictions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username,
          fixture_id: selectedFixture.id,
          market,
          pick,
          analysis_text: analysis.slice(0, 140),
        }),
      });
      if (res.ok) {
        localStorage.setItem("loyal_edge_username", username);
        setSuccess(true);
        setTimeout(() => router.push("/community-leaderboard"), 1500);
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Failed to post. Try again.");
      }
    } catch {
      setError("Network error. Check your connection.");
    } finally {
      setLoading(false);
    }
  };

  const getPickOptions = () => {
    if (market === "1X2") return ["Home Win", "Draw", "Away Win"];
    if (market === "Goals") return ["Over 2.5", "Under 2.5", "Over 1.5", "Under 1.5"];
    return ["BTTS Yes", "BTTS No"];
  };

  if (success) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <div className="card max-w-sm text-center">
          <div className="text-6xl">🎉</div>
          <h1 className="mt-4 text-2xl font-black">Pick Posted!</h1>
          <p className="mt-2 text-slate-400">Your pick is live in the community. Good luck!</p>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-2xl px-4 py-6 sm:px-6">
      <div className="mb-6 flex items-center gap-3">
        <Link href="/community-leaderboard" className="text-slate-400 hover:text-white">← Back</Link>
        <h1 className="text-2xl font-black">Post a Pick</h1>
      </div>

      {/* Progress dots */}
      <div className="mb-6 flex items-center justify-center gap-2">
        {[0, 1, 2].map((i) => (
          <div key={i} className={`h-2 w-2 rounded-full transition-colors ${i <= step ? "bg-emerald-400" : "bg-slate-700"}`} />
        ))}
        <span className="ml-2 text-xs text-slate-500">Step {step + 1}/3</span>
      </div>

      {/* Username (always visible if not set) */}
      {!username && (
        <div className="mb-4">
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Your tipster name"
            className="w-full rounded-xl border border-slate-800 bg-slate-950 p-4 text-lg"
          />
        </div>
      )}

      {/* Step 0: Select Match */}
      {step === 0 && (
        <div>
          <h2 className="mb-3 text-lg font-bold">Pick a match</h2>
          <div className="flex gap-3 overflow-x-auto pb-4 snap-x snap-mandatory">
            {fixtures.map((f) => (
              <button
                key={f.id}
                onClick={() => { setSelectedFixture(f); setStep(1); }}
                className={`min-w-[200px] snap-start rounded-2xl border p-4 text-left transition-all ${
                  selectedFixture?.id === f.id
                    ? "border-emerald-400 bg-emerald-400/10"
                    : "border-slate-800 bg-slate-900/50 hover:border-slate-600"
                }`}
              >
                <p className="text-xs text-slate-500">{f.league}</p>
                <p className="mt-1 text-xs text-slate-500">{f.match_date}</p>
                <p className="mt-2 font-bold">{f.home_team}</p>
                <p className="text-sm text-slate-400">vs</p>
                <p className="font-bold">{f.away_team}</p>
              </button>
            ))}
            {fixtures.length === 0 && (
              <p className="text-slate-400">No upcoming fixtures found.</p>
            )}
          </div>
        </div>
      )}

      {/* Step 1: Select Pick */}
      {step === 1 && selectedFixture && (
        <div>
          <div className="mb-4 rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
            <p className="text-xs text-slate-500">{selectedFixture.league}</p>
            <p className="font-bold">{selectedFixture.home_team} vs {selectedFixture.away_team}</p>
          </div>

          {/* Market selector */}
          <div className="mb-4 flex gap-2">
            {MARKETS.map((m) => (
              <button
                key={m}
                onClick={() => { setMarket(m); setPick(""); }}
                className={`rounded-xl px-4 py-2 text-sm font-bold transition-colors ${
                  market === m ? "bg-emerald-400 text-slate-950" : "border border-slate-800 bg-slate-950"
                }`}
              >
                {m}
              </button>
            ))}
          </div>

          {/* Pick buttons - large and thumb-friendly */}
          <div className="grid grid-cols-3 gap-3">
            {getPickOptions().map((option) => (
              <button
                key={option}
                onClick={() => { setPick(option); setStep(2); }}
                className={`rounded-2xl border-2 p-6 text-center font-bold transition-all ${
                  pick === option
                    ? "border-emerald-400 bg-emerald-400/10 text-emerald-300"
                    : "border-slate-800 bg-slate-900 hover:border-slate-600 active:scale-95"
                }`}
              >
                {option}
              </button>
            ))}
          </div>

          <button onClick={() => setStep(0)} className="mt-4 text-sm text-slate-400 hover:text-white">← Change match</button>
        </div>
      )}

      {/* Step 2: Add note */}
      {step === 2 && selectedFixture && (
        <div>
          <div className="mb-4 rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
            <p className="text-xs text-slate-500">{selectedFixture.league}</p>
            <p className="font-bold">{selectedFixture.home_team} vs {selectedFixture.away_team}</p>
            <p className="mt-1 text-emerald-300">{market} → {pick}</p>
          </div>

          <textarea
            value={analysis}
            onChange={(e) => setAnalysis(e.target.value.slice(0, 140))}
            placeholder="Why do you like this pick? (optional)"
            rows={3}
            className="w-full rounded-xl border border-slate-800 bg-slate-950 p-4"
          />
          <p className="mt-1 text-right text-xs text-slate-500">{analysis.length}/140</p>

          <div className="mt-4 flex gap-3">
            <button onClick={() => setStep(1)} className="rounded-xl border border-slate-800 px-4 py-3">← Back</button>
            <button
              onClick={handleSubmit}
              disabled={loading || !username}
              className="flex-1 rounded-xl bg-emerald-400 px-5 py-3 font-bold text-slate-950 disabled:opacity-50"
            >
              {loading ? "Posting..." : "🚀 Post Pick"}
            </button>
          </div>

          {error && <p className="mt-3 rounded-xl bg-rose-500/10 p-3 text-sm text-rose-300">{error}</p>}
        </div>
      )}

      <section className="responsible-note mt-8">
        <b>Play smart:</b> Community picks are opinions, not guarantees. Records can change fast.
      </section>
    </main>
  );
}