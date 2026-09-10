"use client";

import { useRouter } from "next/navigation";

export function BackButton({ fallback = "/" }: { fallback?: string }) {
  const router = useRouter();

  return (
    <button
      type="button"
      onClick={() => {
        if (window.history.length > 1) router.back();
        else router.push(fallback);
      }}
      className="inline-flex items-center gap-2 text-xs font-bold text-slate-500 transition hover:text-white"
    >
      <span aria-hidden="true">←</span>
      Back
    </button>
  );
}
