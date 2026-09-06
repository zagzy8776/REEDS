export const API_URL = process.env.NEXT_PUBLIC_API_URL || "https://reeds-phj1.onrender.com";

const DEFAULT_API_TIMEOUT_MS = 70000;

async function safeFetchJson(url: string, fallback: any, timeoutMs = DEFAULT_API_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(url, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!r.ok) {
      console.error(`[REEDS API] ${r.status} ${r.statusText}: ${url}`);
      return fallback;
    }
    return await r.json();
  } catch (error) {
    console.error(`[REEDS API] request failed: ${url}`, error);
    return fallback;
  } finally {
    clearTimeout(timeout);
  }
}

async function getFixturesWithRetry(url: string, timeoutMs = DEFAULT_API_TIMEOUT_MS) {
  const first = await safeFetchJson(url, null, timeoutMs);
  if (Array.isArray(first) && first.length > 0) return first;

  // Render can be waking from sleep or briefly recycling while the status
  // endpoint is already available. Retry once before the page falls back to
  // prediction-backed rows.
  await new Promise((resolve) => setTimeout(resolve, 800));
  const second = await safeFetchJson(url, null, timeoutMs);
  return Array.isArray(second) ? second : [];
}

export async function getTodayPredictions(params: Record<string, string> = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString();
  return safeFetchJson(`${API_URL}/api/predictions/today${qs ? `?${qs}` : ""}`, []);
}

export async function getPredictionHistory(params: Record<string, string> = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString();
  return safeFetchJson(`${API_URL}/api/predictions/history${qs ? `?${qs}` : "?days=7&limit=50"}`, []);
}

export async function getPrediction(id: string) {
  return safeFetchJson(`${API_URL}/api/predictions/${id}`, null);
}

export async function getCombo() {
  return safeFetchJson(`${API_URL}/api/predictions/combo?legs=3&min_confidence=55`, { legs: [] });
}

export async function getStats() {
  return safeFetchJson(`${API_URL}/api/stats/backtest`, { models: [], note: "Backend stats are not available yet." });
}

export async function getUpcomingFixtures() {
  return getFixtures({ scope: "all", limit: "300" });
}

export async function getFixtures(params: Record<string, string> = {}) {
  const withDefaults = { scope: "all", limit: "300", ...params };
  const qs = new URLSearchParams(Object.entries(withDefaults).filter(([, v]) => v)).toString();
  return getFixturesWithRetry(`${API_URL}/api/fixtures/upcoming?${qs}`);
}

export async function getFixtureStatus() {
  return safeFetchJson(`${API_URL}/api/fixtures/status`, null, 30000);
}

export async function getFixture(id: string) {
  return safeFetchJson(`${API_URL}/api/fixtures/${id}`, null);
}

export async function getCommunityLeaderboard() {
  return safeFetchJson(`${API_URL}/api/community/leaderboard?limit=50`, []);
}

export async function getCommunityOverview() {
  return safeFetchJson(`${API_URL}/api/community/overview`, { total_posts: 0, pending: 0, settled: 0, recent_posts: [], top_markets: [] });
}

export async function getCommunityExperts() {
  return safeFetchJson(`${API_URL}/api/community/experts?limit=50`, []);
}

export async function getWinWall() {
  return safeFetchJson(`${API_URL}/api/community/win-wall?limit=30`, []);
}

export async function getDailyChallenge() {
  return safeFetchJson(`${API_URL}/api/community/daily-challenge`, { active: false });
}

export async function getUserProfile(username: string) {
  return safeFetchJson(`${API_URL}/api/community/profile/${encodeURIComponent(username)}`, null);
}

export async function followUser(follower: string, following: string) {
  try {
    const r = await fetch(`${API_URL}/api/community/follow`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ follower, following }),
    });
    return r.ok ? r.json() : null;
  } catch {
    return null;
  }
}
