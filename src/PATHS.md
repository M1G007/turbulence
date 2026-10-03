# Absolute paths referenced by the model source

## src/build-static.py

| Constant | Absolute path | Purpose | Repo copy |
|---|---|---|---|
| `DATA_DIR` | `/home/user/workspace/turbulence-static/data` | Reads the 6 exported JSON files: `current.json`, `turbulence.json`, `assets.json`, `heatmap.json`, `events.json`, `spx_overlay.json` | Generated at run time (not committed) |
| `HTML_SRC` | `/home/user/workspace/turbulence-app/static/index.html` | Dashboard HTML template that the JSON is embedded into | `src/turbulence-app/static/index.html` |
| `HTML_OUT` | `/home/user/workspace/turbulence-static/index.html` | Built static dashboard (written) | `index.html` at repo root |

## src/turbulence-app/server.py

No absolute paths. Its only file reference is relative:

- `STATIC_DIR = Path(__file__).parent / "static"` (resolves to `src/turbulence-app/static/`)

The server listens on `http://localhost:8000`. Market data comes from yfinance over the network; nothing is cached to disk.

## Run order

1. `cd /home/user/workspace/turbulence-app && python server.py`
2. Wait for `GET /api/health` to return `status=ok`
3. Export `/api/{current,turbulence,assets,heatmap,events,spx_overlay}` to `DATA_DIR`
4. `python3 /home/user/workspace/build-static.py`
