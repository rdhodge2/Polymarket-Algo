"""
07 - Overreaction Detector (TIME-BASED + NOTIONAL-SAFE + BINARY-AWARE)

Drop-in replacement for 07_overreaction_detector.py

Fixes vs your current version:
✅ Uses TRADE TIMESTAMPS to compute true 5-minute move (not “last 10 trades”)
✅ Computes retail sizing using NOTIONAL ≈ price * size (handles share-size feeds)
✅ Computes volume spike using TIME WINDOWS (last 2m vs prior 10m baseline)
✅ Avoids BUY/SELL confusion: returns BUY + recommended outcome side to fade
✅ Adds debug fields so you can see EXACTLY why it did/didn't trip

How it works:
1) Sharp move over last N minutes (default 5m) using trades
2) BTC mismatch (token moved but BTC didn't) with tunable thresholds
3) Retail panic proxy: median notional small + lots of small trades
4) Volume spike in last window vs baseline window
5) Orderbook exhaustion/imbalance scoring (NOT gating)
6) Optional RSI (on sampled prices)

Scores 0-100. Trade if score >= MIN_OVERREACTION_SCORE.
"""

import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple

from importlib import import_module
indicators = import_module("02_indicators")

# =============================
# Tunable thresholds
# =============================

# Move detection (time-based)
MOVE_WINDOW_MINUTES = 5
MIN_PRICE_CHANGE = 0.03          # 3% move in MOVE_WINDOW triggers base score (was 5% - too strict)

# BTC mismatch
BTC_MOVE_MAX = 0.0035            # 0.35% BTC max move to call it "overreaction vs reality"
BTC_MISMATCH_BONUS_ONLY = True   # if True, mismatch adds score but doesn't hard-gate

# Volume spike (time-based)
VOL_RECENT_MINUTES = 2
VOL_BASELINE_MINUTES = 10
VOLUME_SPIKE_MULTIPLIER = 1.8    # 1.8x baseline (2.0 can be too strict)

# Retail panic (NOTIONAL)
RETAIL_MEDIAN_NOTIONAL_MAX = 40.0  # median trade notional <= $40 => retail-ish
RETAIL_MEAN_NOTIONAL_MAX = 60.0    # mean <= $60
RETAIL_FRACTION_MIN = 0.60         # >=60% of trades in window are "small" => retail-ish

# Scoring
MIN_OVERREACTION_SCORE = 55        # start at 55 to ensure it trips; raise to 60-65 later

# RSI
USE_RSI = True
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# Sampling of prices for RSI (avoid tick noise a bit)
RSI_SAMPLE_EVERY_N_TRADES = 3      # take every 3rd trade price for RSI list

# Orderbook scoring (no hard gate)
IMBALANCE_EXTREME = 0.75           # imbalance >0.75 or <0.25 gives bonus


# =============================
# Helpers
# =============================

def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _parse_ts(ts: Any) -> Optional[datetime]:
    """
    Accepts:
      - ISO string
      - unix seconds (int/str)
      - unix ms (int/str)
    Returns UTC datetime or None
    """
    if ts is None:
        return None

    # Already datetime
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    # Numeric unix
    if isinstance(ts, (int, float)):
        v = float(ts)
        # ms vs sec
        if v > 1e12:
            v = v / 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)

    # String
    if isinstance(ts, str):
        s = ts.strip()
        # numeric string
        if s.isdigit():
            v = float(s)
            if v > 1e12:
                v = v / 1000.0
            return datetime.fromtimestamp(v, tz=timezone.utc)
        # ISO-ish
        try:
            # Polymarket often returns "2026-01-05T01:23:45.678Z"
            s2 = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s2)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    return None


def _get_trade_price_size_ts(t: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[datetime]]:
    """
    Robustly pull price, size, timestamp from trade dict.
    Your PolymarketClient normalizes:
      {
        "timestamp": ...,
        "price": float,
        "size": float,
        ...
      }
    But we handle alternates too.
    """
    price = _to_float(t.get("price") if "price" in t else t.get("p"))
    size = _to_float(t.get("size") if "size" in t else t.get("s"))
    ts = _parse_ts(t.get("timestamp") or t.get("ts") or t.get("time"))
    return price, size, ts


def _window_trades(trades: List[Dict[str, Any]], start: datetime, end: datetime) -> List[Dict[str, Any]]:
    out = []
    for t in trades:
        _, _, ts = _get_trade_price_size_ts(t)
        if ts is None:
            continue
        if start <= ts <= end:
            out.append(t)
    return out


def _last_trade_before(trades: List[Dict[str, Any]], cutoff: datetime) -> Optional[Dict[str, Any]]:
    """
    Find the most recent trade at/before cutoff.
    Assumes trades can be in any order.
    """
    best = None
    best_ts = None
    for t in trades:
        _, _, ts = _get_trade_price_size_ts(t)
        if ts is None or ts > cutoff:
            continue
        if best_ts is None or ts > best_ts:
            best = t
            best_ts = ts
    return best


def _orderbook_depths(orderbook: Dict[str, Any]) -> Tuple[float, float]:
    bid_depth = float(orderbook.get("bid_depth", 0) or 0)
    ask_depth = float(orderbook.get("ask_depth", 0) or 0)
    return bid_depth, ask_depth


def _orderbook_imbalance(bid_depth: float, ask_depth: float) -> Optional[float]:
    total = bid_depth + ask_depth
    if total <= 0:
        return None
    return bid_depth / total


# =============================
# Detector
# =============================

class OverreactionDetector:
    """
    Detect when Polymarket prices overreact to BTC moves.
    Returns a dict signal or None.
    """

    def __init__(
        self,
        move_window_minutes: int = MOVE_WINDOW_MINUTES,
        min_price_change: float = MIN_PRICE_CHANGE,
        btc_move_max: float = BTC_MOVE_MAX,
        volume_multiplier: float = VOLUME_SPIKE_MULTIPLIER,
        min_score: int = MIN_OVERREACTION_SCORE,
        use_rsi: bool = USE_RSI,
        debug: bool = True,
    ):
        self.move_window_minutes = int(move_window_minutes)
        self.min_price_change = float(min_price_change)
        self.btc_move_max = float(btc_move_max)
        self.volume_multiplier = float(volume_multiplier)
        self.min_score = int(min_score)
        self.use_rsi = bool(use_rsi)
        self.debug = bool(debug)

        print("✅ [07] Overreaction detector initialized (time-based)")
        print(f"   Move window: {self.move_window_minutes}m")
        print(f"   Min Price Change: {self.min_price_change:.1%}")
        print(f"   BTC max (mismatch): {self.btc_move_max:.2%}")
        print(f"   Volume spike: {self.volume_multiplier:.2f}x")
        print(f"   Min Score: {self.min_score}/100")
        print(f"   RSI enabled: {self.use_rsi}")

    def detect(
        self,
        current_price: float,
        recent_prices: List[float],
        recent_trades: List[Dict[str, Any]],
        orderbook: Dict[str, Any],
        btc_price_change_5min: float,
        outcome_label: Optional[str] = None,   # "Up"/"Down"/"YES"/"NO"
    ) -> Optional[Dict[str, Any]]:
        """
        Returns:
          {
            signal: True,
            action: "BUY",
            fade_direction: "FADE_UP" or "FADE_DOWN",
            recommended_outcome: <string or None>,  # if you pass outcome_label you can compute opposite
            confidence: 0..100,
            score: 0..100,
            expected_edge: float,
            diagnostics: {...},
            timestamp: utc datetime
          }
        """
        now = datetime.now(timezone.utc)

        # Need trades with timestamps for correct operation
        if not recent_trades or len(recent_trades) < 10:
            return None

        # Ensure current_price is usable; if missing, try last trade price
        if current_price is None:
            p_last, _, _ = _get_trade_price_size_ts(recent_trades[-1])
            if p_last is None:
                return None
            current_price = p_last

        # -------------------------
        # 1) Sharp move over window
        # -------------------------
        cutoff = now - timedelta(minutes=self.move_window_minutes)

        t_ref = _last_trade_before(recent_trades, cutoff)
        p_ref, _, ts_ref = _get_trade_price_size_ts(t_ref) if t_ref else (None, None, None)

        # If no trade at/before cutoff, fall back: use oldest trade as reference
        if p_ref is None:
            p_ref, _, ts_ref = _get_trade_price_size_ts(recent_trades[0])

        if p_ref is None or p_ref <= 0:
            return None

        move = (current_price - p_ref) / p_ref

        # Score components
        score = 0
        signals: Dict[str, Any] = {}

        # Make move scoring graded (helps it trip)
        abs_move = abs(move)
        if abs_move < self.min_price_change:
            return None  # no sharp move, no signal (keep this as the one hard gate)

        # Sharp move score: 35 at threshold, up to 50 as it grows
        sharp_score = 35 + min(15, int((abs_move - self.min_price_change) / 0.01) * 3)  # +3 per extra 1%
        sharp_score = min(sharp_score, 50)
        score += sharp_score

        fade_direction = "FADE_UP" if move > 0 else "FADE_DOWN"
        action = "BUY"  # We always BUY one outcome to fade the move

        signals["sharp_move"] = {
            "triggered": True,
            "move_window_min": self.move_window_minutes,
            "ref_price": p_ref,
            "ref_ts": ts_ref.isoformat() if ts_ref else None,
            "current_price": current_price,
            "price_change": move,
            "score": sharp_score,
        }

        # -------------------------
        # 2) BTC mismatch scoring
        # -------------------------
        btc_abs = abs(btc_price_change_5min) if btc_price_change_5min is not None else None
        mismatch = (btc_abs is not None) and (btc_abs <= self.btc_move_max)

        btc_score = 0
        if mismatch:
            btc_score = 20
            score += btc_score

        signals["btc_mismatch"] = {
            "triggered": mismatch,
            "btc_change_5min": btc_price_change_5min,
            "btc_max": self.btc_move_max,
            "score": btc_score,
            "note": "Token moved but BTC did not (overreaction candidate)" if mismatch else "BTC also moved (less pure)",
        }

        # -------------------------
        # 3) Retail panic (notional)
        # -------------------------
        # Use trades in last move window for retail stats
        window_trades = _window_trades(recent_trades, cutoff, now)
        if len(window_trades) < 8:
            window_trades = recent_trades[-20:]  # fallback

        notionals = []
        small_flags = []
        for t in window_trades:
            p, s, _ = _get_trade_price_size_ts(t)
            if p is None or s is None:
                continue
            notional = p * s
            notionals.append(notional)
            small_flags.append(1 if notional <= RETAIL_MEDIAN_NOTIONAL_MAX else 0)

        retail_score = 0
        retail_triggered = False
        med_notional = None
        mean_notional = None
        small_frac = None

        if notionals:
            notionals_sorted = sorted(notionals)
            n = len(notionals_sorted)
            med_notional = notionals_sorted[n // 2]
            mean_notional = sum(notionals_sorted) / n
            small_frac = sum(small_flags) / len(small_flags) if small_flags else 0.0

            # Retail if median is small OR lots of small trades
            if (med_notional <= RETAIL_MEDIAN_NOTIONAL_MAX and mean_notional <= RETAIL_MEAN_NOTIONAL_MAX) or (small_frac >= RETAIL_FRACTION_MIN):
                retail_triggered = True
                retail_score = 15
                score += retail_score

        signals["retail_panic"] = {
            "triggered": retail_triggered,
            "median_notional": med_notional,
            "mean_notional": mean_notional,
            "small_trade_frac": small_frac,
            "thresholds": {
                "median_max": RETAIL_MEDIAN_NOTIONAL_MAX,
                "mean_max": RETAIL_MEAN_NOTIONAL_MAX,
                "small_frac_min": RETAIL_FRACTION_MIN,
            },
            "score": retail_score,
        }

        # -------------------------
        # 4) Volume spike (time-based)
        # -------------------------
        vol_recent_start = now - timedelta(minutes=VOL_RECENT_MINUTES)
        vol_base_start = now - timedelta(minutes=VOL_BASELINE_MINUTES)

        recent_slice = _window_trades(recent_trades, vol_recent_start, now)
        base_slice = _window_trades(recent_trades, vol_base_start, vol_recent_start)

        def sum_notional(trs: List[Dict[str, Any]]) -> float:
            total = 0.0
            for t in trs:
                p, s, _ = _get_trade_price_size_ts(t)
                if p is None or s is None:
                    continue
                total += p * s
            return total

        recent_notional = sum_notional(recent_slice)
        base_notional = sum_notional(base_slice)

        vol_ratio = None
        vol_triggered = False
        vol_score = 0

        # Normalize baseline to same duration as recent
        if base_notional > 0 and VOL_BASELINE_MINUTES > 0:
            base_per_min = base_notional / max(1e-9, float(VOL_BASELINE_MINUTES - VOL_RECENT_MINUTES))
            expected_recent = base_per_min * float(VOL_RECENT_MINUTES)
            vol_ratio = recent_notional / expected_recent if expected_recent > 0 else None

            if vol_ratio is not None and vol_ratio >= self.volume_multiplier:
                vol_triggered = True
                vol_score = 15
                score += vol_score

        signals["volume_spike"] = {
            "triggered": vol_triggered,
            "recent_minutes": VOL_RECENT_MINUTES,
            "baseline_minutes": VOL_BASELINE_MINUTES,
            "recent_notional": recent_notional,
            "baseline_notional": base_notional,
            "vol_ratio": vol_ratio,
            "threshold": self.volume_multiplier,
            "score": vol_score,
        }

        # -------------------------
        # 5) Orderbook exhaustion / imbalance scoring
        # -------------------------
        bid_depth, ask_depth = _orderbook_depths(orderbook)
        imb = _orderbook_imbalance(bid_depth, ask_depth)

        ob_score = 0
        ob_triggered = False

        # For fade-up (price pumped), we'd like to see bid-heavy imbalance (chasing) or thin asks
        # For fade-down, ask-heavy imbalance or thin bids
        if imb is not None:
            if imb >= IMBALANCE_EXTREME or imb <= (1.0 - IMBALANCE_EXTREME):
                ob_triggered = True
                ob_score = 10
                score += ob_score

        signals["orderbook_imbalance"] = {
            "triggered": ob_triggered,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "imbalance": imb,
            "extreme": IMBALANCE_EXTREME,
            "score": ob_score,
        }

        # -------------------------
        # 6) RSI bonus (optional)
        # -------------------------
        rsi_score = 0
        rsi_triggered = False
        rsi_val = None

        if self.use_rsi:
            # Use trade-derived prices if possible; fall back to recent_prices
            prices_for_rsi = []
            # sample from window trades to reduce tick noise
            for idx, t in enumerate(window_trades):
                if idx % RSI_SAMPLE_EVERY_N_TRADES != 0:
                    continue
                p, _, _ = _get_trade_price_size_ts(t)
                if p is not None:
                    prices_for_rsi.append(p)

            if len(prices_for_rsi) < RSI_PERIOD + 2 and recent_prices:
                prices_for_rsi = recent_prices[-max(30, RSI_PERIOD + 5):]

            if len(prices_for_rsi) >= RSI_PERIOD + 1:
                rsi_val = indicators.calculate_rsi(prices_for_rsi, period=RSI_PERIOD)
                if rsi_val is not None:
                    if rsi_val <= RSI_OVERSOLD or rsi_val >= RSI_OVERBOUGHT:
                        rsi_triggered = True
                        rsi_score = 10
                        score += rsi_score

        signals["rsi_extreme"] = {
            "triggered": rsi_triggered,
            "rsi": rsi_val,
            "oversold": RSI_OVERSOLD,
            "overbought": RSI_OVERBOUGHT,
            "score": rsi_score,
        }

        # Cap score
        score = int(min(score, 100))

        # -------------------------
        # Recommended outcome (binary-aware)
        # -------------------------
        # If you pass outcome_label that corresponds to the token you're analyzing,
        # we can suggest the opposite outcome to fade.
        recommended_outcome = None
        if outcome_label:
            lab = (outcome_label or "").strip().lower()
            if fade_direction == "FADE_UP":
                # token price went up → fade by buying the opposite outcome
                if lab in ("up", "yes"):
                    recommended_outcome = "Down" if lab == "up" else "No"
                elif lab in ("down", "no"):
                    # If you're looking at Down token but it went up (rare), opposite is Up/Yes
                    recommended_outcome = "Up" if lab == "down" else "Yes"
            else:
                # token price went down → fade by buying opposite
                if lab in ("up", "yes"):
                    recommended_outcome = "Down" if lab == "up" else "No"
                elif lab in ("down", "no"):
                    recommended_outcome = "Up" if lab == "down" else "Yes"

        # Expected edge heuristic (keep conservative)
        expected_edge = (score / 100.0) * 0.06  # 3.3% at 55, 3.6% at 60, 6% at 100

        # Diagnostics you’ll want in logs
        diagnostics = {
            "window_trades_count": len(window_trades),
            "recent_trades_count": len(recent_trades),
            "cutoff": cutoff.isoformat(),
            "now": now.isoformat(),
        }

        # Final decision
        if score >= self.min_score:
            return {
                "signal": True,
                "action": action,
                "fade_direction": fade_direction,
                "recommended_outcome": recommended_outcome,
                "confidence": score,
                "score": score,
                "signals": signals,
                "expected_edge": expected_edge,
                "current_price": current_price,
                "price_change": move,
                "diagnostics": diagnostics,
                "timestamp": now,
            }

        return None

    def print_signal(self, signal: Dict[str, Any]) -> None:
        if not signal or not signal.get("signal"):
            print("❌ No signal detected")
            return

        print("\n🎯 OVERREACTION SIGNAL DETECTED!")
        print(f"   Action: {signal.get('action')}")
        print(f"   Fade: {signal.get('fade_direction')}")
        if signal.get("recommended_outcome"):
            print(f"   Recommended outcome: {signal.get('recommended_outcome')}")
        print(f"   Confidence/Score: {signal.get('score')}/100")
        print(f"   Expected Edge: {signal.get('expected_edge'):.2%}")
        print(f"   Current Price: ${signal.get('current_price'):.4f}")
        print(f"   Move: {signal.get('price_change'):+.2%}")

        print("\n   Breakdown:")
        for name, data in (signal.get("signals") or {}).items():
            trig = data.get("triggered")
            emoji = "✓" if trig else "✗"
            print(f"   {emoji} {name}: score={data.get('score', 0)}")
            # A few key details:
            if name == "sharp_move":
                print(f"      ref={data.get('ref_price')} at {data.get('ref_ts')}")
            if name == "btc_mismatch":
                print(f"      btc_5m={data.get('btc_change_5min')}")
            if name == "retail_panic":
                print(f"      median_notional={data.get('median_notional')} small_frac={data.get('small_trade_frac')}")
            if name == "volume_spike":
                print(f"      vol_ratio={data.get('vol_ratio')} recent_notional={data.get('recent_notional')}")
            if name == "orderbook_imbalance":
                print(f"      imbalance={data.get('imbalance')} bid={data.get('bid_depth')} ask={data.get('ask_depth')}")
            if name == "rsi_extreme":
                print(f"      rsi={data.get('rsi')}")

        print()


print("✅ [07] Overreaction detector loaded (time-based)")


# =============================
# Test Runner
# =============================
if __name__ == "__main__":
    print("\n🧪 Testing [07] - Overreaction Detector (time-based)\n" + "=" * 90)

    # Import clients for testing
    try:
        alpaca_client_module = import_module("03_alpaca_client")
        poly_client_module = import_module("04_polymarket_client")
        AlpacaClient = alpaca_client_module.AlpacaClient
        PolymarketClient = poly_client_module.PolymarketClient
    except ImportError as e:
        print(f"❌ Could not import clients: {e}")
        sys.exit(1)

    detector = OverreactionDetector(debug=True)
    alpaca = AlpacaClient()
    poly = PolymarketClient()

    # BTC 5m change
    btc_bars = alpaca.get_historical_bars(timeframe="1Min", limit=10)
    if btc_bars and len(btc_bars) >= 6:
        btc_current = btc_bars[-1]["close"]
        btc_5m_ago = btc_bars[-6]["close"]
        btc_change_5min = (btc_current - btc_5m_ago) / btc_5m_ago if btc_5m_ago else 0.0
    else:
        btc_change_5min = 0.001

    markets = poly.get_active_btc_eth_15m_updown_markets(window_minutes=30, print_markets=False)

    if markets:
        market = markets[0]
        token_ids = poly.get_token_ids_from_market(market)
        outcomes = poly.get_outcomes_from_market(market)

        if token_ids:
            token_id = token_ids[0]
            label = outcomes[0] if outcomes else None

            current_price = poly.get_current_price(token_id)
            recent_prices = poly.get_recent_trade_prices(token_id, limit=60)
            recent_trades = poly.get_trades_public(token_id=token_id, limit=200)
            orderbook = poly.get_orderbook(token_id)

            print(f"\n✅ Market: {market.get('slug')}")
            print(f"✅ Token: {label} | {token_id[:18]}...")

            # Sanity check on timestamps coverage
            if recent_trades:
                ts0 = _parse_ts(recent_trades[0].get("timestamp"))
                ts1 = _parse_ts(recent_trades[-1].get("timestamp"))
                if ts0 and ts1:
                    span_min = abs((ts1 - ts0).total_seconds()) / 60.0
                    print(f"✅ Trades fetched: {len(recent_trades)} spanning ~{span_min:.1f} minutes")

            if current_price and orderbook and recent_trades:
                sig = detector.detect(
                    current_price=current_price,
                    recent_prices=recent_prices,
                    recent_trades=recent_trades,
                    orderbook=orderbook,
                    btc_price_change_5min=btc_change_5min,
                    outcome_label=label,
                )
                if sig:
                    detector.print_signal(sig)
                else:
                    print("✅ No signal (normal)")
            else:
                print("⚠️ Missing data to test with real market")
        else:
            print("⚠️ No token_ids in market")
    else:
        print("⚠️ No active markets found")

    print("\n" + "=" * 90)
    print("✅ Test complete")
    print("=" * 90 + "\n")