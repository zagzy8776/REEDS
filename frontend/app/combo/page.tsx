import { PredictionCard } from "../../components/PredictionCard";
import { getCombo } from "../../lib/api";

export const dynamic = "force-dynamic";

export default async function Combo() {
  const combo = await getCombo();
  return <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10"><h1 className="text-3xl font-black sm:text-4xl">{combo.label || "LOYAL EDGE 3-leg combo"}</h1><p className="mt-2 text-sm text-slate-400 sm:text-base">Combo chance: {combo.combined_confidence || 0}% • Avg EDGE: {combo.avg_edge_score || 0}% • Risk: {combo.risk_level || "N/A"}</p><p className="mt-2 text-sm text-slate-500">Combos are harder to land because every leg has to win. Good picks can still make a risky slip.</p><div className="mt-8 grid gap-5">{(combo.legs || []).length ? combo.legs.map((p: any) => <PredictionCard key={p.id} p={p} />) : <div className="card text-slate-400">No combo yet. Check back after picks refresh.</div>}</div></main>;
}
