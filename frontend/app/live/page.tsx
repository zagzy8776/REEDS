import Link from "next/link";
import { getLatestAlerts, getLiveMatches } from "../../lib/api";
import LiveHub from "../../components/LiveHub";

export const dynamic = "force-dynamic";

function formatSport(value?: string) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

export default async function LiveCenter() {
  const [matches, alerts] = await Promise.all([getLiveMatches(), getLatestAlerts()]);
  const liveMatches = Array.isArray(matches) ? matches : [];
  const initialMatchIds = liveMatches.map((m: any) => Number(m.id)).filter(Boolean);

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <section>
        <p className="badge inline-block">Live center</p>
        <h1 className="mt-4 text-4xl font-black sm:text-5xl">Matches in play, read live.</h1>
        <p className="mt-3 max-w-3xl text-slate-300">
          Score, status, odds, and match events for every tracked fixture that is actually in progress. Updated automatically — no refresh needed.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Link href="/predictions" className="rounded-xl bg-emerald-400 px-5 py-3 font-black text-slate-950">Today's reads</Link>
          <Link href="/fixtures" className="rounded-xl border border-white/10 bg-white/5 px-5 py-3 font-bold">Upcoming fixtures</Link>
        </div>
      </section>

      <section className="mt-8">
        <LiveHub initialMatchIds={initialMatchIds} />
      </section>

      {(!liveMatches.length && Array.isArray(alerts) && alerts.length === 0) && (
        <p className="mt-6 text-xs text-slate-600">
          Coverage: {formatSport("soccer")}, {formatSport("basketball")} and more. Events load as tracked fixture feeds push live data.
        </p>
      )}
    </main>
  );
}