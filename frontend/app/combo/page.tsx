import { PredictionCard } from "../../components/PredictionCard";
import { getCombo } from "../../lib/api";

export default async function Combo() {
  const combo = await getCombo();
  return <main className="mx-auto max-w-5xl px-6 py-10"><h1 className="text-4xl font-black">{combo.label || "LOYAL EDGE 3-Leg Combo"}</h1><p className="mt-2 text-slate-400">Combined EDGE: {combo.combined_confidence || 0}% • Risk: {combo.risk_level || "N/A"}</p><div className="mt-8 grid gap-5">{(combo.legs || []).map((p: any) => <PredictionCard key={p.id} p={p} />)}</div></main>;
}
