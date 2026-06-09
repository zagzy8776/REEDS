export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function safeFetchJson(url: string, fallback: any) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000); // 12s instead of 5s
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

export async function getTodayPredictions(params: Record<string, string> = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString();
  return safeFetchJson(`${API_URL}/api/predictions/today${qs ? `?${qs}` : ""}`, []);
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
  return safeFetchJson(`${API_URL}/api/fixtures/upcoming?limit=100`, []);
}

export async function getFixtures(params: Record<string, string> = {}) {
  const withDefaults = { scope: "upcoming", limit: "300", ...params };
  const qs = new URLSearchParams(Object.entries(withDefaults).filter(([, v]) => v)).toString();
  return safeFetchJson(`${API_URL}/api/fixtures/upcoming${qs ? `?${qs}` : "?limit=300"}`, []);
}

export async function getFixtureStatus() {
  return safeFetchJson(`${API_URL}/api/fixtures/status`, null);
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
