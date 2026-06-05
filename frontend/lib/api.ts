export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function getTodayPredictions() {
  const r = await fetch(`${API_URL}/api/predictions/today`, { next: { revalidate: 60 } });
  return r.ok ? r.json() : [];
}

export async function getCombo() {
  const r = await fetch(`${API_URL}/api/predictions/combo?legs=3&min_confidence=55`, { next: { revalidate: 60 } });
  return r.ok ? r.json() : { legs: [] };
}

export async function getStats() {
  const r = await fetch(`${API_URL}/api/stats/backtest`, { next: { revalidate: 60 } });
  return r.ok ? r.json() : { models: [] };
}
