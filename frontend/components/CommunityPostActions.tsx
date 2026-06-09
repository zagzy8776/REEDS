"use client";

import { useState } from "react";
import { API_URL } from "../lib/api";

type Props = {
  post: any;
};

async function postJson(path: string, payload: Record<string, unknown>) {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Community action failed");
  }
  return response.json();
}

export function CommunityPostActions({ post }: Props) {
  const [username, setUsername] = useState("");
  const [comment, setComment] = useState("");
  const [rating, setRating] = useState("5");
  const [stake, setStake] = useState("1");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [counts, setCounts] = useState({
    comments: post.social?.comments || 0,
    plays: post.social?.plays || 0,
    ratings: post.social?.rating_count || 0,
    averageRating: post.social?.average_rating || 0,
    wins: post.social?.win_slips || 0,
  });

  async function run(action: string, task: () => Promise<void>) {
    setMessage("");
    if (!username.trim()) {
      setMessage("Add your name first.");
      return;
    }
    setBusy(action);
    try {
      await task();
      setMessage("Done. You are in.");
    } catch (error: any) {
      setMessage(error.message || "Something went wrong.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-4 rounded-2xl border border-slate-800 bg-slate-950/70 p-3">
      <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
        <input value={username} onChange={(e) => setUsername(e.target.value)} maxLength={80} placeholder="Your username" className="rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-sm" />
        <input value={stake} onChange={(e) => setStake(e.target.value)} type="number" min="0.1" step="0.1" className="rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-sm sm:w-24" title="Stake units" />
        <button type="button" disabled={busy === "tail"} onClick={() => run("tail", async () => {
          await postJson(`/api/community/predictions/${post.id}/plays`, { username, stake_units: Number(stake) || 1 });
          setCounts((x) => ({ ...x, plays: x.plays + 1 }));
        })} className="rounded-xl bg-emerald-400 px-3 py-2 text-xs font-black text-slate-950 disabled:opacity-60">🎯 Tail it</button>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto_auto]">
        <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} placeholder="Drop a comment..." className="rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-sm" />
        <select value={rating} onChange={(e) => setRating(e.target.value)} className="rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-sm">
          {[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{"⭐".repeat(n)}</option>)}
        </select>
        <button type="button" disabled={busy === "comment"} onClick={() => run("comment", async () => {
          if (comment.trim()) {
            await postJson(`/api/community/predictions/${post.id}/comments`, { username, comment_text: comment });
            setComment("");
            setCounts((x) => ({ ...x, comments: x.comments + 1 }));
          }
          await postJson(`/api/community/predictions/${post.id}/reactions`, { username, reaction: "rated", rating: Number(rating) });
          setCounts((x) => ({ ...x, ratings: x.ratings + 1, averageRating: x.averageRating || Number(rating) }));
        })} className="rounded-xl border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-xs font-black text-emerald-200 disabled:opacity-60">💬 Send</button>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-slate-400">
        <span>🎯 {counts.plays} played</span>
        <span>💬 {counts.comments} comments</span>
        <span>⭐ {counts.averageRating || 0} ({counts.ratings})</span>
        <span>🏆 {counts.wins} wins</span>
      </div>
      {message ? <p className="mt-2 text-xs text-emerald-200">{message}</p> : null}
    </div>
  );
}