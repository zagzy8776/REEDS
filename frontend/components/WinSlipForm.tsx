"use client";

import { useState } from "react";
import { API_URL } from "../lib/api";

export function WinSlipForm({ posts = [] }: { posts?: any[] }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(formData: FormData) {
    setBusy(true);
    setMessage("");
    const payload = Object.fromEntries(formData.entries());
    if (!payload.prediction_id) delete payload.prediction_id;
    try {
      const response = await fetch(`${API_URL}/api/community/win-slips`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error("Could not post the win");
      setMessage("🏆 Win posted. Refresh and it will show up.");
    } catch (error: any) {
      setMessage(error.message || "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form action={submit} className="mt-4 grid gap-3 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-4">
      <input name="username" required maxLength={80} placeholder="Your name" className="rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-sm" />
      <input name="title" required maxLength={160} placeholder="What won? e.g. 3-leg code landed" className="rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-sm" />
      <select name="prediction_id" className="rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-sm">
        <option value="">Link it to a pick (optional)</option>
        {posts.map((p: any) => <option key={p.id} value={p.id}>{p.username} • {p.market}: {p.pick}</option>)}
      </select>
      <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
        <input name="profit_units" type="number" step="0.1" placeholder="Profit units" className="rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-sm" />
        <button disabled={busy} className="rounded-xl bg-emerald-400 px-4 py-2 text-sm font-black text-slate-950 disabled:opacity-60">Post win</button>
      </div>
      <textarea name="proof_text" maxLength={1000} placeholder="Tell us what landed. Add odds, code details, or a quick note." className="min-h-24 rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-sm" />
      {message ? <p className="text-xs text-emerald-100">{message}</p> : null}
    </form>
  );
}