# signals.py
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

import config
from logger import append_excel


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


def get_latest_and_oldest_mid(hist: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    mids = [h.get("mid") for h in hist if h.get("mid") is not None]
    if len(mids) < 2:
        return None, None
    return float(mids[-1]), float(mids[0])


def _passes_extreme(p: float) -> bool:
    return (p >= config.P_EXTREME_HIGH) or (p <= config.P_EXTREME_LOW)


def _recommended_side(p: float) -> str:
    # fade certainty
    return "no" if p >= config.P_EXTREME_HIGH else "yes"


def run_once() -> Dict[str, Any]:
    ts = utcnow_iso()

    cache = read_json(Path(config.MARKET_CACHE_FILE)) or {}
    rolling = read_json(Path(config.ROLLING_QUOTES_FILE)) or {"markets": {}}
    anchor = read_json(Path(config.ANCHOR_FILE)) or {"assets": {}}

    assets = cache.get("assets") or {}

    signals: List[Dict[str, Any]] = []
    debug_counts = {"evaluated": 0, "passed": 0}

    for asset, info in assets.items():
        mkts = info.get("markets") or []
        a = (anchor.get("assets") or {}).get(asset, {})
        anchor_strength = float(a.get("anchor_strength") or 0.0)

        # We still log evaluations even if anchor blocks
        anchor_block = anchor_strength >= config.ANCHOR_STRENGTH_MAX

        best = None
        best_score = -1e9

        for m in mkts:
            t = m.get("market_ticker")
            if not t:
                continue

            rec = (rolling.get("markets") or {}).get(t)
            hist = (rec or {}).get("history") or []
            if len(hist) < 2:
                continue

            latest = hist[-1]
            mid = latest.get("mid")
            bid = latest.get("yes_bid")
            ask = latest.get("yes_ask")

            debug_counts["evaluated"] += 1

            passed = True
            reason = "PASS"

            if mid is None or bid is None or ask is None:
                passed = False
                reason = "missing_l1"
            else:
                p = float(mid)
                spread = float(ask - bid)
                t_rem = minutes_to(m.get("close_time"))
                newest, oldest = get_latest_and_oldest_mid(hist)
                belief_speed = abs(newest - oldest) if (newest is not None and oldest is not None) else 0.0
                belief_strength = abs(p - 0.5)

                # gates (in order)
                if anchor_block:
                    passed = False
                    reason = "anchor_block"
                elif spread > config.MAX_SPREAD_ALLOWED:
                    passed = False
                    reason = "spread"
                elif not _passes_extreme(p):
                    passed = False
                    reason = "not_extreme"
                elif (t_rem is None) or (t_rem < config.MIN_TIME_REMAINING_MIN):
                    passed = False
                    reason = "time_remaining"
                elif belief_speed < config.BELIEF_SPEED_MIN_MOVE:
                    passed = False
                    reason = "belief_speed"

                side = _recommended_side(p) if passed else ""

                # Excel log: one row per evaluation
                append_excel(
                    config.SIGNALS_XLSX,
                    sheet="signals",
                    row={
                        "ts_utc": ts,
                        "asset": asset,
                        "market_ticker": t,
                        "title": m.get("title", ""),
                        "p": p,
                        "bid": bid,
                        "ask": ask,
                        "spread": spread,
                        "belief_speed": belief_speed,
                        "belief_strength": belief_strength,
                        "time_remaining_min": t_rem,
                        "anchor_strength": anchor_strength,
                        "passed": passed,
                        "reason": reason,
                        "side": side,
                    },
                )

                if passed:
                    score = belief_strength - spread
                    if score > best_score:
                        best_score = score
                        best = {
                            "asset": asset,
                            "market_ticker": t,
                            "title": m.get("title"),
                            "close_time": m.get("close_time"),
                            "p": p,
                            "bid": float(bid),
                            "ask": float(ask),
                            "spread": spread,
                            "belief_speed": belief_speed,
                            "belief_strength": belief_strength,
                            "time_remaining_min": t_rem,
                            "anchor_strength": anchor_strength,
                            "action": "buy",
                            "side": _recommended_side(p),
                            "score": score,
                        }

        if best:
            debug_counts["passed"] += 1
            signals.append(best)

    out = {"ts_utc": ts, "signals": signals, "debug": debug_counts}
    atomic_write_json(Path(config.SIGNAL_FILE), out)
    return out


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
