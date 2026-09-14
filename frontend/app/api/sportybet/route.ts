import { NextRequest, NextResponse } from "next/server";

const SPORTYBET_URL =
  "https://www.sportybet.com/api/ng/factsCenter/wapConfigurableUpcomingEvents";

const ALLOWED_PARAMS = [
  "sportId",
  "marketId",
  "productId",
  "page",
  "pageSize",
  "categoryId",
  "tournamentId",
  "_t",
];

export async function GET(request: NextRequest) {
  const upstreamUrl = new URL(SPORTYBET_URL);

  for (const key of ALLOWED_PARAMS) {
    const value = request.nextUrl.searchParams.get(key);
    if (value !== null && value !== "") {
      upstreamUrl.searchParams.set(key, value);
    }
  }

  try {
    const response = await fetch(upstreamUrl.toString(), {
      method: "GET",
      cache: "no-store",
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        Accept: "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        Origin: "https://www.sportybet.com",
        Referer: "https://www.sportybet.com/ng/lite",
        "x-app-name": "sportybet",
      },
    });

    const body = await response.text();
    const contentType = response.headers.get("content-type") || "application/json";

    return new NextResponse(body, {
      status: response.status,
      headers: {
        "content-type": contentType,
        "cache-control": "no-store, max-age=0",
      },
    });
  } catch (error) {
    console.error("SportyBet relay failed", error);
    return NextResponse.json(
      { ok: false, error: "sportybet_upstream_unavailable" },
      { status: 502, headers: { "cache-control": "no-store" } },
    );
  }
}
