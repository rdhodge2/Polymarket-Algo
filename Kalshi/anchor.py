# 02_alpaca_anchor.py
# ============================================================
# Purpose:
#   Compute a simple "anchor" per asset:
#     ret_15m, ret_30m, realized_vol_30m
#     anchor_strength = |ret_30m| / vol_30m
#     anchor_dir = sign(ret_30m)
#
# Writes: anchor_metrics.json
# Runs: every 60s (called from main loop)
# ============================================================

from __future__ import annotations

import time
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

import pandas as pd
import numpy as np

import config

def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)

def compute_anchor(df: pd.DataFrame) -> Dict[str, float]:
    """
    df must have timestamp (datetime) and close (float) at 1-min frequency.
    """
    df = df.sort_values("timestamp").dropna(subset=["close"]).copy()
    if len(df) < 40:
        return {"ret_15m": 0.0, "ret_30m": 0.0, "vol_30m": 0.0, "anchor_strength": 0.0, "anchor_dir": 0.0}

    closes = df["close"].to_numpy()

    def ret_n(n: int) -> float:
        if len(closes) < n + 1:
            return 0.0
        return (closes[-1] / closes[-(n + 1)]) - 1.0

    r15 = ret_n(15)
    r30 = ret_n(30)

    # realized vol over last 30 1-min returns
    rets_1m = np.diff(np.log(closes[-31:])) if len(closes) >= 32 else np.diff(np.log(closes))
    vol30 = float(np.std(rets_1m)) if len(rets_1m) > 5 else 0.0

    anchor_strength = float(abs(r30) / (vol30 + 1e-9)) if vol30 > 0 else 0.0
    anchor_dir = float(np.sign(r30))

    return {
        "ret_15m": float(r15),
        "ret_30m": float(r30),
        "vol_30m": float(vol30),
        "anchor_strength": anchor_strength,
        "anchor_dir": anchor_dir,
    }

def fetch_alpaca_crypto_bars(symbol: str, minutes_back: int = 120) -> pd.DataFrame:
    """
    Uses alpaca-py CryptoHistoricalDataClient (preferred).
    """
    try:
        from alpaca.data.historical import CryptoHistoricalDataClient
        from alpaca.data.requests import CryptoBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except Exception as e:
        raise RuntimeError(
            "alpaca-py not available. Install: pip install alpaca-py"
        ) from e

    client = CryptoHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_API_SECRET)

    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes_back)

    req = CryptoBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
    )
    bars = client.get_crypto_bars(req).df
    if bars is None or len(bars) == 0:
        return pd.DataFrame(columns=["timestamp", "close"])

    # alpaca-py returns multiindex (symbol, timestamp)
    bars = bars.reset_index()
    if "timestamp" not in bars.columns:
        # sometimes it's "time" depending on version
        for c in bars.columns:
            if "time" in c.lower():
                bars = bars.rename(columns={c: "timestamp"})
                break

    return bars[["timestamp", "close"]]

def run_once() -> Dict[str, Any]:
    # adjust if your Alpaca symbols differ
    SYMBOL_MAP = {
        "BTC": "BTC/USD",
        "ETH": "ETH/USD",
        "SOL": "SOL/USD",
    }

    out: Dict[str, Any] = {"last_updated_utc": utcnow_iso(), "assets": {}}

    for asset in getattr(config, "ASSETS", ["BTC", "ETH", "SOL"]):
        symbol = SYMBOL_MAP.get(asset)
        if not symbol:
            continue
        df = fetch_alpaca_crypto_bars(symbol, minutes_back=120)
        metrics = compute_anchor(df)
        out["assets"][asset] = {"symbol": symbol, **metrics}

    atomic_write_json(Path(config.ANCHOR_FILE), out)
    return out

if __name__ == "__main__":
    print(run_once())
