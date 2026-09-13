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

export async function getPredictionHistoryPaginated(params: Record<string, string> = {}) {
  const withDefaults = { paginated: "true", days: "45", limit: "20", page: "1", ...params };
  const qs = new URLSearchParams(Object.entries(withDefaults).filter(([, v]) => v)).toString();
  return safeFetchJson(`${API_URL}/api/predictions/history?${qs}`, { items: [], total: 0, page: 1, page_size: 20, has_more: false });
}

export async function getPerformance() {
  return safeFetchJson(`${API_URL}/api/stats/performance`, { label: "LIVE RECORD", segments: {}, by_sport: [], by_market: [], calibration: [], note: "" }, 60000);
}

export async function getDataStatus() {
  return safeFetchJson(`${API_URL}/api/stats/data-status`, { as_of: "", fixtures: {}, odds: {}, predictions: {} }, 45000);
}

export async function getLatestAlerts() {
  return safeFetchJson(`${API_URL}/api/stats/alerts/latest`, [], 45000);
}

export async function getModelFeedbackStats() {
  return safeFetchJson(`${API_URL}/api/stats/model-feedback`, { segments: [], flagged_patterns: [], total_feedback_records: 0, note: "" }, 45000);
}

export async function getFixtureIntelligence(fixtureId: string | number) {
  return safeFetchJson(`${API_URL}/api/fixtures/${encodeURIComponent(String(fixtureId))}/intelligence`, null, 45000);
}

export async function getLiveMatches() {
  return safeFetchJson(`${API_URL}/api/live/matches`, [], 30000);
}

export async function getLiveEvents(fixtureId: string | number) {
  return safeFetchJson(`${API_URL}/api/live/events/${encodeURIComponent(String(fixtureId))}`, { events: [] }, 30000);
}

export async function getPrediction(id: string) {
  return safeFetchJson(`${API_URL}/api/predictions/${id}`, null);
}

export async function getCombo() {
  return safeFetchJson(`${API_URL}/api/predictions/combo?legs=3&min_confidence=60`, { legs: [] });
}

export async function getStats() {
  return safeFetchJson(`${API_URL}/api/stats/summary`, { results: { settled_picks: 0, wins: 0, losses: 0, hit_rate: 0, by_sport: [], by_market: [], confidence_buckets: [] }, market_proof: { tracked_bets: 0, profit_units: 0, roi_percent: 0, clv_tracked: 0, positive_clv_rate: 0, by_market: [], note: "ROI/CLV tracking is warming up." }, backtests: [], models: [], data_quality: {} });
}

export async function getUpcomingFixtures() {
  return getFixtures({ scope: "all", limit: "500" });
}

export async function getFixtures(params: Record<string, string> = {}) {
  const withDefaults = { scope: "all", limit: "500", ...params };
  const qs = new URLSearchParams(Object.entries(withDefaults).filter(([, v]) => v)).toString();
  return getFixturesWithRetry(`${API_URL}/api/fixtures/upcoming?${qs}`);
}

export async function getFixtureStatus() {
  return safeFetchJson(`${API_URL}/api/fixtures/status`, null, 30000);
}

export async function getFixture(id: string) {
  return safeFetchJson(`${API_URL}/api/fixtures/${id}`, null);
}

export async function getAIReads(fixtureId: string | number) {
  return safeFetchJson(`${API_URL}/api/ai-reads/${encodeURIComponent(String(fixtureId))}`, null, 45000);
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
