"use client";

import { useState } from "react";

export function CommunityActions({ seed = 0 }: { seed?: number }) {
  const [likes, setLikes] = useState(seed);
  const [replies, setReplies] = useState<string[]>([]);
  const [text, setText] = useState("");

  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap gap-2 text-xs font-bold">
        <button type="button" onClick={() => setLikes((x) => x + 1)} className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-emerald-200">
          👍 Like {likes}
        </button>
        <button type="button" onClick={() => setText((x) => x || "I agree because ")} className="rounded-full border border-slate-700 bg-slate-950/70 px-3 py-2 text-slate-200">
          💬 Reply
        </button>
        <button type="button" onClick={() => navigator.clipboard?.writeText(window.location.href)} className="rounded-full border border-slate-700 bg-slate-950/70 px-3 py-2 text-slate-200">
          🔗 Copy link
        </button>
      </div>
      {text || replies.length ? (
        <div className="rounded-2xl border border-slate-800 bg-slate-950/70 p-3">
          {replies.map((reply, i) => (
            <p key={`${reply}-${i}`} className="mb-2 rounded-xl bg-slate-900 p-2 text-xs text-slate-300">{reply}</p>
          ))}
          <div className="flex gap-2">
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Write a quick take..." className="min-w-0 flex-1 rounded-xl border border-slate-800 bg-slate-950 px-3 py-2 text-sm" />
            <button type="button" onClick={() => { if (text.trim()) { setReplies((x) => [text.trim(), ...x]); setText(""); } }} className="rounded-xl bg-emerald-400 px-3 py-2 text-xs font-black text-slate-950">
              Send
            </button>
          </div>
          <p className="mt-2 text-[11px] text-slate-500">Quick replies are local for now. Full accounts, saved likes, and follows come with the trust layer.</p>
        </div>
      ) : null}
    </div>
  );
}