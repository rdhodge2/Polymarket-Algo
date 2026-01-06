"""
06_regime_filter.py  (DROP-IN REPLACEMENT)
=========================================
Purpose:
- Determine if market conditions are safe for trading (regime filter).
- Fix the Polymarket spread / orderbook issues you hit:
    ✅ Prevent YES/NO orderbooks from being confused
    ✅ Fetch BOTH YES + NO books and choose a "tradable" token (mid closest to 0.50)
    ✅ Normalize price units (0..1 vs 0..100 cents)
    ✅ Use absolute spread (ask - bid) as primary liquidity metric
    ✅ Add "price zone" gate so near-resolved markets (0.01/0.99) are rejected for the RIGHT reason
    ✅ Provide strong debug prints for what token + market is being evaluated

Requirements:
- 02_indicators.py present
- 03_alpaca_client.py present (must implement AlpacaClient.get_price_series)
- 04_polymarket_client.py present (must implement PolymarketClient.get_active_btc_eth_15m_updown_markets and get_orderbook)
  NOTE: This script will work even if 04_polymarket_client doesn't have helper methods like get_token_ids_from_market.

Tuneables:
- MAX_SPREAD_ABS: 0.03 = 3 cents
- PRICE_ZONE: only trade when mid is between 0.10 and 0.90 (avoids near-resolution)
"""

import sys
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

from importlib import import_module
indicators = import_module("02_indicators")

# =========================
# THRESHOLDS (TUNE HERE)
# =========================
MAX_BTC_ATR = 0.015              # 1.5% ATR (volatility)
MAX_BB_WIDTH = 0.020             # 2% Bollinger Band width (trend)

# Liquidity measure for a 0..1 contract:
# 0.03 = 3 cents wide
MAX_SPREAD_ABS = 0.15

# Avoid trading near-resolved markets where price collapses to 0/1
MIN_MID_PRICE = 0.10
MAX_MID_PRICE = 0.90

# Depth balance (stampede detection)
MIN_ORDERBOOK_BALANCE = 0.40
MAX_ORDERBOOK_BALANCE = 0.60


class RegimeFilter:
    """
    Filter out high-volatility/trending markets and poor Polymarket microstructure.
    """

    def __init__(
        self,
        max_btc_atr: float = MAX_BTC_ATR,
        max_bb_width: float = MAX_BB_WIDTH,
        max_spread_abs: float = MAX_SPREAD_ABS,
        min_mid_price: float = MIN_MID_PRICE,
        max_mid_price: float = MAX_MID_PRICE,
        min_balance: float = MIN_ORDERBOOK_BALANCE,
        max_balance: float = MAX_ORDERBOOK_BALANCE,
        debug: bool = True,
    ):
        self.max_atr = float(max_btc_atr)
        self.max_bb_width = float(max_bb_width)
        self.max_spread_abs = float(max_spread_abs)
        self.min_mid_price = float(min_mid_price)
        self.max_mid_price = float(max_mid_price)
        self.min_balance = float(min_balance)
        self.max_balance = float(max_balance)
        self.debug = bool(debug)

        print("✅ [06] Regime filter initialized")
        print(f"   Max BTC ATR: {self.max_atr:.3f} ({self.max_atr*100:.1f}%)")
        print(f"   Max BB Width: {self.max_bb_width:.3f} ({self.max_bb_width*100:.1f}%)")
        print(f"   Max Spread (ABS): {self.max_spread_abs:.3f} (≈{self.max_spread_abs*100:.0f}¢)")
        print(f"   Price Zone: {self.min_mid_price:.2f}–{self.max_mid_price:.2f} mid")
        print(f"   Balance Zone: {self.min_balance:.2f}–{self.max_balance:.2f}")

    # =========================================================
    # Polymarket Book Helpers (YES/NO safe)
    # =========================================================
    def _parse_jsonish(self, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (list, dict)):
            return v
        if isinstance(v, str):
            s = v.strip()
            if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
                try:
                    return json.loads(s)
                except Exception:
                    return v
        return v

    def _normalize_price_0_1(self, p: Optional[float]) -> Optional[float]:
        """
        Normalize price to 0..1.
        Many endpoints return 0..1 already; if 0..100 treat as cents.
        """
        if p is None:
            return None
        try:
            p = float(p)
        except Exception:
            return None

        if 0.0 <= p <= 1.0:
            return p

        # cents
        if 1.0 < p <= 100.0:
            return p / 100.0

        # out of expected range; keep as-is but caller will likely reject
        return p

    def _best_bid_ask_from_book(self, book: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
        """
        Robust best bid/ask extraction:
        - prefer book['best_bid']/['best_ask'] if present
        - else read bids[0].price / asks[0].price
        """
        bid = book.get("best_bid")
        ask = book.get("best_ask")

        if bid is None:
            bids = book.get("bids") or []
            if bids:
                bid = bids[0].get("price")

        if ask is None:
            asks = book.get("asks") or []
            if asks:
                ask = asks[0].get("price")

        return self._normalize_price_0_1(bid), self._normalize_price_0_1(ask)

    def _compute_spreads(self, book: Dict[str, Any]) -> Dict[str, Any]:
        """
        Returns:
          spread_abs = ask - bid
          spread_rel = (ask - bid)/mid
          mid
          bid, ask (normalized)
        """
        bid, ask = self._best_bid_ask_from_book(book)
        if bid is None or ask is None:
            return {"spread_abs": None, "spread_rel": None, "mid": None, "bid": bid, "ask": ask}

        # invalid book
        if ask < bid:
            return {"spread_abs": None, "spread_rel": None, "mid": None, "bid": bid, "ask": ask}

        spread_abs = max(0.0, ask - bid)
        mid = (ask + bid) / 2.0
        spread_rel = (spread_abs / mid) if mid and mid > 0 else None

        return {"spread_abs": spread_abs, "spread_rel": spread_rel, "mid": mid, "bid": bid, "ask": ask}

    def _get_token_ids_and_outcomes(self, market: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        """
        Extract token IDs and outcome labels from a Gamma market dict safely.
        Supports:
        - clobTokenIds (list or JSON string)
        - outcomes (list or JSON string)
        """
        token_ids = (
            market.get("clobTokenIds")
            or market.get("clob_token_ids")
            or market.get("token_ids")
            or market.get("tokens")
        )
        token_ids = self._parse_jsonish(token_ids)
        if isinstance(token_ids, list) and token_ids and isinstance(token_ids[0], dict):
            # older shape (list of dicts)
            token_ids = [t.get("token_id") or t.get("tokenId") for t in token_ids]
        token_ids = [t for t in (token_ids or []) if t]

        outcomes = self._parse_jsonish(market.get("outcomes")) or []
        if not isinstance(outcomes, list):
            outcomes = []

        # Keep only first 2 for binary
        return token_ids[:2], outcomes[:2]

    def _get_book_for_token(self, poly_client, token_id: str) -> Optional[Dict[str, Any]]:
        """
        Calls poly_client.get_orderbook(token_id) and returns a book dict.
        """
        try:
            return poly_client.get_orderbook(token_id)
        except Exception as e:
            if self.debug:
                print(f"      [DEBUG] get_orderbook failed for token_id={token_id}: {e}")
            return None

    def _pick_tradable_outcome(
        self,
        poly_client,
        market: Dict[str, Any],
        target_mid: float = 0.50,
    ) -> Dict[str, Any]:
        """
        Fetch YES + NO books (separately) and select the outcome token whose mid is closest to 0.50.
        This prevents YES/NO confusion and avoids always taking token_ids[0].
        Returns dict:
          {
            selected: {token_id,label,book,mid,spread_abs,spread_rel,bid,ask,depths},
            other:    { ... } or None,
            market_slug, market_end
          }
        """
        token_ids, outcomes = self._get_token_ids_and_outcomes(market)
        if len(token_ids) < 2:
            return {"selected": None, "other": None, "market_slug": market.get("slug"), "market_end": market.get("endDateIso") or market.get("endDate")}

        candidates = []
        for i, tid in enumerate(token_ids):
            label = outcomes[i] if i < len(outcomes) else f"Outcome{i}"
            book = self._get_book_for_token(poly_client, tid)
            if not book:
                candidates.append({"token_id": tid, "label": label, "book": None, "mid": None, "sp": None})
                continue

            sp = self._compute_spreads(book)
            mid = sp["mid"]

            candidates.append({
                "token_id": tid,
                "label": label,
                "book": book,
                "mid": mid,
                "sp": sp,
                "bid_depth": float(book.get("bid_depth", 0) or 0),
                "ask_depth": float(book.get("ask_depth", 0) or 0),
            })

        # choose one with valid mid closest to target
        valid = [c for c in candidates if c["mid"] is not None]
        if not valid:
            # no valid books; just return first
            return {"selected": candidates[0], "other": candidates[1] if len(candidates) > 1 else None,
                    "market_slug": market.get("slug"),
                    "market_end": market.get("endDateIso") or market.get("endDate")}

        selected = min(valid, key=lambda c: abs(c["mid"] - target_mid))
        other = None
        for c in candidates:
            if c["token_id"] != selected["token_id"]:
                other = c
                break

        return {
            "selected": selected,
            "other": other,
            "market_slug": market.get("slug"),
            "market_end": market.get("endDateIso") or market.get("endDate"),
        }

    # =========================================================
    # Regime checks
    # =========================================================
    def check_regime(self, btc_prices: List[float], orderbook: Dict[str, Any]) -> Dict[str, Any]:
        """
        Backwards-compatible: accepts a single orderbook dict (already chosen token).
        If you use the new "check_regime_market()" below, it will choose token safely for you.
        """
        checks: Dict[str, Dict[str, Any]] = {}

        # ===== CHECK 1: BTC Volatility (ATR) =====
        if len(btc_prices) >= 15:
            atr = indicators.calculate_atr(btc_prices, period=15)
            checks["btc_volatility"] = {"value": atr, "threshold": self.max_atr, "pass": atr < self.max_atr, "name": "BTC Volatility (ATR)"}
        else:
            checks["btc_volatility"] = {"value": 0.0, "threshold": self.max_atr, "pass": True, "name": "BTC Volatility (ATR)", "note": "Insufficient data"}

        # ===== CHECK 2: BTC Trend (BB Width) =====
        if len(btc_prices) >= 20:
            bb_upper, bb_lower, bb_mid = indicators.calculate_bollinger_bands(btc_prices, period=20)
            bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0.0
            checks["btc_trend"] = {"value": bb_width, "threshold": self.max_bb_width, "pass": bb_width < self.max_bb_width, "name": "BTC Trend (BB Width)"}
        else:
            checks["btc_trend"] = {"value": 0.0, "threshold": self.max_bb_width, "pass": True, "name": "BTC Trend (BB Width)", "note": "Insufficient data"}

        # ===== CHECK 3: Price Zone (NEW) =====
        sp = self._compute_spreads(orderbook)
        mid = sp["mid"]
        price_zone_pass = (mid is not None) and (self.min_mid_price < mid < self.max_mid_price)
        checks["price_zone"] = {
            "value": mid if mid is not None else -1,
            "min_threshold": self.min_mid_price,
            "max_threshold": self.max_mid_price,
            "pass": price_zone_pass,
            "name": "Market Price Zone (mid)",
            "note": None if mid is not None else "Missing bid/ask -> can't compute mid",
        }

        # ===== CHECK 4: Spread (ABS) (FIXED) =====
        spread_abs = sp["spread_abs"]
        spread_pass = (spread_abs is not None) and (spread_abs < self.max_spread_abs)
        if self.debug:
            print(
                f"      [DEBUG] Spread ABS: {spread_abs if spread_abs is not None else 'None'} "
                f"(bid={sp['bid']}, ask={sp['ask']}, mid={mid}) vs max {self.max_spread_abs:.4f} → "
                f"{'PASS' if spread_pass else 'FAIL'}"
            )
        checks["spread_abs"] = {
            "value": spread_abs if spread_abs is not None else 999.0,
            "threshold": self.max_spread_abs,
            "pass": spread_pass,
            "name": "Polymarket Spread (ABS)",
            "note": None if spread_abs is not None else "Missing best bid/ask on book",
        }

        # ===== CHECK 5: Orderbook Balance =====
        bid_depth = float(orderbook.get("bid_depth", 0) or 0)
        ask_depth = float(orderbook.get("ask_depth", 0) or 0)
        total = bid_depth + ask_depth
        if total > 0:
            bid_ratio = bid_depth / total
            balance_pass = self.min_balance < bid_ratio < self.max_balance
            checks["orderbook_balance"] = {
                "value": bid_ratio,
                "min_threshold": self.min_balance,
                "max_threshold": self.max_balance,
                "pass": balance_pass,
                "name": "Orderbook Balance",
            }
        else:
            checks["orderbook_balance"] = {
                "value": 0.0,
                "min_threshold": self.min_balance,
                "max_threshold": self.max_balance,
                "pass": False,
                "name": "Orderbook Balance",
                "note": "No depth in book",
            }

        all_pass = all(c["pass"] for c in checks.values())
        num_passed = sum(1 for c in checks.values() if c["pass"])
        regime_score = num_passed / len(checks)

        reason = None
        if not all_pass:
            failed = [c["name"] for c in checks.values() if not c["pass"]]
            reason = f"Failed: {', '.join(failed)}"

        return {
            "regime_ok": all_pass,
            "regime_score": regime_score,
            "checks": checks,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc),
        }

    def check_regime_market(self, btc_prices: List[float], poly_client, market: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recommended entry point:
        - Fetches BOTH YES/NO books (separate token_ids)
        - Picks the outcome token with mid closest to 0.50 (tradable near open)
        - Runs regime checks on that selected token
        """
        picked = self._pick_tradable_outcome(poly_client, market, target_mid=0.50)
        selected = picked.get("selected")

        if self.debug:
            print(f"      [DEBUG] Market slug: {picked.get('market_slug')}")
            print(f"      [DEBUG] Market end : {picked.get('market_end')}")

        if not selected or not selected.get("book"):
            # fail safe
            if self.debug:
                print("      [DEBUG] Could not fetch valid books for YES/NO -> regime FAIL")
            dummy_book = {"bid_depth": 0, "ask_depth": 0}
            result = self.check_regime(btc_prices, dummy_book)
            result["reason"] = (result["reason"] or "") + " | No valid orderbook"
            result["selected_token"] = None
            return result

        book = selected["book"]
        # Ensure depth keys exist even if client didn't compute them
        if "bid_depth" not in book:
            bids = book.get("bids") or []
            book["bid_depth"] = sum(float(b.get("size", 0)) for b in bids[:5]) if bids else 0.0
        if "ask_depth" not in book:
            asks = book.get("asks") or []
            book["ask_depth"] = sum(float(a.get("size", 0)) for a in asks[:5]) if asks else 0.0

        if self.debug:
            sp = selected.get("sp") or self._compute_spreads(book)
            print(
                f"      [DEBUG] Selected token: {selected.get('label')} "
                f"token_id={selected.get('token_id')}"
            )
            print(
                f"      [DEBUG] Book: bid={sp.get('bid')} ask={sp.get('ask')} "
                f"mid={sp.get('mid')} spread_abs={sp.get('spread_abs')} spread_rel={sp.get('spread_rel')}"
            )
            other = picked.get("other")
            if other and other.get("book"):
                sp2 = other.get("sp") or self._compute_spreads(other["book"])
                print(
                    f"      [DEBUG] Other token: {other.get('label')} "
                    f"token_id={other.get('token_id')} mid={sp2.get('mid')}"
                )

        result = self.check_regime(btc_prices, book)
        result["selected_token"] = {
            "label": selected.get("label"),
            "token_id": selected.get("token_id"),
            "mid": selected.get("mid"),
        }
        result["market_slug"] = picked.get("market_slug")
        result["market_end"] = picked.get("market_end")
        return result

    def print_regime_status(self, regime_result: Dict[str, Any]) -> None:
        ok = regime_result["regime_ok"]
        score = regime_result["regime_score"]

        status_emoji = "✅" if ok else "❌"
        print(f"\n{status_emoji} Regime Status: {'SAFE TO TRADE' if ok else 'DO NOT TRADE'}")
        print(f"   Score: {score:.1%} ({int(score * len(regime_result['checks']))}/{len(regime_result['checks'])} checks passed)")
        if not ok:
            print(f"   Reason: {regime_result['reason']}")

        if "market_slug" in regime_result:
            print(f"   Market: {regime_result.get('market_slug')}  End: {regime_result.get('market_end')}")
        if "selected_token" in regime_result and regime_result["selected_token"]:
            st = regime_result["selected_token"]
            print(f"   Selected Token: {st.get('label')} mid={st.get('mid')}")

        print("\n   Individual Checks:")
        for _, c in regime_result["checks"].items():
            passed = c["pass"]
            emoji = "✓" if passed else "✗"
            name = c["name"]
            value = c["value"]

            if "min_threshold" in c:
                print(f"   {emoji} {name}: {value:.3f} (must be {c['min_threshold']:.2f}-{c['max_threshold']:.2f})")
            else:
                print(f"   {emoji} {name}: {value:.4f} (max: {c['threshold']:.4f})")

            if c.get("note"):
                print(f"      Note: {c['note']}")
        print()


print("✅ [06] Regime filter loaded (YES/NO-safe, spread+price-zone fix)")


# =============================
# Test Runner
# =============================
if __name__ == "__main__":
    print("\n🧪 Testing [06] - Regime Filter (YES/NO-safe)\n" + "=" * 90)

    # Import clients
    try:
        alpaca_client_module = import_module("03_alpaca_client")
        poly_client_module = import_module("04_polymarket_client")
        AlpacaClient = alpaca_client_module.AlpacaClient
        PolymarketClient = poly_client_module.PolymarketClient
    except ImportError as e:
        print(f"❌ Could not import clients: {e}")
        print("   Make sure 03_alpaca_client.py and 04_polymarket_client.py are in the same directory")
        sys.exit(1)

    filt = RegimeFilter(debug=True)
    alpaca = AlpacaClient()
    poly = PolymarketClient()

    # BTC prices
    btc_prices = alpaca.get_price_series(timeframe="1Min", limit=60)
    if not btc_prices:
        print("⚠️ BTC prices missing, using dummy")
        btc_prices = [95000 + i * 10 for i in range(60)]

    # Get target markets (best effort)
    try:
        markets = poly.get_active_btc_eth_15m_updown_markets(window_minutes=30, include_eth=True, print_markets=False)
    except TypeError:
        markets = poly.get_active_btc_eth_15m_updown_markets(window_minutes=30, print_markets=False)

    if not markets:
        print("⚠️ No active markets. Exiting.")
        sys.exit(0)

    market = markets[0]
    print(f"\n✅ Using market: {market.get('slug')}\n")

    # Run the YES/NO-safe regime check
    result = filt.check_regime_market(btc_prices, poly, market)
    filt.print_regime_status(result)

    print("\n" + "=" * 90)
    print("✅ Test complete")
    print("=" * 90 + "\n")
