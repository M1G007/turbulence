"""Compare this run's output with the numbers embedded in the live index.html.

Usage: compare.py live_index.html data_dir
"""
import json, re, sys
from pathlib import Path

live_html = open(sys.argv[1]).read()
data = Path(sys.argv[2])
def emb(name):
    m = re.search(r"const EMBEDDED_%s = (.*?);\n" % name, live_html, re.S)
    return json.loads(m.group(1))

live, new = emb("CURRENT"), json.loads((data / "current.json").read_text())
live_t = {r["date"]: r for r in emb("TURBULENCE")}
new_t = {r["date"]: r for r in json.loads((data / "turbulence.json").read_text())}

common = [d for d in sorted(live_t) if d in new_t
          and live_t[d]["rolling_turbulence"] is not None and new_t[d]["rolling_turbulence"] is not None]
diffs = [abs(new_t[d]["rolling_turbulence"] - live_t[d]["rolling_turbulence"]) for d in common]
close = sum(1 for d, x in zip(common, diffs) if x <= max(0.01, 0.001 * abs(live_t[d]["rolling_turbulence"])))
print(f"**History check:** {close} of {len(common)} shared days match the live site within 0.1%.\n")
print("**Last 5 shared days (rolling / EWMA):**\n")
print("| Date | Live site | GitHub run |\n|---|---|---|")
for d in common[-5:]:
    L, N = live_t[d], new_t[d]
    print(f"| {d} | {L['rolling_turbulence']:.2f} / {L['ewma_turbulence']:.2f} | {N['rolling_turbulence']:.2f} / {N['ewma_turbulence']:.2f} |")
print("\n**Latest reading:**\n")
print("| Metric | Live site | GitHub run |\n|---|---|---|")
for k in ["as_of_date", "rolling_value", "ewma_value", "percentile_rank", "p75", "p95", "signal_status",
          "warning_level", "days_elevated", "spx_level", "vix_level", "divergence_active", "ai_turbulence"]:
    print(f"| {k} | {live.get(k)} | {new.get(k)} |")
