import { PredictionCard } from "../../components/PredictionCard";
import { getTodayPredictions } from "../../lib/api";

export default async function Predictions() {
  const picks = await getTodayPredictions();
  return <main className="mx-auto max-w-6xl px-6 py-10"><h1 className="text-4xl font-black">Predictions</h1><p className="mt-2 text-slate-400">Daily LOYAL EDGE picks. Probabilities are not guarantees.</p><div className="mt-8 grid gap-5 md:grid-cols-2">{picks.map((p: any) => <PredictionCard key={p.id} p={p} />)}</div></main>;
}
