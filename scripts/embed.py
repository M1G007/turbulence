"""Fallback builder: swap fresh JSON into the EMBEDDED_* constants of index.html.

Used only if src/build-static.py is missing or fails. The live page stores
each dataset as a one-line `const EMBEDDED_<NAME> = <json>;` statement.
"""
import json, re, sys
from pathlib import Path

html_path, data_dir = Path(sys.argv[1]), Path(sys.argv[2])
html = html_path.read_text()
names = {"CURRENT": "current", "TURBULENCE": "turbulence", "ASSETS": "assets",
         "HEATMAP": "heatmap", "EVENTS": "events", "SPX_OVERLAY": "spx_overlay"}
for const, fname in names.items():
    f = data_dir / f"{fname}.json"
    if not f.exists():
        continue
    payload = json.dumps(json.loads(f.read_text()))
    pat = re.compile(r"(const EMBEDDED_%s = ).*?;\n" % const, re.S)
    html, n = pat.subn(lambda m: m.group(1) + payload + ";\n", html, count=1)
    print(f"{const}: {'replaced' if n else 'NOT FOUND'}")
html_path.write_text(html)
