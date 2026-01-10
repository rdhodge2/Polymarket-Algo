# execution.py
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import config
from logger import append_excel

from kalshi_client import KalshiClient, KalshiAuth
from config import load_kalshi_private_key


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


def minutes_to(close_time_iso: Optional[str]) -> Optional[float]:
    if not close_time_iso:
        return None
    s = close_time_iso
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        ct = datetime.fromisoformat(s)
        return (ct - datetime.now(timezone.utc)).total_seconds() / 60.0
    except Exception:
        return None


def load_positions() -> Dict[str, Any]:
    return read_json(Path(config.POSITIONS_FILE)) or {"ts_utc": None, "positions": {}}


def save_positions(p: Dict[str, Any]) -> None:
    p["ts_utc"] = utcnow_iso()
    atomic_write_json(Path(config.POSITIONS_FILE), p)


def get_client() -> KalshiClient:
    if config.PAPER_TRADING:
        return KalshiClient(auth=None)

    config.require_env()
    pem = load_kalshi_private_key()
    auth = KalshiAuth(
        api_key_id=config.KALSHI_API_KEY_ID,
        private_key_pem=pem,
        base_url=config.KALSHI_BASE_URL,
    )
    return KalshiClient(auth=auth, timeout=getattr(config, "HTTP_TIMEOUT_SECONDS", 10))


def run_once(client: KalshiClient) -> None:
    ts = utcnow_iso()

    sig = read_json(Path(config.SIGNAL_FILE)) or {"signals": []}
    signals = sig.get("signals") or []

    rolling = read_json(Path(config.ROLLING_QUOTES_FILE)) or {"markets": {}}
    anchor = read_json(Path(config.ANCHOR_FILE)) or {"assets": {}}

    pos = load_positions()
    positions: Dict[str, Any] = pos["positions"]

    # -------------------------
    # Manage open positions
    # -------------------------
    to_close = []

    for asset, p in list(positions.items()):
        market_ticker = p["market_ticker"]
        rec = (rolling.get("markets") or {}).get(market_ticker)
        hist = (rec or {}).get("history") or []
        if not hist:
            continue

        latest = hist[-1]
        mid = latest.get("mid")
        bid = latest.get("yes_bid")
        ask = latest.get("yes_ask")
        if mid is None or bid is None or ask is None:
            continue

        cur_p = float(mid)
        entry_p = float(p["entry_p"])
        side = p["side"]  # "yes" or "no"

        # Profit direction: YES profits when p rises; NO profits when p falls
        pnl_move = (cur_p - entry_p) if side == "yes" else (entry_p - cur_p)

        t_rem = minutes_to(p.get("close_time"))
        a = (anchor.get("assets") or {}).get(asset, {})
        anchor_strength = float(a.get("anchor_strength") or 0.0)

        exit_reason = None
        if pnl_move >= config.PROFIT_TAKE:
            exit_reason = "profit"
        elif pnl_move <= -config.STOP_LOSS_MOVE:
            exit_reason = "stop"
        elif (t_rem is not None) and (t_rem <= config.EXIT_LAST_N_MINUTES):
            exit_reason = "time_stop"
        elif anchor_strength >= config.ANCHOR_STRENGTH_BREAK:
            exit_reason = "regime_break"

        if exit_reason:
            exit_p = cur_p  # paper close at mid
            exit_ts = utcnow_iso()

            # Excel trade log row
            append_excel(
                config.TRADES_XLSX,
                sheet="trades",
                row={
                    "ts_utc": exit_ts,
                    "asset": asset,
                    "market_ticker": market_ticker,
                    "action": "EXIT",
                    "side": side,
                    "price": exit_p,
                    "entry_p": entry_p,
                    "pnl_move": pnl_move,
                    "exit_reason": exit_reason,
                    "paper": config.PAPER_TRADING,
                    "count": p.get("count", ""),
                },
            )

            # mark for removal
            to_close.append(asset)

    for asset in to_close:
        positions.pop(asset, None)

    # -------------------------
    # Open new positions
    # -------------------------
    for s in signals:
        asset = s["asset"]
        if asset in positions:
            continue  # already holding

        # basic sizing
        bankroll = float(getattr(config, "BANKROLL_USD", 10000))
        risk_frac = float(getattr(config, "RISK_PER_TRADE", 0.015))
        dollars = bankroll * risk_frac

        # rough contract sizing (MVP)
        approx_cost = 0.50
        count = max(1, int(dollars / approx_cost))

        entry_p = float(s["p"])  # paper fill at mid
        entry_ts = utcnow_iso()

        positions[asset] = {
            "entry_ts_utc": entry_ts,
            "asset": asset,
            "market_ticker": s["market_ticker"],
            "side": s["side"],
            "count": count,
            "entry_p": entry_p,
            "close_time": s.get("close_time"),
            "rationale": {
                "p": s["p"],
                "belief_speed": s["belief_speed"],
                "time_remaining_min": s["time_remaining_min"],
                "anchor_strength": s["anchor_strength"],
                "spread": s["spread"],
                "score": s.get("score", ""),
            },
            "paper": config.PAPER_TRADING,
        }

        append_excel(
            config.TRADES_XLSX,
            sheet="trades",
            row={
                "ts_utc": entry_ts,
                "asset": asset,
                "market_ticker": s["market_ticker"],
                "action": "ENTRY",
                "side": s["side"],
                "price": entry_p,
                "entry_p": entry_p,
                "pnl_move": "",
                "exit_reason": "",
                "paper": config.PAPER_TRADING,
                "count": count,
            },
        )

    save_positions(pos)
