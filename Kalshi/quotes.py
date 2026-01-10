# 03_kalshi_quote_poll.py
# ============================================================
# Purpose:
#   Poll minimal L1 quote data for every strike market in the cached event:
#     yes_bid, yes_ask, mid, last_price (if available)
#   Keep a rolling window (~15 min) per market in rolling_quotes.json
#
# Runs: every 30–60s (called from main loop)
# ============================================================

from __future__ import annotations

import json
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

import config
from kalshi_client import KalshiClient

def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)

def _pick_price_field(mkt: Dict[str, Any], cents_field: str, dollars_field: str) -> Optional[float]:
    """
    Prefer dollars field if present, else cents field -> dollars.
    """
    if dollars_field in mkt and mkt[dollars_field] is not None:
        try:
            return float(mkt[dollars_field])
        except Exception:
            pass
    if cents_field in mkt and mkt[cents_field] is not None:
        try:
            return float(mkt[cents_field]) / 100.0
        except Exception:
            pass
    return None

def parse_l1(market_payload: Dict[str, Any]) -> Dict[str, Any]:
    mkt = market_payload.get("market") or market_payload  # some endpoints nest under "market"
    # handle new-ish dollars fields first, fallback to cents
    yes_bid = _pick_price_field(mkt, "yes_bid", "yes_bid_dollars")
    yes_ask = _pick_price_field(mkt, "yes_ask", "yes_ask_dollars")
    last = _pick_price_field(mkt, "last_price", "last_price_dollars")

    # If ask missing but bid exists, keep None; we'll gate by spread
    mid = None
    if yes_bid is not None and yes_ask is not None:
        mid = (yes_bid + yes_ask) / 2.0
    elif last is not None:
        mid = last

    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "mid": mid,
        "last": last,
        "status": mkt.get("status"),
        "close_time": mkt.get("close_time"),
        "expiration_time": mkt.get("expiration_time"),
    }

def _extract_market_tickers_from_cache(cache: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    returns {asset: [market_ticker, ...]} from your 01 cache format.
    """
    out: Dict[str, List[str]] = {}
    assets = (cache.get("assets") or {})
    for asset, info in assets.items():
        mkts = info.get("markets") or []
        tickers = [m.get("market_ticker") for m in mkts if m.get("market_ticker")]
        out[asset] = tickers
    return out

def run_once(client: KalshiClient) -> Dict[str, Any]:
    cache = read_json(Path(config.MARKET_CACHE_FILE))
    if not cache:
        return {"last_updated_utc": utcnow_iso(), "markets": {}}

    m_by_asset = _extract_market_tickers_from_cache(cache)

    rolling_path = Path(config.ROLLING_QUOTES_FILE)
    rolling = read_json(rolling_path) or {"last_updated_utc": None, "markets": {}}

    # rolling window config
    window_minutes = int(getattr(config, "ROLLING_WINDOW_MINUTES", 15))
    max_points = max(5, int((window_minutes * 60) / getattr(config, "ROLLING_SAMPLE_SECONDS", 60)) + 3)

    now = utcnow_iso()
    for asset, tickers in m_by_asset.items():
        for t in tickers:
            try:
                payload = client.get_market(t)
                l1 = parse_l1(payload)
            except Exception as e:
                # non-blocking: skip market on error
                continue

            if t not in rolling["markets"]:
                rolling["markets"][t] = {"asset": asset, "history": []}

            hist = rolling["markets"][t]["history"]
            hist.append({"ts": now, **l1})

            # trim
            if len(hist) > max_points:
                rolling["markets"][t]["history"] = hist[-max_points:]

    rolling["last_updated_utc"] = now
    atomic_write_json(rolling_path, rolling)
    return rolling

if __name__ == "__main__":
    client = KalshiClient(auth=None)
    print(json.dumps(run_once(client), indent=2))
