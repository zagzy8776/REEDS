"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { API_URL, getLiveEvents, getLiveMatches, getLatestAlerts } from "../lib/api";

const STATUS_LABEL: Record<string, string> = {
  "1H": "1st half", "2H": "2nd half", HT: "Half time", ET: "Extra time",
  BT: "Break time", P: "Paused", LIVE: "Live", INT: "Interrupted",
};

const EVENT_TONE: Record<string, string> = {
  goal: "text-emerald-300 border-emerald-400/30",
  score_update: "text-emerald-300 border-emerald-400/30",
  penalty_missed: "text-amber-300 border-amber-400/30",
  red_card: "text-rose-300 border-rose-400/30",
  yellow_card: "text-amber-300 border-amber-400/30",
  substitution: "text-sky-300 border-sky-400/30",
  live_intelligence: "text-violet-300 border-violet-400/30",
};

const CLOCK_STATUSES = new Set(["1H", "2H", "ET", "LIVE"]);

function liveClock(match: any, now: number) {
  const base = Number(match?.elapsed);
  if (!Number.isFinite(base)) return null;
  const status = String(match?.status || "").toUpperCase();
  if (!CLOCK_STATUSES.has(status)) return `${base}'`;
  const synced = Date.parse(String(match?.last_synced_at || ""));
  const extraMinutes = Number.isFinite(synced) ? Math.max(0, Math.floor((now - synced) / 60000)) : 0;
  return `${base + extraMinutes}'`;
}

function statValue(value: any) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function pressureLabel(value: any) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

export default function LiveHub({ initialMatchIds }: { initialMatchIds: number[] }) {
  const [matches, setMatches] = useState<any[]>([...initialMatchIds.map((id) => ({ id, _loading: true }))]);
  const [eventsByFixture, setEventsByFixture] = useState<Record<number, any[]>>({});
  const [alerts, setAlerts] = useState<any[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [lastRefresh, setLastRefresh] = useState<string>("");

  useEffect(() => {
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(clock);
  }, []);

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
    const timer = window.setInterval(tick, 15000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    const liveIds = matches.filter((m) => !m._loading && m.id).map((m) => Number(m.id));
    const streams: EventSource[] = [];
    for (const id of liveIds) {
      const stream = new EventSource(`${API_URL}/api/live/stream/${id}`);
      stream.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data);
          if (event.type === "connected") return;
          setMatches((prev) => prev.map((m) => {
            if (Number(m.id) !== id) return m;
            const next = { ...m };
            if (event.home_score !== undefined) next.home_score = event.home_score;
            if (event.away_score !== undefined) next.away_score = event.away_score;
            if (event.minute !== undefined && event.minute !== null) next.elapsed = event.minute;
            if (event.status) next.status = event.status;
            if (event.intelligence) next.intelligence = event.intelligence;
            next.last_synced_at = new Date().toISOString();
            if (event.stats) next.stats = event.stats;
            if (event.event_type === "stats_update") next.stats_updated_at = new Date().toISOString();
            return next;
          }));
          if (event.event_type && event.event_type !== "stats_update") {
            setEventsByFixture((prev) => ({ ...prev, [id]: [...(prev[id] || []), event].slice(-30) }));
          }
        } catch {
          // REST refresh remains the authoritative fallback if an SSE frame is malformed.
        }
      };
      streams.push(stream);
    }
    return () => streams.forEach((stream) => stream.close());
  }, [matches.map((m) => `${m.id}:${m.status}`).join("|")]);

  const loadEvents = async (id: number) => {
    const data = await getLiveEvents(id);
    if (Array.isArray(data?.events)) setEventsByFixture((prev) => ({ ...prev, [id]: data.events }));
    if (data) {
      setMatches((prev) => prev.map((m) => Number(m.id) === id ? {
        ...m,
        home_score: data.home_score ?? m.home_score,
        away_score: data.away_score ?? m.away_score,
        status: data.status ?? m.status,
        elapsed: data.elapsed ?? m.elapsed,
        stats: data.stats ?? m.stats,
        intelligence: data.intelligence ?? m.intelligence,
        last_synced_at: data.last_synced_at ?? m.last_synced_at,
        stats_updated_at: data.stats_updated_at ?? m.stats_updated_at,
      } : m));
    }
  };

  const live = matches.filter((m) => !m._loading);

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xl font-bold">Live right now</h2>
        <div className="flex items-center gap-3 text-xs text-slate-500">
          {lastRefresh ? <span>Live feed • SSE + fallback refresh</span> : <span>Connecting…</span>}
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 animate-pulse rounded-full bg-red-400" />
            {live.filter((m) => m.status !== "HT" && m.status !== "BT" && m.status !== "INT").length} in play
          </span>
        </div>
      </div>

      {!live.length && (
        <div className="mt-4 rounded-2xl border border-dashed border-white/10 bg-white/5 p-6 text-center">
          <p className="font-bold text-slate-200">No matches are in play right now.</p>
          <p className="mt-1 text-sm text-slate-400">The live feed checks the provider state continuously. When a tracked fixture becomes live, its score, clock, statistics and live read will appear automatically.</p>
          <Link href="/predictions" className="mt-4 inline-flex rounded-xl bg-emerald-400 px-4 py-2 text-sm font-black text-slate-950">Today's reads</Link>
        </div>
      )}

      <div className="mt-4 space-y-4">
        {live.map((m) => {
          const stats = m.stats && typeof m.stats === "object" ? Object.entries(m.stats).slice(0, 10) : [];
          const intelligence = m.intelligence && typeof m.intelligence === "object" ? m.intelligence : {};
          const drivers = Array.isArray(intelligence.drivers) ? intelligence.drivers : [];
          const evidence = intelligence.evidence && typeof intelligence.evidence === "object" ? intelligence.evidence : {};
          return (
            <div key={m.id} className="card">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs uppercase tracking-wide text-slate-500">{m.sport} • {m.league}</p>
                  <p className="mt-1 truncate text-lg font-bold">{m.home_team} vs {m.away_team}</p>
                </div>
                <div className="text-right">
                  <p className="text-2xl font-black">{m.home_score ?? "-"} : {m.away_score ?? "-"}</p>
                  <p className="text-xs font-bold text-slate-400">{STATUS_LABEL[(m.status || "").toUpperCase()] || m.status || "Live"}{liveClock(m, now) ? ` • ${liveClock(m, now)}` : ""}</p>
                </div>
              </div>

              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                {m.home_odds ? <span className="rounded-full border border-slate-700 px-2 py-0.5 text-slate-300">Home {m.home_odds}</span> : null}
                {m.draw_odds ? <span className="rounded-full border border-slate-700 px-2 py-0.5 text-slate-300">Draw {m.draw_odds}</span> : null}
                {m.away_odds ? <span className="rounded-full border border-slate-700 px-2 py-0.5 text-slate-300">Away {m.away_odds}</span> : null}
                <Link href={`/fixtures/${m.id}/ai-reads`} className="rounded-full border border-sky-400/30 bg-sky-400/10 px-2 py-0.5 font-bold text-sky-200">AI Reads</Link>
                <button onClick={() => loadEvents(Number(m.id))} className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 font-bold text-slate-300 hover:bg-white/10">Refresh data</button>
              </div>

              {(intelligence.pressure || drivers.length > 0 || Object.keys(evidence).length > 0) && (
                <div className="mt-4 rounded-xl border border-violet-400/15 bg-violet-400/[0.04] p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs font-black uppercase tracking-wider text-violet-300">Live read</p>
                    <span className="text-xs font-bold text-slate-300">{pressureLabel(intelligence.pressure) || "Insufficient live evidence"}</span>
                  </div>
                  {drivers.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {drivers.slice(0, 4).map((driver: any, i: number) => (
                        <span key={i} className="rounded-full border border-white/10 bg-slate-950/60 px-2 py-1 text-xs text-slate-300">{driver}</span>
                      ))}
                    </div>
                  )}
                  {Object.keys(evidence).length > 0 && (
                    <p className="mt-2 text-[11px] text-slate-500">
                      Evidence: {Object.entries(evidence).filter(([, value]) => Boolean(value)).map(([key]) => key.replaceAll("_", " ")).join(" • ") || "provider stats pending"}
                    </p>
                  )}
                  <p className="mt-2 text-[10px] text-slate-600">Provider statistics only. No invented live probability.</p>
                </div>
              )}

              {stats.length > 0 && (
                <div className="mt-4 rounded-xl border border-white/10 bg-slate-950/50 p-3">
                  <div className="flex items-center justify-between"><p className="text-xs font-black uppercase tracking-wider text-slate-500">Live statistics</p><span className="text-[10px] text-slate-600">provider data</span></div>
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    {stats.map(([name, value]: any) => (
                      <div key={name} className="flex items-center justify-between rounded-lg border border-white/5 px-3 py-2 text-xs"><span className="text-slate-400">{name}</span><span className="font-bold text-slate-200">{statValue(value?.home)} — {statValue(value?.away)}</span></div>
                    ))}
                  </div>
                </div>
              )}

              {eventsByFixture[m.id]?.length ? (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {eventsByFixture[m.id].map((ev: any, i: number) => (
                    <div key={`${ev.id || "live"}-${i}`} className={`rounded-lg border bg-slate-950/60 p-2 text-xs ${EVENT_TONE[ev.event_type] || "border-slate-800 text-slate-400"}`}>
                      <span className="font-bold">{ev.label || ev.event_type}</span><span className="ml-1 text-slate-500">{ev.minute ? `${ev.minute}'` : ""}</span>
                      {ev.score ? <span className="ml-1 text-slate-400">{ev.score}</span> : null}
                      {ev.player ? <span className="ml-1 text-slate-400">{ev.player}</span> : null}
                      {ev.detail && ev.event_type === "score_update" ? <span className="ml-1 text-slate-500">{ev.detail}</span> : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {alerts.length > 0 && (
        <section className="mt-10">
          <h2 className="text-xl font-bold">Latest tracked signals</h2>
          <div className="mt-3 grid gap-2">
            {alerts.slice(0, 8).map((a: any, i: number) => (
              <div key={i} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-sm">
                <div><p className="font-bold text-slate-200">{a.title}</p><p className="text-xs text-slate-500">{a.message}</p></div>
                <span className="shrink-0 text-xs text-slate-500">{a.time_label || a.occurred_at || ""}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
