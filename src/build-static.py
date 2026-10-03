"""
Build static HTML dashboard by embedding all API JSON data directly
into the HTML file. No backend needed at runtime.
"""
import json
import os

DATA_DIR = '/home/user/workspace/turbulence-static/data'
HTML_SRC = '/home/user/workspace/turbulence-app/static/index.html'
HTML_OUT = '/home/user/workspace/turbulence-static/index.html'

# Read all data files
data_files = {
    'current': 'current.json',
    'turbulence': 'turbulence.json',
    'assets': 'assets.json',
    'heatmap': 'heatmap.json',
    'events': 'events.json',
    'spx_overlay': 'spx_overlay.json',
}

data = {}
for key, filename in data_files.items():
    filepath = os.path.join(DATA_DIR, filename)
    try:
        with open(filepath) as f:
            data[key] = json.load(f)
        print(f"  Loaded {filename}: {os.path.getsize(filepath):,} bytes")
    except Exception as e:
        print(f"  WARNING: Could not load {filename}: {e}")
        data[key] = {} if key == 'current' else []

# Read the HTML template
with open(HTML_SRC) as f:
    html = f.read()

# Create the embedded data block
data_block = f"""
// ========== EMBEDDED DATA (pre-computed, no backend needed) ==========
const EMBEDDED_CURRENT = {json.dumps(data['current'])};
const EMBEDDED_TURBULENCE = {json.dumps(data['turbulence'])};
const EMBEDDED_ASSETS = {json.dumps(data['assets'])};
const EMBEDDED_HEATMAP = {json.dumps(data['heatmap'])};
const EMBEDDED_EVENTS = {json.dumps(data['events'])};
const EMBEDDED_SPX_OVERLAY = {json.dumps(data['spx_overlay'])};
"""

# Replace the config section to inject embedded data
old_config = """// ========== CONFIG ==========
const API_BASE = '';"""

new_config = f"""// ========== CONFIG ==========
const API_BASE = '';
{data_block}"""

html = html.replace(old_config, new_config)

# Replace the data loading functions to use embedded data
old_fetch = """async function fetchJSON(endpoint) {
  const resp = await fetch(API_BASE + endpoint);
  return resp.json();
}

async function loadCurrent() {
  const data = await fetchJSON('/api/current');
  if (data.error) return null;
  return data;
}

// ========== INIT ==========
async function init() {
  const overlay = document.getElementById('loading-overlay');

  async function tryLoad() {
    currentData = await loadCurrent();
    if (!currentData) {
      setTimeout(tryLoad, 5000);
      return;
    }

    const [turb, assets, heatmap, events, spxOverlay] = await Promise.all([
      fetchJSON('/api/turbulence'),
      fetchJSON('/api/assets'),
      fetchJSON('/api/heatmap'),
      fetchJSON('/api/events'),
      fetchJSON('/api/spx_overlay')
    ]);

    turbulenceData = turb;
    assetsData = assets;
    heatmapData = heatmap;
    eventsData = events;
    spxOverlayData = Array.isArray(spxOverlay) ? spxOverlay : [];

    renderAll();
    document.getElementById('app').style.visibility = 'visible';
    overlay.classList.add('hidden');
  }

  tryLoad();
}"""

new_fetch = """async function fetchJSON(endpoint) {
  // Use embedded data - no backend needed
  const map = {
    '/api/current': EMBEDDED_CURRENT,
    '/api/turbulence': EMBEDDED_TURBULENCE,
    '/api/assets': EMBEDDED_ASSETS,
    '/api/heatmap': EMBEDDED_HEATMAP,
    '/api/events': EMBEDDED_EVENTS,
    '/api/spx_overlay': EMBEDDED_SPX_OVERLAY
  };
  return map[endpoint] || {};
}

async function loadCurrent() {
  return EMBEDDED_CURRENT;
}

// ========== INIT ==========
async function init() {
  const overlay = document.getElementById('loading-overlay');

  currentData = EMBEDDED_CURRENT;
  turbulenceData = EMBEDDED_TURBULENCE;
  assetsData = EMBEDDED_ASSETS;
  heatmapData = EMBEDDED_HEATMAP;
  eventsData = EMBEDDED_EVENTS;
  spxOverlayData = Array.isArray(EMBEDDED_SPX_OVERLAY) ? EMBEDDED_SPX_OVERLAY : [];

  renderAll();
  document.getElementById('app').style.visibility = 'visible';
  overlay.classList.add('hidden');
}"""

html = html.replace(old_fetch, new_fetch)

# Write the output
os.makedirs(os.path.dirname(HTML_OUT), exist_ok=True)
with open(HTML_OUT, 'w') as f:
    f.write(html)

print(f"\nStatic HTML written: {len(html):,} bytes")
print(f"Output: {HTML_OUT}")
print("Done!")
