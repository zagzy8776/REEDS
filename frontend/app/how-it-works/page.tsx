import Link from "next/link";

export const dynamic = "force-dynamic";

const STEPS = [
  {
    title: "1 · The model reads every fixture",
    tone: "text-sky-300",
    body: "REEDS pulls fixtures and odds from multiple feeds, checks form, goals, xG, streaks, head-to-head, and odds movement, and produces probabilities per market. Draft reads are never presented as published picks.",
  },
  {
    title: "2 · REEDS classifies its own verdict",
    tone: "text-emerald-300",
    body: "STRONG READ (published, high confidence + edge), REEDS VALUE (published, passed the empirical evidence gate), ANALYZED — LOW EVIDENCE (model has a view, but not enough validated evidence to recommend), or WATCHLIST (insufficient data). Drafts stay clearly labelled.",
  },
  {
    title: "3 · The market is part of the analysis",
    tone: "text-amber-300",
    body: "For 1X2 reads with real odds we show implied probability vs the model's probability and the edge in points. When there's no real price, we say 'not available' instead of inventing one.",
  },
  {
    title: "4 · Every read settles in public",
    tone: "text-rose-300",
    body: "After the fixture ends, the result is attached to the read — won, lost, or pending. Post-match feedback explains what held and what failed, using the same signals the model cited.",
  },
  {
    title: "5 · The record is classified honestly",
    tone: "text-violet-300",
    body: "LIVE RECORD = published reads that actually settled. BACKTEST = simulated on older data before going live. HISTORICAL EVALUATION = walk-forward out-of-sample runs. They are shown side-by-side and never blended into one fake number.",
  },
  {
    title: "6 · Calibration changes only after evidence",
    tone: "text-slate-300",
    body: "One loss never changes the model. Repeated failures in the same dimension are flagged as candidates — and even then, they require validation before any calibrated adjustment is applied. You can see those candidates on the Performance page.",
  },
];

const FAQ = [
  { q: "Why do some cards say ANALYZED instead of a pick?", a: "REEDS generates a read for nearly every fixture. Publication is gated on real empirical evidence. When that evidence isn't there yet, the read is shown as analysed — not as a recommendation disguised as one." },
  { q: "Is the hit rate real?", a: "Yes — it is computed only from published picks that settle. Pending picks don't count. Backtests and historical evaluations are labelled and kept separate from the live record." },
  { q: "What is CLV / closing line value?", a: "When the market's closing price is worse than the price of a published read, the read beat the closing line. We track this only when real odds are stored, and show the count." },
  { q: "How do I read confidence and risk?", a: "Confidence is the model probability for the pick. Risk is how spread out the outcome is. A high-confidence low-risk read is a REEDS STRONG READ; everything else shows its limits." },
];

export default function HowItWorks() {
  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-12">
      <section className="mx-auto max-w-3xl text-center">
        <p className="badge inline-block">Transparency in practice</p>
        <h1 className="mt-4 text-4xl font-black sm:text-5xl">How REEDS makes and proves its reads.</h1>
        <p className="mt-4 text-slate-300">
          Six steps, no smoke. Every claim on the product maps to something real in the data, and every number you can't trust is labelled as such.
        </p>
      </section>

      <section className="mt-12 grid gap-5 md:grid-cols-2">
        {STEPS.map((s) => (
          <div key={s.title} className="card">
            <h2 className={`text-lg font-black ${s.tone}`}>{s.title}</h2>
            <p className="mt-2 text-sm leading-relaxed text-slate-300">{s.body}</p>
          </div>
        ))}
      </section>

      <section className="mt-14">
        <h2 className="text-2xl font-black">Straight answers</h2>
        <div className="mt-4 space-y-3">
          {FAQ.map((item) => (
            <details key={item.q} className="group rounded-2xl border border-white/10 bg-slate-900/60 p-4">
              <summary className="cursor-pointer list-none font-bold text-slate-100 group-open:text-emerald-300">
                {item.q}
                <span className="ml-2 text-slate-600 group-open:hidden">▸</span>
                <span className="ml-2 hidden text-slate-600 group-open:inline">▾</span>
              </summary>
              <p className="mt-3 text-sm leading-relaxed text-slate-300">{item.a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="mt-14 card">
        <div className="grid gap-6 md:grid-cols-2 md:items-center">
          <div>
            <h2 className="text-2xl font-black">See it on a real record</h2>
            <p className="mt-2 text-slate-400">The track record and performance pages apply all of this with live data.</p>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row md:justify-end">
            <Link href="/history" className="rounded-xl bg-emerald-400 px-5 py-3 text-center font-black text-slate-950">Track record</Link>
            <Link href="/performance" className="rounded-xl border border-white/10 bg-white/5 px-5 py-3 text-center font-bold">Performance center</Link>
          </div>
        </div>
      </section>
    </main>
  );
}