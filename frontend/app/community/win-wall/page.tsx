import { getWinWall } from "../../../lib/api";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function WinWall() {
  const wins = await getWinWall();

  return (
    <main className="mx-auto max-w-4xl px-4 py-6 sm:px-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="badge inline-block">💰 Win Wall</p>
          <h1 className="mt-4 text-3xl font-black sm:text-4xl">Winning Slips</h1>
          <p className="mt-2 text-slate-400">The best picks from the community. Proof that people are winning.</p>
        </div>
        <Link href="/community-leaderboard" className="text-sm text-slate-400 hover:text-white">← Leaderboard</Link>
      </div>

      {wins.length === 0 ? (
        <div className="card mt-8 text-center">
          <div className="text-5xl">🏆</div>
          <h2 className="mt-4 text-xl font-black">No winning slips yet</h2>
          <p className="mt-2 text-slate-400">Post a pick, win, then share your slip here.</p>
          <Link href="/predictions/submit" className="mt-4 inline-flex rounded-xl bg-emerald-400 px-5 py-2 text-sm font-bold text-slate-950">+ Post a pick</Link>
        </div>
      ) : (
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {wins.map((w: any) => (
            <div key={w.id} className="card group relative overflow-hidden transition-all hover:border-emerald-400/30">
              <div className="absolute -right-8 -top-8 h-24 w-24 rounded-full bg-emerald-400/10 blur-xl transition-all group-hover:bg-emerald-400/20" />
              <div className="flex items-center justify-between">
                <Link href={`/community/profile/${w.username}`} className="font-bold text-white hover:text-emerald-300">{w.username}</Link>
                <span className="rounded-full bg-emerald-400/10 px-3 py-1 text-xs font-bold text-emerald-300">+{w.profit_units} units</span>
              </div>
              <h3 className="mt-3 text-lg font-black">{w.title}</h3>
              {w.proof_text && <p className="mt-2 text-sm text-slate-400">{w.proof_text}</p>}
              <p className="mt-3 text-xs text-slate-500">{w.created_at ? new Date(w.created_at).toLocaleDateString() : ""}</p>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}