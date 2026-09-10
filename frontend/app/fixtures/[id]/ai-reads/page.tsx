import Link from "next/link";
import { getAIReads } from "../../../../lib/api";
import { PredictionCard } from "../../../../components/PredictionCard";
import { BackButton } from "../../../../components/BackButton";

export const dynamic = "force-dynamic";

function formatDate(value?: string) {
  if (!value) return "Time TBA";
  return new Intl.DateTimeFormat("en", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function formatSport(value?: string) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
}

function Timeline({ intelligence }: { intelligence?: any }) {
  const timeline = Array.isArray(intelligence?.timeline) ? intelligence.timeline : [];
  const revisions = Array.isArray(intelligence?.revisions) ? intelligence.revisions : [];
  const market = intelligence?.market;
  if (!timeline.length && !revisions.length && !market) return null;
  return (
    <section className="mt-8 grid gap-4 lg:grid-cols-[.8fr_1.2fr]">
      <div className="card">
        <p className="section-kicker">Match story</p>
        <h2 className="section-title">What changed?</h2>
        <p className="mt-2 text-sm text-slate-500">The important moments in REEDS' read, from the first analysis to the latest update.</p>
        <ol className="mt-5 space-y-4">
          {timeline.slice(0, 12).map((t: any, i: number) => (
            <li key={i} className="relative pl-5">
              <span className="absolute left-0 top-1.5 h-2 w-2 rounded-full bg-emerald-400/70" />
              {i < Math.min(timeline.length, 12) - 1 && <span className="absolute left-[3px] top-4 h-[calc(100%+4px)] w-px bg-white/[0.07]" />}
              <p className="text-sm font-bold text-slate-200">{t.title}</p>
              {t.detail && <p className="mt-1 text-xs leading-relaxed text-slate-500">{t.detail}</p>}
              {t.ts && <p className="mt-1 text-[10px] text-slate-700">{t.ts}</p>}
            </li>
          ))}
        </ol>
      </div>
      <div className="space-y-4">
        {revisions.length > 0 && <div className="card">
          <p className="section-kicker">Prediction history</p>
          <h2 className="section-title">If REEDS changed its mind, you'll see it.</h2>
          <div className="mt-5 space-y-3">
            {revisions.slice(0, 4).map((rev: any, i: number) => <div key={i} className="rounded-2xl border border-white/[0.06] bg-slate-950/50 p-4">
              <div className="flex items-center justify-between gap-3"><p className="text-sm font-bold text-slate-200">{rev.market}</p>{rev.latest && <span className="text-[10px] font-bold uppercase tracking-wide text-emerald-300">Current</span>}</div>
              <p className="mt-2 text-base font-black text-emerald-300">{(rev.versions || []).map((v: any) => v.pick).filter(Boolean).join(" → ")}</p>
              <div className="mt-3 flex flex-wrap gap-2">{(rev.versions || []).map((v: any, j: number) => <span key={j} className="rounded-lg bg-slate-900 px-2.5 py-1 text-[10px] text-slate-500">v{v.version} · {v.confidence?.toFixed?.(0) ?? "—"}%</span>)}</div>
            </div>)}
          </div>
        </div>}
        {market && <div className="card">
          <p className="section-kicker">Market context</p>
          <h2 className="section-title">How the price moved</h2>
          <div className="mt-5 grid grid-cols-3 gap-2 text-center">
            {[["Opening", market.opening], ["Current", market.current || market.fixture_line], ["Closing", market.closing]].map(([label, values]: any) => <div key={label} className="rounded-xl bg-slate-950/60 p-3"><p className="text-[10px] uppercase tracking-wide text-slate-600">{label}</p><p className="mt-1 text-xs font-bold text-slate-300">{values?.home_odds ?? "—"} / {values?.draw_odds ?? "—"} / {values?.away_odds ?? "—"}</p></div>)}
          </div>
          {market.pick_side?.available && <p className="mt-4 text-xs leading-relaxed text-slate-500">For this read, the market implied {market.pick_side.market_probability}% while REEDS estimated {market.pick_side.model_probability}%. Difference: {market.pick_side.edge_pp >= 0 ? "+" : ""}{market.pick_side.edge_pp} points.</p>}
        </div>}
      </div>
    </section>
  );
}

export default async function FixtureAIReads({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const data = await getAIReads(id);
  if (!data?.fixture) return <main className="mx-auto max-w-4xl px-4 py-10"><BackButton fallback="/fixtures" /><div className="card mt-6">This match isn't available right now.</div></main>;
  const f = data.fixture;
  const picks = Array.isArray(data.predictions) ? data.predictions : [];
  const sources = Array.isArray(f.provider_sources) ? f.provider_sources : [];

  return (
    <main className="mx-auto max-w-7xl px-4 pb-12 pt-7 sm:px-6 sm:pt-10">
      <div className="flex items-center gap-3 text-xs font-bold"><BackButton fallback="/fixtures" /><span className="text-slate-700">/</span><span className="text-emerald-300">Intelligence</span></div>
      <section className="mt-5 overflow-hidden rounded-[1.5rem] border border-white/[0.07] bg-gradient-to-br from-slate-900 to-slate-950 p-5 sm:p-7">
        <div className="flex flex-wrap items-center gap-2"><span className="badge">{formatSport(f.sport)}</span><span className="text-xs text-slate-600">{f.league}</span>{sources.slice(0, 3).map((source: string) => <span key={source} className="text-[10px] text-slate-700">· {source}</span>)}</div>
        <div className="mt-6 grid gap-5 md:grid-cols-[1fr_auto_1fr] md:items-center">
          <div><p className="text-xl font-black text-white sm:text-3xl">{f.home_team}</p><p className="mt-1 text-xs text-slate-600">Home</p></div>
          <div className="text-center"><p className="text-xs font-bold uppercase tracking-[.16em] text-slate-600">{f.home_score != null && f.away_score != null ? `${f.home_score} — ${f.away_score}` : "VS"}</p><p className="mt-2 text-[11px] text-slate-500">{formatDate(f.match_date)}</p>{f.status && <p className="mt-1 text-[10px] text-emerald-400/70">{f.status}{f.is_live && f.elapsed ? ` · ${f.elapsed}'` : ""}</p>}</div>
          <div className="text-left md:text-right"><p className="text-xl font-black text-white sm:text-3xl">{f.away_team}</p><p className="mt-1 text-xs text-slate-600">Away</p></div>
        </div>
      </section>
      {data.status === "preparing" || data.status === "insufficient_data" ? (
        <section className="card mt-6 border-amber-400/15">
          <p className="section-kicker">REEDS intelligence check</p>
          <h2 className="section-title">This match is detected.</h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">Analysis unavailable until evidence reaches the required threshold.</p>
          <ul className="mt-5 space-y-2 text-sm">
            <li className="flex items-center gap-2 text-emerald-300"><span aria-hidden>✓</span> Fixture found</li>
            <li className={`flex items-center gap-2 ${data.evidence_checklist?.league_identified !== false ? "text-emerald-300" : "text-rose-300"}`}>
              <span aria-hidden>{data.evidence_checklist?.league_identified !== false ? "✓" : "✕"}</span> League identified
            </li>
            <li className={`flex items-center gap-2 ${data.evidence_checklist?.odds_present ? "text-emerald-300" : "text-rose-300"}`}>
              <span aria-hidden>{data.evidence_checklist?.odds_present ? "✓" : "✕"}</span> Odds market
            </li>
            <li className={`flex items-center gap-2 ${data.evidence_checklist?.history_present ? "text-emerald-300" : "text-rose-300"}`}>
              <span aria-hidden>{data.evidence_checklist?.history_present ? "✓" : "✕"}</span> Historical team data
            </li>
          </ul>
          <p className="mt-4 text-xs text-slate-500">REEDS will publish when evidence reaches the required threshold.</p>
        </section>
      ) : (
        <>
          <section className="mt-8">
            <div className="flex items-end justify-between gap-4"><div><p className="section-kicker">The read</p><h2 className="section-title">What REEDS sees</h2><p className="mt-2 text-sm text-slate-500">Start with the decision. Open a card only when you want the evidence.</p></div><span className="text-xs text-slate-600">{picks.length} read{picks.length === 1 ? "" : "s"}</span></div>
            {data.status === "draft" && <div className="mt-4 rounded-2xl border border-amber-400/15 bg-amber-400/[0.05] p-4 text-sm text-slate-400"><span className="font-bold text-amber-300">Early read.</span> REEDS has generated this analysis, but the evidence is not strong enough to treat it as a published recommendation.</div>}
            <div className="mt-5 grid gap-5 md:grid-cols-2">{picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="card text-sm text-slate-500">No public reads for this match yet.</div>}</div>
          </section>
          <Timeline intelligence={data.intelligence} />
        </>
      )}
      {data.responsible_note && <section className="responsible-note mt-8">{data.responsible_note}</section>}
    </main>
  );
}
