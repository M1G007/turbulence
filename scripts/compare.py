"""Compare a fresh current.json against the numbers embedded in the live index.html."""
import json, re, sys
live_html, new_json = open(sys.argv[1]).read(), json.load(open(sys.argv[2]))
m = re.search(r"const EMBEDDED_CURRENT = (.*?);\n", live_html, re.S)
live = json.loads(m.group(1))
keys = ["as_of_date", "rolling_value", "ewma_value", "percentile_rank", "p75", "p95",
        "signal_status", "warning_level", "days_elevated", "spx_level", "vix_level",
        "divergence_active", "ai_turbulence"]
rows = ["| Metric | Live site | GitHub run |", "|---|---|---|"]
for k in keys:
    rows.append(f"| {k} | {live.get(k)} | {new_json.get(k)} |")
print("\n".join(rows))
