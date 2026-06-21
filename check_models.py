import json, urllib.request
url = "https://reeds-phj1.onrender.com/api/stats/backtest"
d = json.loads(urllib.request.urlopen(url).read())
models = d.get("models", [])
print(f"\n{'SPORT':<22} {'TYPE':<35} {'ROWS':>8} {'ACC':>8}  STATUS")
print("-"*82)
for m in models:
    status = "ACTIVE" if m["active"] else "old"
    print(f"{m['sport']:<22} {m['type']:<35} {m['sample_size']:>8} {m['accuracy']*100:>7.1f}%  {status}")
print(f"\nSports with models: {', '.join(sorted(set(m['sport'] for m in models)))}")
print(f"Active models: {sum(1 for m in models if m['active'])}")
