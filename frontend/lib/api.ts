export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function safeFetchJson(url: string, fallback: any) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const r = await fetch(url, {
      cache: "no-store",
      signal: controller.signal,
    });
    return r.ok ? r.json() : fallback;
  } catch {
    return fallback;
  } finally {
    clearTimeout(timeout);
  }
}

export async function getTodayPredictions() {
  return safeFetchJson(`${API_URL}/api/predictions/today`, []);
}

export async function getCombo() {
  return safeFetchJson(`${API_URL}/api/predictions/combo?legs=3&min_confidence=55`, { legs: [] });
}

export async function getStats() {
  return safeFetchJson(`${API_URL}/api/stats/backtest`, { models: [], note: "Backend stats are not available yet." });
}
