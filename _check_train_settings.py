import requests
BASE = "http://127.0.0.1:5000" # Use default backend port
r = requests.get(f"{BASE}/api/train_settings", timeout=60)
d = r.json()
pg = d.get("per_game", {})
print(f"HTTP {r.status_code}")
for g in ["take5", "pick3", "powerball", "megamillions", "pick10", "cash4life", "quickdraw", "nylotto"]:
    p = pg.get(g, {})
    td = p.get("training_data", {})
    print(
        f"{g:14} model={str(p.get('has_saved_model')):5} "
        f"acc={p.get('highest_accuracy')} "
        f"data={td.get('status')} new={td.get('new_draws', 0)}"
    )