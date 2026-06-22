import json, sys, urllib.request

base = "https://reeds-phj1.onrender.com"

def fetch(path):
    try:
        r = urllib.request.urlopen(f"{base}{path}", timeout=10)
        return json.loads(r.read())
    except Exception as e:
        print(f"  Error: {e}")
        return None

print("=== Fixtures Status ===")
status = fetch("/api/fixtures/status")
if status:
    print(f"  Total: {status.get('total')}")
    print(f"  Upcoming: {status.get('upcoming')}")
    print(f"  Today: {status.get('today')}")
    print(f"  Feed health: {status.get('feed_health')}")

print("\n=== Today Predictions ===")
today = fetch("/api/predictions/today")
print(f"  Count: {len(today)}")
if today:
    for p in today[:3]:
        print(f"  {p.get('home_team')} vs {p.get('away_team')} - {p.get('market')} - {p.get('pick')} ({p.get('confidence')}%)")

print("\n=== History (7 days) ===")
hist = fetch("/api/predictions/history?days=7&limit=5")
print(f"  Count: {len(hist)}")
if hist:
    for p in hist:
        print(f"  {p.get('home_team')} vs {p.get('away_team')} - {p.get('match_date')} - {p.get('result')}")

print("\n=== Stats ===")
stats = fetch("/api/stats/backtest")
if stats:
    r = stats.get('results', {})
    print(f"  Settled picks: {r.get('settled_picks')}")
    print(f"  Hit rate: {r.get('hit_rate')}%")
    print(f"  Wins: {r.get('wins')} / Losses: {r.get('losses')}")