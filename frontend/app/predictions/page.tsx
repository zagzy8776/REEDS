import { PredictionCard } from "../../components/PredictionCard";
import { getTodayPredictions } from "../../lib/api";

export const dynamic = "force-dynamic";

export default async function Predictions() {
  const picks = await getTodayPredictions();
  return <main className="mx-auto max-w-6xl px-6 py-10"><h1 className="text-4xl font-black">Today’s EDGE Picks</h1><p className="mt-2 text-slate-400">Football and basketball selections published after model refresh. Picks are probability-rated, not guarantees.</p><div className="mt-8 grid gap-5 md:grid-cols-2">{picks.length ? picks.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="card text-slate-400">No public picks yet. Today’s board will appear after the next LOYAL EDGE refresh.</div>}</div></main>;
}
