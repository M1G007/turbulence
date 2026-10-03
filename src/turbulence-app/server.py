"""
Market Turbulence Model Backend
Mahalanobis-distance based turbulence index (Visser-style)
"""

import warnings
warnings.filterwarnings("ignore")

import sys
import math
import traceback
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd
from scipy.linalg import inv, LinAlgError

import yfinance as yf

from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# ---------------------------------------------------------------------------
# Asset Universe
# ---------------------------------------------------------------------------

ASSETS: Dict[str, List[str]] = {
    "US Equity": [
        "SPY", "QQQ", "IWM", "DIA", "MDY",
        "XLB", "XLC", "XLE", "XLF", "XLI",
        "XLK", "XLP", "XLRE", "XLU", "XLV",
        "XLY", "XBI", "ARKK", "SMH", "SOXX",
        "IGV", "KRE", "XHB", "IYT", "XRT",
    ],
    "International Equity": [
        "EFA", "EEM", "FXI", "EWJ", "EWZ",
        "EWG", "EWU", "INDA", "EWT", "EWY",
        "KWEB", "VGK", "MCHI", "VWO", "IEMG",
    ],
    "Fixed Income": [
        "TLT", "IEF", "SHY", "AGG", "BND",
        "LQD", "HYG", "JNK", "TIP", "MUB",
        "EMB", "BWX", "GOVT", "VCSH", "VCLT",
    ],
    "Commodities": [
        "GLD", "SLV", "USO", "UNG", "DBA",
        "DBB", "COPX", "WEAT", "CORN", "PPLT",
        "PALL", "DBC",
    ],
    "Crypto": [
        "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD",
        "ADA-USD", "AVAX-USD", "LINK-USD", "DOT-USD",
    ],
    "Currencies & Vol": [
        "UUP", "FXE", "FXY", "FXB", "FXA",
        "FXC", "CYB", "VIXY", "SVXY", "GDX",
    ],
    "Macro": [
        "^VIX", "^TNX", "^TYX", "^FVX", "^IRX",
        "DX-Y.NYB", "CL=F", "GC=F", "SI=F", "HG=F",
        "NG=F", "ZB=F", "ZN=F",
    ],
}

# Flat list of all tickers
ALL_TICKERS: List[str] = [t for tickers in ASSETS.values() for t in tickers]

# Reverse map: ticker → category
TICKER_CATEGORY: Dict[str, str] = {
    t: cat for cat, tickers in ASSETS.items() for t in tickers
}

# ---------------------------------------------------------------------------
# Stress Events
# ---------------------------------------------------------------------------

STRESS_EVENTS = [
    {"date": "2020-03-23", "label": "COVID Crash Low"},
    {"date": "2022-01-24", "label": "Ukraine Invasion Selloff"},
    {"date": "2022-06-16", "label": "2022 Bear Market Low"},
    {"date": "2023-03-13", "label": "SVB Bank Crisis"},
    {"date": "2023-10-27", "label": "Oct 2023 Correction Low"},
    {"date": "2024-08-05", "label": "Yen Carry Unwind"},
    {"date": "2025-04-08", "label": "Liberation Day Tariff Crash"},
]

# ---------------------------------------------------------------------------
# Global cache
# ---------------------------------------------------------------------------

_cache: Dict[str, Any] = {
    "turbulence_series": None,   # DataFrame: date, rolling_turbulence, ewma_turbulence
    "current": None,             # dict of KPI values
    "assets": None,              # list of per-asset dicts
    "heatmap": None,             # list of category groups
    "last_updated": None,
    "prices": None,              # raw price DataFrame
    "returns": None,             # daily returns DataFrame
    "ready": False,
}

# ---------------------------------------------------------------------------
# Data Fetch
# ---------------------------------------------------------------------------

BATCH_SIZE = 20
LOOKBACK_YEARS = 5
ROLLING_WINDOW = 252
EWMA_HALFLIFE = 60
REGULARIZATION = 1e-6


def fetch_prices() -> pd.DataFrame:
    """Download 5 years of adjusted close prices in batches of 20."""
    end = datetime.today()
    start = end - timedelta(days=LOOKBACK_YEARS * 365 + 60)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    all_frames: List[pd.DataFrame] = []
    batches = [ALL_TICKERS[i:i + BATCH_SIZE] for i in range(0, len(ALL_TICKERS), BATCH_SIZE)]

    print(f"[Fetch] Downloading {len(ALL_TICKERS)} tickers in {len(batches)} batches...", flush=True)

    for idx, batch in enumerate(batches):
        print(f"  Batch {idx + 1}/{len(batches)}: {batch}", flush=True)
        try:
            raw = yf.download(
                batch,
                start=start_str,
                end=end_str,
                auto_adjust=True,
                progress=False,
                threads=True,
                timeout=60,
            )
            if raw.empty:
                print(f"  [WARN] Batch {idx + 1} returned empty data", flush=True)
                continue

            # Extract Close prices
            if isinstance(raw.columns, pd.MultiIndex):
                if "Close" in raw.columns.get_level_values(0):
                    closes = raw["Close"]
                else:
                    # fallback to first level
                    closes = raw.xs(raw.columns.get_level_values(0)[0], axis=1, level=0)
            else:
                # Single ticker returned flat
                closes = raw[["Close"]] if "Close" in raw.columns else raw
                if len(batch) == 1:
                    closes.columns = batch

            all_frames.append(closes)

        except Exception as exc:
            print(f"  [ERROR] Batch {idx + 1} failed: {exc}", flush=True)
            continue

    if not all_frames:
        raise RuntimeError("No data fetched — check network / yfinance installation.")

    prices = pd.concat(all_frames, axis=1)
    # Deduplicate columns (can happen if a ticker appears twice)
    prices = prices.loc[:, ~prices.columns.duplicated()]
    prices.index = pd.to_datetime(prices.index)
    prices.sort_index(inplace=True)

    # Keep only tickers that have at least 30% of trading days
    min_obs = int(len(prices) * 0.30)
    prices = prices.dropna(axis=1, thresh=min_obs)

    print(f"[Fetch] Retained {prices.shape[1]} tickers with sufficient history.", flush=True)
    return prices


# ---------------------------------------------------------------------------
# Covariance helpers
# ---------------------------------------------------------------------------

def regularized_inv(cov: np.ndarray) -> Optional[np.ndarray]:
    """Add ridge regularization and invert. Returns None if singular."""
    n = cov.shape[0]
    reg = cov + REGULARIZATION * np.eye(n)
    try:
        return inv(reg)
    except (LinAlgError, ValueError):
        try:
            return np.linalg.pinv(reg)
        except Exception:
            return None


def mahalanobis_distance(r: np.ndarray, mu: np.ndarray, cov_inv: np.ndarray) -> float:
    """Compute raw Mahalanobis distance: (r-mu)' * Cov^-1 * (r-mu)
    NOT normalized by 1/n — matches Visser's scale where values range 0-600+"""
    diff = r - mu
    result = float(diff @ cov_inv @ diff)
    return max(result, 0.0)


# ---------------------------------------------------------------------------
# Rolling Turbulence
# ---------------------------------------------------------------------------

def compute_rolling_turbulence(returns: pd.DataFrame, window: int = ROLLING_WINDOW) -> pd.Series:
    """Compute rolling-window Mahalanobis turbulence index."""
    dates = returns.index
    turbulence = pd.Series(index=dates, dtype=float)

    print(f"[Compute] Rolling turbulence (window={window})...", flush=True)

    for i in range(window, len(dates)):
        window_data = returns.iloc[i - window: i]
        today = returns.iloc[i]

        # Use only assets with no NaN in the window
        valid_cols = window_data.columns[window_data.notna().all()]
        if len(valid_cols) < 2:
            continue

        w = window_data[valid_cols].values
        r_t = today[valid_cols].values

        # Skip if today has NaN in valid columns
        if np.any(np.isnan(r_t)):
            valid_cols = valid_cols[~np.isnan(r_t)]
            if len(valid_cols) < 2:
                continue
            w = window_data[valid_cols].values
            r_t = today[valid_cols].values

        mu = w.mean(axis=0)
        cov = np.cov(w.T)

        if cov.ndim == 0:
            continue

        cov_inv = regularized_inv(cov)
        if cov_inv is None:
            continue

        turbulence.iloc[i] = mahalanobis_distance(r_t, mu, cov_inv)

    print(f"[Compute] Rolling done. Valid points: {turbulence.notna().sum()}", flush=True)
    return turbulence


# ---------------------------------------------------------------------------
# EWMA Turbulence
# ---------------------------------------------------------------------------

def compute_ewma_turbulence(returns: pd.DataFrame, halflife: int = EWMA_HALFLIFE) -> pd.Series:
    """
    Compute EWMA Mahalanobis turbulence index.
    Uses pandas ewm for mean and covariance with given halflife.
    """
    dates = returns.index
    turbulence = pd.Series(index=dates, dtype=float)

    # Pre-compute EWMA mean for each column
    ewm_mean = returns.ewm(halflife=halflife, min_periods=halflife // 2).mean()

    print(f"[Compute] EWMA turbulence (halflife={halflife})...", flush=True)

    # Decay factor λ = 1 - exp(-ln2/halflife)
    decay = 1.0 - math.exp(-math.log(2) / halflife)

    # We'll compute EWMA cov incrementally
    # Use pandas ewm cov for simplicity (pairwise)
    # This can be slow for large matrices; we batch compute

    # For efficiency, compute EWMA cov using the analytical formula:
    # Σ_t = (1-λ) * Σ_{t-1} + λ * (r_t - μ_{t-1})(r_t - μ_{t-1})'
    # We warm up for the first halflife*4 periods

    warmup = halflife * 4
    n_assets = returns.shape[1]
    cov_matrix = np.eye(n_assets) * 0.0001  # initial covariance

    prev_mean = returns.iloc[:warmup].mean().values

    for i in range(warmup, len(dates)):
        r_t = returns.iloc[i].values
        mu_t = ewm_mean.iloc[i].values

        # Find valid assets (non-NaN today and in mean estimate)
        valid_mask = ~(np.isnan(r_t) | np.isnan(mu_t))
        valid_idx = np.where(valid_mask)[0]

        if len(valid_idx) < 2:
            continue

        r_v = r_t[valid_idx]
        mu_v = mu_t[valid_idx]

        # Extract sub-covariance and update
        cov_v = cov_matrix[np.ix_(valid_idx, valid_idx)]

        diff = r_v - mu_v
        outer = np.outer(diff, diff)
        cov_v_new = (1 - decay) * cov_v + decay * outer

        # Write back
        cov_matrix[np.ix_(valid_idx, valid_idx)] = cov_v_new

        cov_inv = regularized_inv(cov_v_new)
        if cov_inv is None:
            continue

        turbulence.iloc[i] = mahalanobis_distance(r_v, mu_v, cov_inv)

    print(f"[Compute] EWMA done. Valid points: {turbulence.notna().sum()}", flush=True)
    return turbulence


# ---------------------------------------------------------------------------
# Per-asset stats
# ---------------------------------------------------------------------------

def compute_asset_stats(
    prices: pd.DataFrame,
    returns: pd.DataFrame,
    rolling_turbulence: pd.Series,
) -> List[Dict[str, Any]]:
    """Compute per-asset statistics for the latest trading day."""
    latest_date = returns.index[-1]

    # Rolling z-score window
    z_window = min(ROLLING_WINDOW, len(returns) - 1)
    recent_returns = returns.iloc[-z_window:]

    # Today's returns
    today_ret = returns.loc[latest_date] if latest_date in returns.index else returns.iloc[-1]
    # 5-day return
    five_day_prices = prices.iloc[-6:]
    five_day_ret = (five_day_prices.iloc[-1] / five_day_prices.iloc[0] - 1) if len(five_day_prices) >= 2 else today_ret * 0

    results = []
    for ticker in returns.columns:
        if ticker not in TICKER_CATEGORY:
            continue
        try:
            tr = float(today_ret.get(ticker, float("nan")))
            fdr = float(five_day_ret.get(ticker, float("nan"))) if hasattr(five_day_ret, "get") else float("nan")

            # Z-score
            col_returns = recent_returns[ticker].dropna()
            if len(col_returns) > 5:
                mean_r = col_returns.mean()
                std_r = col_returns.std()
                z = float((tr - mean_r) / std_r) if std_r > 1e-10 and not math.isnan(tr) else 0.0
            else:
                z = 0.0

            # Turbulence contribution: approximate as z^2 / (2 * n_assets)
            n_assets = len(returns.columns)
            contrib = float(z ** 2) / max(n_assets, 1)

            results.append({
                "ticker": ticker,
                "category": TICKER_CATEGORY[ticker],
                "today_return": round(tr * 100, 4) if not math.isnan(tr) else None,
                "five_day_return": round(fdr * 100, 4) if not math.isnan(fdr) else None,
                "z_score": round(z, 4),
                "turbulence_contribution": round(contrib, 6),
            })
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------

def compute_heatmap(asset_stats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group asset stats by category for heatmap endpoint."""
    from collections import defaultdict
    grouped: Dict[str, List] = defaultdict(list)
    for a in asset_stats:
        grouped[a["category"]].append({
            "ticker": a["ticker"],
            "return": a["today_return"],
            "z_score": a["z_score"],
        })
    return [{"category": cat, "assets": assets} for cat, assets in grouped.items()]


# ---------------------------------------------------------------------------
# Signal Status
# ---------------------------------------------------------------------------

def compute_signal(value: float, series_1yr: pd.Series) -> Dict[str, Any]:
    """Return signal status and percentile rank for a given turbulence value."""
    clean = series_1yr.dropna()
    if len(clean) == 0:
        return {"signal_status": "UNKNOWN", "percentile_rank": 50.0, "p75": 0.0, "p95": 0.0}

    p75 = float(np.percentile(clean, 75))
    p95 = float(np.percentile(clean, 95))
    pct_rank = float((clean <= value).mean() * 100)

    if value < p75:
        status = "CALM"
    elif value < p95:
        status = "ELEVATED"
    else:
        status = "CRITICAL"

    return {
        "signal_status": status,
        "percentile_rank": round(pct_rank, 2),
        "p75": round(p75, 6),
        "p95": round(p95, 6),
    }


# ---------------------------------------------------------------------------
# Main Computation Pipeline
# ---------------------------------------------------------------------------

def run_pipeline():
    """Execute the full data pipeline and populate _cache."""
    print("=" * 60, flush=True)
    print("[Pipeline] Starting turbulence model computation...", flush=True)

    # 1. Fetch prices
    prices = fetch_prices()
    _cache["prices"] = prices

    # 2. Compute returns
    returns = prices.pct_change().iloc[1:]  # drop first NaN row
    # Forward-fill minor gaps (up to 3 days) then re-drop
    returns = returns.ffill(limit=3)
    # Clip extreme outliers (> 5 std) per column to reduce noise
    for col in returns.columns:
        col_std = returns[col].std()
        if col_std > 0:
            returns[col] = returns[col].clip(lower=-5 * col_std, upper=5 * col_std)
    _cache["returns"] = returns

    print(f"[Pipeline] Returns shape: {returns.shape}", flush=True)

    # 3. Compute turbulence
    rolling_turbulence = compute_rolling_turbulence(returns)
    ewma_turbulence = compute_ewma_turbulence(returns)

    # 4. Build turbulence series DataFrame
    turb_df = pd.DataFrame({
        "rolling_turbulence": rolling_turbulence,
        "ewma_turbulence": ewma_turbulence,
    }, index=returns.index)
    turb_df = turb_df.dropna(how="all")
    _cache["turbulence_series"] = turb_df

    # 5. Current KPIs
    latest = turb_df.dropna(how="all").iloc[-1]
    prev = turb_df.dropna(how="all").iloc[-2] if len(turb_df.dropna(how="all")) > 1 else latest

    rolling_val = float(latest["rolling_turbulence"]) if not pd.isna(latest["rolling_turbulence"]) else 0.0
    ewma_val = float(latest["ewma_turbulence"]) if not pd.isna(latest["ewma_turbulence"]) else 0.0
    rolling_prev = float(prev["rolling_turbulence"]) if not pd.isna(prev["rolling_turbulence"]) else rolling_val
    ewma_prev = float(prev["ewma_turbulence"]) if not pd.isna(prev["ewma_turbulence"]) else ewma_val

    # 1-year history for signal computation
    one_year_ago = turb_df.index[-1] - pd.DateOffset(years=1)
    series_1yr_rolling = turb_df.loc[turb_df.index >= one_year_ago, "rolling_turbulence"]
    series_1yr_ewma = turb_df.loc[turb_df.index >= one_year_ago, "ewma_turbulence"]

    signal_info = compute_signal(rolling_val, series_1yr_rolling)

    # 30-day sparklines
    sparkline_rolling = (
        turb_df["rolling_turbulence"].dropna().iloc[-30:].tolist()
    )
    sparkline_ewma = (
        turb_df["ewma_turbulence"].dropna().iloc[-30:].tolist()
    )

    # ---- SPX price series, 50-day MA, VIX ----
    # Fetch ^GSPC (actual S&P 500 index) separately for display level
    # Use SPY for turbulence computation but ^GSPC for display (~10x SPY)
    spx_price_spy = prices["SPY"] if "SPY" in prices.columns else pd.Series(dtype=float)
    
    # Try to get ^GSPC for true SPX level; fall back to SPY * 10
    try:
        spx_raw = yf.download("^GSPC", start=(datetime.today() - timedelta(days=LOOKBACK_YEARS * 365 + 60)).strftime("%Y-%m-%d"),
                              end=datetime.today().strftime("%Y-%m-%d"), auto_adjust=True, progress=False, timeout=30)
        if not spx_raw.empty:
            if isinstance(spx_raw.columns, pd.MultiIndex):
                spx_price = spx_raw["Close"]["^GSPC"] if ("Close" in spx_raw.columns.get_level_values(0)) else spx_raw.iloc[:, 0]
            elif "Close" in spx_raw.columns:
                spx_price = spx_raw["Close"]
            else:
                spx_price = spx_raw.iloc[:, 0]
            spx_price.index = pd.to_datetime(spx_price.index)
            print(f"[Pipeline] Fetched ^GSPC. Latest: {spx_price.iloc[-1]:.2f}", flush=True)
        else:
            spx_price = spx_price_spy * 10  # fallback
            print("[Pipeline] ^GSPC empty, using SPY*10 fallback", flush=True)
    except Exception as exc:
        print(f"[Pipeline] ^GSPC fetch failed ({exc}), using SPY*10 fallback", flush=True)
        spx_price = spx_price_spy * 10
    
    spx_50ma = spx_price.rolling(50).mean()

    vix_col = "^VIX"
    vix_series = prices[vix_col] if vix_col in prices.columns else pd.Series(dtype=float)

    latest_date = turb_df.dropna(how="all").index[-1]
    spx_latest = float(spx_price.loc[latest_date]) if latest_date in spx_price.index and not pd.isna(spx_price.loc[latest_date]) else None
    spx_50ma_latest = float(spx_50ma.loc[latest_date]) if latest_date in spx_50ma.index and not pd.isna(spx_50ma.loc[latest_date]) else None
    vix_latest = float(vix_series.loc[latest_date]) if latest_date in vix_series.index and not pd.isna(vix_series.loc[latest_date]) else None
    spx_above_50ma = (spx_latest > spx_50ma_latest) if spx_latest and spx_50ma_latest else None

    # ---- Divergence detection: turbulence elevated while SPX above 50-MA ----
    divergence_active = (signal_info["signal_status"] in ("ELEVATED", "CRITICAL")) and (spx_above_50ma is True)

    # ---- Days elevated: count consecutive trading days above p75 ----
    rolling_clean = turb_df["rolling_turbulence"].dropna()
    days_elevated = 0
    if len(rolling_clean) > 0:
        p75_val = signal_info["p75"]
        for val in rolling_clean.iloc[::-1]:
            if val >= p75_val:
                days_elevated += 1
            else:
                break

    # ---- AI sector turbulence (compute sub-turbulence for AI-related tickers) ----
    ai_tickers = [t for t in ["SMH", "SOXX", "IGV", "ARKK", "QQQ", "XLK"] if t in returns.columns]
    ai_turbulence_val = 0.0
    if len(ai_tickers) >= 2 and len(returns) > ROLLING_WINDOW:
        ai_window = returns[ai_tickers].iloc[-(ROLLING_WINDOW + 1):-1]
        ai_today = returns[ai_tickers].iloc[-1]
        ai_valid = ai_window.columns[ai_window.notna().all()]
        if len(ai_valid) >= 2:
            ai_w = ai_window[ai_valid].values
            ai_r = ai_today[ai_valid].values
            if not np.any(np.isnan(ai_r)):
                ai_mu = ai_w.mean(axis=0)
                ai_cov = np.cov(ai_w.T)
                ai_cov_inv = regularized_inv(ai_cov)
                if ai_cov_inv is not None:
                    ai_turbulence_val = mahalanobis_distance(ai_r, ai_mu, ai_cov_inv)

    ai_ratio = round(ai_turbulence_val / rolling_val, 2) if rolling_val > 0.01 else 0.0

    # ---- Warning level ----
    if signal_info["signal_status"] == "CRITICAL":
        warning_level = "HIGH RISK"
    elif signal_info["signal_status"] == "ELEVATED":
        warning_level = "ELEVATED RISK"
    else:
        warning_level = "NORMAL"

    # ---- Recommended actions ----
    if warning_level == "HIGH RISK":
        actions = [
            "Reduce position sizes by 50-75%",
            "Raise cash levels significantly",
            "Hedge long exposure (puts, inverse ETFs)",
            "Review stop-loss levels on all positions",
            "Avoid adding new positions",
            "Historical precedent: SPX typically falls 5-15% within 20 days from this signal",
            "Next critical check: DAILY until turbulence normalizes"
        ]
    elif warning_level == "ELEVATED RISK":
        actions = [
            "Reduce position sizes by 25-50%",
            "Raise cash levels",
            "Hedge long exposure (puts, inverse ETFs)",
            "Review stop-loss levels on all positions",
            "Avoid adding new positions",
            "Historical precedent: SPX typically falls 5-15% within 20 days from this signal",
            "Next critical check: DAILY until turbulence normalizes"
        ]
    else:
        actions = [
            "Normal position sizing appropriate",
            "Continue monitoring for regime changes",
            "Next check: daily at market close"
        ]

    # ---- SPX + turbulence overlay series for divergence chart ----
    spx_turb_series = []
    try:
        # Reindex SPX to turbulence dates for alignment
        spx_aligned = spx_price.reindex(turb_df.index, method="ffill")
        spx_50ma_aligned = spx_50ma.reindex(turb_df.index, method="ffill")
        
        for dt in turb_df.index:
            rv = turb_df.loc[dt, "rolling_turbulence"]
            sp = spx_aligned.loc[dt] if dt in spx_aligned.index else None
            sp50 = spx_50ma_aligned.loc[dt] if dt in spx_50ma_aligned.index else None
            
            sp_val = None if (sp is None or (isinstance(sp, float) and pd.isna(sp))) else float(sp)
            sp50_val = None if (sp50 is None or (isinstance(sp50, float) and pd.isna(sp50))) else float(sp50)
            rv_val = None if pd.isna(rv) else float(rv)
            
            is_div = (rv_val is not None) and (rv_val >= signal_info["p75"]) and (sp_val is not None) and (sp50_val is not None) and (sp_val > sp50_val)
            
            spx_turb_series.append({
                "date": str(dt.date()),
                "turbulence": round(rv_val, 2) if rv_val is not None else None,
                "spx": round(sp_val, 2) if sp_val is not None else None,
                "spx_50ma": round(sp50_val, 2) if sp50_val is not None else None,
                "divergence": is_div,
            })
        print(f"[Pipeline] SPX overlay computed: {len(spx_turb_series)} points", flush=True)
    except Exception as exc:
        print(f"[Pipeline] SPX overlay error: {exc}", flush=True)
        traceback.print_exc()
    _cache["spx_overlay"] = spx_turb_series

    _cache["current"] = {
        "rolling_value": round(rolling_val, 2),
        "ewma_value": round(ewma_val, 2),
        "rolling_delta": round(rolling_val - rolling_prev, 2),
        "ewma_delta": round(ewma_val - ewma_prev, 2),
        "signal_status": signal_info["signal_status"],
        "warning_level": warning_level,
        "percentile_rank": signal_info["percentile_rank"],
        "p75": round(signal_info["p75"], 2),
        "p95": round(signal_info["p95"], 2),
        "days_elevated": days_elevated,
        "spx_level": round(spx_latest, 2) if spx_latest else None,
        "spx_50ma": round(spx_50ma_latest, 2) if spx_50ma_latest else None,
        "spx_above_50ma": spx_above_50ma,
        "vix_level": round(vix_latest, 2) if vix_latest else None,
        "divergence_active": divergence_active,
        "ai_turbulence": round(ai_turbulence_val, 2),
        "market_turbulence": round(rolling_val, 2),
        "ai_ratio": ai_ratio,
        "actions": actions,
        "rolling_sparkline_30d": [round(v, 2) for v in sparkline_rolling],
        "ewma_sparkline_30d": [round(v, 2) for v in sparkline_ewma],
        "as_of_date": str(turb_df.dropna(how="all").index[-1].date()),
    }

    # 6. Per-asset stats
    asset_stats = compute_asset_stats(prices, returns, rolling_turbulence)
    _cache["assets"] = asset_stats

    # 7. Heatmap
    _cache["heatmap"] = compute_heatmap(asset_stats)

    _cache["last_updated"] = datetime.utcnow().isoformat() + "Z"
    _cache["ready"] = True

    print(f"[Pipeline] Done. Last updated: {_cache['last_updated']}", flush=True)
    print("=" * 60, flush=True)


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(title="Market Turbulence API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """Run the computation pipeline on startup."""
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, run_pipeline)
    except Exception as exc:
        print(f"[ERROR] Pipeline failed: {exc}", flush=True)
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Static files & root
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def serve_root():
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/turbulence")
def get_turbulence():
    """Full daily turbulence time series."""
    if not _cache["ready"] or _cache["turbulence_series"] is None:
        return {"error": "Data not ready yet. Try again shortly."}

    df = _cache["turbulence_series"]
    records = []
    for dt, row in df.iterrows():
        rv = row["rolling_turbulence"]
        ev = row["ewma_turbulence"]
        records.append({
            "date": str(dt.date()),
            "rolling_turbulence": None if pd.isna(rv) else round(float(rv), 6),
            "ewma_turbulence": None if pd.isna(ev) else round(float(ev), 6),
        })
    return records


@app.get("/api/current")
def get_current():
    """Latest KPI values."""
    if not _cache["ready"] or _cache["current"] is None:
        return {"error": "Data not ready yet. Try again shortly."}
    return _cache["current"]


@app.get("/api/assets")
def get_assets():
    """Per-asset detail for the latest trading day."""
    if not _cache["ready"] or _cache["assets"] is None:
        return {"error": "Data not ready yet. Try again shortly."}
    return _cache["assets"]


@app.get("/api/heatmap")
def get_heatmap():
    """Cross-asset z-score heatmap grouped by category."""
    if not _cache["ready"] or _cache["heatmap"] is None:
        return {"error": "Data not ready yet. Try again shortly."}
    return _cache["heatmap"]


@app.get("/api/spx_overlay")
def get_spx_overlay():
    """SPX + turbulence overlay with divergence detection."""
    if not _cache["ready"] or _cache.get("spx_overlay") is None:
        return {"error": "Data not ready yet. Try again shortly."}
    return _cache["spx_overlay"]


@app.get("/api/events")
def get_events():
    """Historical stress events for chart annotation."""
    return STRESS_EVENTS


@app.get("/api/health")
def health():
    return {
        "status": "ok" if _cache["ready"] else "computing",
        "last_updated": _cache.get("last_updated"),
        "as_of_date": _cache["current"]["as_of_date"] if _cache.get("current") else None,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
