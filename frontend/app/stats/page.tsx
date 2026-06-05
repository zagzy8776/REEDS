import { getStats } from "../../lib/api";

export default async function Stats() {
  const stats = await getStats();
  return <main className="mx-auto max-w-5xl px-6 py-10"><h1 className="text-4xl font-black">Performance Stats</h1><p className="mt-2 text-slate-400">{stats.note}</p><div className="mt-8 card"><table className="w-full text-left text-sm"><thead><tr className="text-slate-400"><th>Sport</th><th>Type</th><th>Accuracy</th><th>Rows</th><th>Active</th></tr></thead><tbody>{(stats.models || []).map((m: any, i: number) => <tr key={i} className="border-t border-slate-800"><td className="py-3">{m.sport}</td><td>{m.type}</td><td>{Math.round((m.accuracy || 0) * 100)}%</td><td>{m.sample_size}</td><td>{m.active ? "Yes" : "No"}</td></tr>)}</tbody></table></div></main>;
}
