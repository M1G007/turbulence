"""Open a GitHub issue with the day's turbulence reading.

GitHub emails the repo owner about new issues opened by the Actions bot,
so this doubles as an email alert. ALERT_MODE=elevated only alerts when
the signal is not CALM; ALERT_MODE=daily alerts every run.
"""
import json, os, subprocess, sys

c = json.load(open(sys.argv[1]))
mode = os.environ.get("ALERT_MODE", "elevated")
status = c.get("signal_status", "?")
level = c.get("warning_level", status)
if mode != "daily" and status == "CALM":
    print(f"{c.get('as_of_date')}: {status}, no alert sent")
    sys.exit(0)

def f(v, nd=2):
    return f"{v:,.{nd}f}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)

body = f"""@M1G007

**As of {c.get('as_of_date')}**

| Metric | Value |
|---|---|
| Signal | **{status}** ({level}) |
| Rolling 252d | {f(c.get('rolling_value'))} (delta {f(c.get('rolling_delta'))}) |
| EWMA 60d | {f(c.get('ewma_value'))} (delta {f(c.get('ewma_delta'))}) |
| Percentile | {f(c.get('percentile_rank'))} |
| p75 / p95 | {f(c.get('p75'))} / {f(c.get('p95'))} |
| Days elevated | {c.get('days_elevated')} |
| SPX / 50-day | {f(c.get('spx_level'))} / {f(c.get('spx_50ma'))} (above: {c.get('spx_above_50ma')}) |
| VIX | {f(c.get('vix_level'))} |
| Divergence | {c.get('divergence_active')} |
| AI turbulence | {f(c.get('ai_turbulence'))} (ratio {f(c.get('ai_ratio'))}) |

Dashboard: https://m1g007.github.io/turbulence/
"""
title = f"Turbulence Update: {level} ({c.get('as_of_date')})"
subprocess.run(["gh", "label", "create", "turbulence-alert", "--color", "d29922", "--force"], check=False)
subprocess.run(["gh", "issue", "create", "--title", title, "--body", body, "--label", "turbulence-alert"], check=True)
print("Alert issue opened:", title)
