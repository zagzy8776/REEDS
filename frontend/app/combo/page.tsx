import { PredictionCard } from "../../components/PredictionCard";
import { getCombo } from "../../lib/api";

export const dynamic = "force-dynamic";

export default async function Combo() {
  const combo = await getCombo();
  return <main className="mx-auto max-w-5xl px-6 py-10"><h1 className="text-4xl font-black">{combo.label || "LOYAL EDGE 3-Leg Combo"}</h1><p className="mt-2 text-slate-400">True compound probability: {combo.combined_confidence || 0}% • Average EDGE: {combo.avg_edge_score || 0}% • Risk: {combo.risk_level || "N/A"}</p><p className="mt-2 text-sm text-slate-500">Combo probability multiplies leg probabilities. A high average EDGE does not mean a low-risk accumulator.</p><div className="mt-8 grid gap-5">{(combo.legs || []).length ? combo.legs.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="card text-slate-400">Combo will appear after predictions are generated.</div>}</div></main>;
}
