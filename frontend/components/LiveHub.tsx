"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getLiveEvents, getLiveMatches, getLatestAlerts } from "../lib/api";

const STATUS_LABEL: Record<string, string> = {
  "1H": "1st half", "2H": "2nd half", HT: "Half time", ET: "Extra time",
  BT: "Break time", P: "Paused", LIVE: "Live", INT: "Interrupted",
};

const EVENT_TONE: Record<string, string> = {
  goal: "text-emerald-300 border-emerald-400/30",
  penalty_missed: "text-amber-300 border-amber-400/30",
  red_card: "text-rose-300 border-rose-400/30",
  yellow_card: "text-amber-300 border-amber-400/30",
  substitution: "text-sky-300 border-sky-400/30",
};

export default function LiveHub({ initialMatchIds }: { initialMatchIds: number[] }) {
  const [matches, setMatches] = useState<any[]>([...initialMatchIds.map((id) => ({ id, _loading: true }))]);
  const [eventsByFixture, setEventsByFixture] = useState<Record<number, any[]>>({});
  const [alerts, setAlerts] = useState<any[]>([]);
  const [lastRefresh, setLastRefresh] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const [m, a] = await Promise.all([getLiveMatches(), getLatestAlerts()]);
      if (cancelled) return;
      setMatches(Array.isArray(m) ? m : []);
      setAlerts(Array.isArray(a) ? a : []);
      setLastRefresh(new Date().toLocaleTimeString());
    };
    tick();
    const timer = setInterval(tick, 15000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  const loadEvents = async (id: number) => {
    const data = await getLiveEvents(id);
    if (Array.isArray(data?.events)) {
      setEventsByFixture((prev) => ({ ...prev, [id]: data.events }));
    }
  };

  const live = matches.filter((m) => !m._loading);

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xl font-bold">Live right now</h2>
        <div className="flex items-center gap-3 text-xs text-slate-500">
          {lastRefresh ? <span>Updated {lastRefresh} (auto-refresh 15s)</span> : <span>Connecting…</span>}
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 animate-pulse rounded-full bg-red-400" />
            {live.filter((m) => m.status !== "HT" && m.status !== "BT" && m.status !== "INT").length} in play
          </span>
        </div>
      </div>

      {!live.length && (
        <div className="mt-4 rounded-2xl border border-dashed border-white/10 bg-white/5 p-6 text-center">
          <p className="font-bold text-slate-200">No matches are in play right now.</p>
          <p className="mt-1 text-sm text-slate-400">The feed turns live automatically when a tracked fixture kicks off. Check the board for upcoming reads in the meantime.</p>
          <Link href="/predictions" className="mt-4 inline-flex rounded-xl bg-emerald-400 px-4 py-2 text-sm font-black text-slate-950">Today's reads</Link>
        </div>
      )}

      <div className="mt-4 space-y-4">
        {live.map((m) => (
          <div key={m.id} className="card">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs uppercase tracking-wide text-slate-500">{m.sport} • {m.league}</p>
                <p className="mt-1 truncate text-lg font-bold">{m.home_team} vs {m.away_team}</p>
              </div>
              <div className="text-right">
                <p className="text-2xl font-black">{m.home_score ?? "-"} : {m.away_score ?? "-"}</p>
                <p className="text-xs text-slate-500">{STATUS_LABEL[(m.status || "").toUpperCase()] || m.status || "Live"}{m.elapsed ? ` • ${m.elapsed}'` : ""}</p>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              {m.home_odds ? <span className="rounded-full border border-slate-700 px-2 py-0.5 text-slate-300">Home {m.home_odds}</span> : null}
              {m.draw_odds ? <span className="rounded-full border border-slate-700 px-2 py-0.5 text-slate-300">Draw {m.draw_odds}</span> : null}
              {m.away_odds ? <span className="rounded-full border border-slate-700 px-2 py-0.5 text-slate-300">Away {m.away_odds}</span> : null}
              <Link href={`/fixtures/${m.id}/ai-reads`} className="rounded-full border border-sky-400/30 bg-sky-400/10 px-2 py-0.5 font-bold text-sky-200">AI Reads</Link>
              <button
                onClick={() => loadEvents(m.id)}
                className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 font-bold text-slate-300 hover:bg-white/10"
              >
                Refresh events
              </button>
            </div>
            {eventsByFixture[m.id]?.length ? (
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {eventsByFixture[m.id].map((ev: any, i: number) => (
                  <div key={i} className={`rounded-lg border bg-slate-950/60 p-2 text-xs ${EVENT_TONE[ev.event_type] || "border-slate-800 text-slate-400"}`}>
                    <span className="font-bold">{ev.label}</span>
                    <span className="ml-1 text-slate-500">{ev.minute ? `${ev.minute}'` : ""}</span>
                    {ev.score ? <span className="ml-1 text-slate-400">{ev.score}</span> : null}
                    {ev.player ? <span className="ml-1 text-slate-400">{ev.player}</span> : null}
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ))}
      </div>

      {alerts.length > 0 && (
        <section className="mt-10">
          <h2 className="text-xl font-bold">Latest tracked signals</h2>
          <div className="mt-3 grid gap-2">
            {alerts.slice(0, 8).map((a: any, i: number) => (
              <div key={i} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-sm">
                <div>
                  <p className="font-bold text-slate-200">{a.title}</p>
                  <p className="text-xs text-slate-500">{a.message}</p>
                </div>
                <span className="shrink-0 text-xs text-slate-500">{a.time_label || a.occurred_at || ""}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </>
  );
}