"""
04 - Polymarket Client (Gamma + CLOB + Data API)  ✅ DROP-IN REPLACEMENT

Fixes common "static book" / wrong best-bid-ask issues:
- Defensive sorting of bids/asks (API may not return sorted arrays)
- Cache-busting + no-cache headers for /book
- Captures response headers for debugging (Age/ETag/Cache)
- Helpers to debug top-of-book and verify YES/NO (UP/DOWN) complementarity

Endpoints:
- Gamma markets: https://gamma-api.polymarket.com
- CLOB orderbook: https://clob.polymarket.com/book
- Data API trades: https://data-api.polymarket.com/trades
"""

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Endpoints
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"

POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY")
POLYMARKET_WALLET_ADDRESS = os.getenv("POLYMARKET_WALLET")


def _safe_float(x) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


class PolymarketClient:
    def __init__(self, debug: bool = False):
        self.gamma_url = GAMMA_API_URL
        self.clob_url = CLOB_API_URL
        self.data_url = DATA_API_URL

        self.api_key = POLYMARKET_API_KEY
        self.wallet = POLYMARKET_WALLET_ADDRESS

        self.debug = bool(debug)

        self.gamma_session = requests.Session()
        self.clob_public_session = requests.Session()
        self.data_session = requests.Session()

        # Public endpoints: force "no-cache" behavior (best effort)
        self.clob_public_session.headers.update(
            {
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": "polymarket-bot/1.0",
            }
        )

        self.gamma_session.headers.update(
            {
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": "polymarket-bot/1.0",
            }
        )

        self.data_session.headers.update(
            {
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": "polymarket-bot/1.0",
            }
        )

        print("✅ [04] Polymarket client initialized")

    # -----------------------------
    # Helpers
    # -----------------------------
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

    def _get_end_iso(self, market: Dict[str, Any]) -> Optional[str]:
        return market.get("endDateIso") or market.get("endDate") or market.get("end_date_iso")

    def _calculate_spread_rel(self, best_bid: Optional[float], best_ask: Optional[float]) -> float:
        if best_bid is None or best_ask is None:
            return 999.0
        if best_bid == 0 and best_ask == 0:
            return 999.0
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0:
            return 999.0
        return (best_ask - best_bid) / mid

    def _sort_book_levels(
        self, bids: List[Dict[str, Any]], asks: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Defensive sorting:
        - bids: highest price first
        - asks: lowest price first
        """
        def bid_key(lvl):
            return _safe_float(lvl.get("price")) if lvl else None

        def ask_key(lvl):
            return _safe_float(lvl.get("price")) if lvl else None

        bids_sorted = sorted(
            [b for b in (bids or []) if _safe_float(b.get("price")) is not None],
            key=lambda x: bid_key(x),
            reverse=True,
        )

        asks_sorted = sorted(
            [a for a in (asks or []) if _safe_float(a.get("price")) is not None],
            key=lambda x: ask_key(x),
            reverse=False,
        )

        return bids_sorted, asks_sorted

    # -----------------------------
    # Gamma: Markets
    # -----------------------------
    def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: Optional[bool] = True,
        closed: Optional[bool] = False,
        archived: Optional[bool] = False,
        end_date_min: Optional[str] = None,
        end_date_max: Optional[str] = None,
        order: str = "endDate",
        ascending: bool = True,
        slug: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        endpoint = f"{self.gamma_url}/markets"
        params: Dict[str, Any] = {
            "limit": min(int(limit), 100),
            "offset": int(offset),
            "order": order,
            "ascending": bool(ascending),
        }

        if active is not None:
            params["active"] = bool(active)
        if closed is not None:
            params["closed"] = bool(closed)
        if archived is not None:
            params["archived"] = bool(archived)

        if end_date_min:
            params["end_date_min"] = end_date_min
        if end_date_max:
            params["end_date_max"] = end_date_max

        if slug:
            params["slug"] = [slug]

        try:
            resp = self.gamma_session.get(endpoint, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"❌ [04] Error fetching markets: {e}")
            return []

    def get_active_btc_eth_15m_updown_markets(
        self,
        window_minutes: int = 180,
        include_eth: bool = True,
        print_markets: bool = True,
    ) -> List[Dict[str, Any]]:
        print("🔍 [04] Searching for BTC/ETH 15-min Up/Down markets (windowed)...\n")

        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=10)
        end = now + timedelta(minutes=int(window_minutes))

        candidates = self.get_markets(
            limit=100,
            offset=0,
            active=True,
            closed=False,
            archived=False,
            end_date_min=start.isoformat(),
            end_date_max=end.isoformat(),
            order="endDate",
            ascending=True,
        )

        def looks_15m(q: str, s: str) -> bool:
            t = (q or "").lower()
            u = (s or "").lower()
            return (
                "15 minute" in t or "15-minute" in t or "15 min" in t or "15m" in t
                or "15m" in u or "15min" in u or "15-min" in u
            )

        def is_updown(q: str, s: str) -> bool:
            t = (q or "").lower()
            u = (s or "").lower()
            return ("up or down" in t) or ("updown" in u) or ("up-or-down" in u)

        def is_btc(q: str, s: str) -> bool:
            t = (q or "").lower()
            u = (s or "").lower()
            return ("bitcoin" in t) or u.startswith("btc") or (" btc" in t)

        def is_eth(q: str, s: str) -> bool:
            t = (q or "").lower()
            u = (s or "").lower()
            return ("ethereum" in t) or u.startswith("eth") or (" eth" in t)

        results: List[Dict[str, Any]] = []
        for m in candidates:
            q = m.get("question", "")
            s = m.get("slug", "")

            if not is_updown(q, s):
                continue

            if is_btc(q, s):
                pass
            elif include_eth and is_eth(q, s):
                pass
            else:
                continue

            if not looks_15m(q, s):
                continue

            end_iso = self._get_end_iso(m)
            if end_iso:
                try:
                    end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
                    if end_dt <= now:
                        continue
                except Exception:
                    pass

            results.append(m)
            if print_markets:
                self.print_market(m, now)

        if print_markets and not results:
            print("⚠️  No active BTC/ETH 15-min Up/Down markets found in this window.\n")

        return results

    def print_market(self, market: Dict[str, Any], now_utc: Optional[datetime] = None) -> None:
        now_utc = now_utc or datetime.now(timezone.utc)

        q = market.get("question", "Unknown")
        slug = market.get("slug", "N/A")
        market_id = market.get("id", "N/A")
        condition_id = market.get("conditionId") or market.get("condition_id") or "N/A"

        print(f"✓ {q}")
        print(f"  Slug: {slug}")
        print(f"  Market ID: {market_id}")
        print(f"  Condition ID: {condition_id}")

        end_iso = self._get_end_iso(market)
        if end_iso:
            try:
                end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
                mins_left = (end_dt - now_utc).total_seconds() / 60
                print(f"  Ends: {end_dt.strftime('%H:%M UTC')} ({mins_left:.1f} min left)")
            except Exception:
                print(f"  End: {end_iso}")

        clob_ids = self._parse_jsonish(market.get("clobTokenIds"))
        outcomes = self._parse_jsonish(market.get("outcomes"))

        if isinstance(clob_ids, list) and clob_ids:
            print("  CLOB Token IDs:")
            for i, tid in enumerate(clob_ids):
                label = outcomes[i] if isinstance(outcomes, list) and i < len(outcomes) else f"Outcome {i}"
                print(f"    - {label}: {tid}")
        print()

    # -----------------------------
    # Market token/outcome extraction
    # -----------------------------
    def get_token_ids_from_market(self, market: Dict[str, Any]) -> List[str]:
        clob_ids = self._parse_jsonish(market.get("clobTokenIds"))
        if isinstance(clob_ids, list):
            return [str(x) for x in clob_ids if x]
        return []

    def get_outcomes_from_market(self, market: Dict[str, Any]) -> List[str]:
        outcomes = self._parse_jsonish(market.get("outcomes"))
        if isinstance(outcomes, list):
            return [str(x) for x in outcomes if x is not None]
        return []

    # -----------------------------
    # CLOB (public): Orderbook  ✅ FIXED
    # -----------------------------
    def get_orderbook(self, token_id: str, top_n_depth: int = 5) -> Optional[Dict[str, Any]]:
        """
        Fetches orderbook and returns a normalized structure:
        - bids/asks sorted defensively
        - best_bid/best_ask computed from sorted arrays
        - mid/spread_abs/spread_rel computed safely
        - includes response headers for debugging (cache clues)
        """
        endpoint = f"{self.clob_url}/book"

        # cache buster prevents some CDNs from serving stale book responses
        cache_buster = str(int(time.time() * 1000))
        params = {"token_id": token_id, "_cb": cache_buster}

        t0 = time.time()
        try:
            resp = self.clob_public_session.get(endpoint, params=params, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
            elapsed_ms = (time.time() - t0) * 1000.0

            bids = raw.get("bids") or []
            asks = raw.get("asks") or []

            bids_sorted, asks_sorted = self._sort_book_levels(bids, asks)

            best_bid = _safe_float(bids_sorted[0].get("price")) if bids_sorted else None
            best_ask = _safe_float(asks_sorted[0].get("price")) if asks_sorted else None

            crossed = (best_bid is not None and best_ask is not None and best_bid > best_ask)

            mid = None
            spread_abs = None
            spread_rel = None
            if best_bid is not None and best_ask is not None and not crossed:
                mid = (best_bid + best_ask) / 2.0
                spread_abs = max(0.0, best_ask - best_bid)
                spread_rel = self._calculate_spread_rel(best_bid, best_ask)

            # Depth: top N sizes (not USD)
            bid_depth = sum(_safe_float(b.get("size")) or 0.0 for b in (bids_sorted[:top_n_depth] if bids_sorted else []))
            ask_depth = sum(_safe_float(a.get("size")) or 0.0 for a in (asks_sorted[:top_n_depth] if asks_sorted else []))

            # capture cache-related headers if present
            hdr = {k: v for k, v in resp.headers.items()}
            cache_headers = {
                "Age": hdr.get("Age"),
                "ETag": hdr.get("ETag"),
                "CF-Cache-Status": hdr.get("CF-Cache-Status") or hdr.get("Cf-Cache-Status"),
                "X-Cache": hdr.get("X-Cache"),
                "Via": hdr.get("Via"),
            }

            out = {
                "timestamp": datetime.now(timezone.utc),
                "token_id": token_id,
                "bids": bids_sorted,
                "asks": asks_sorted,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid": mid,
                "spread_abs": spread_abs,
                "spread_rel": spread_rel,
                "crossed": crossed,
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "http_ms": elapsed_ms,
                "cache_headers": cache_headers,
            }

            if self.debug:
                print(
                    f"      [DEBUG][04] /book token={str(token_id)[:18]}.. "
                    f"bid={best_bid} ask={best_ask} mid={mid} spread={spread_abs} crossed={crossed} "
                    f"ms={elapsed_ms:.0f} cache={cache_headers}"
                )

            return out

        except Exception as e:
            print(f"❌ [04] Orderbook error: {e}")
            return None

    def debug_orderbook(self, token_id: str) -> None:
        """
        Prints top 3 levels from the raw/sorted book and cache headers
        """
        book = self.get_orderbook(token_id, top_n_depth=5)
        if not book:
            print("⚠️ No book returned.")
            return

        bids = book.get("bids") or []
        asks = book.get("asks") or []

        def fmt_levels(levels):
            out = []
            for lvl in levels[:3]:
                out.append(f"{_safe_float(lvl.get('price')):.4f}@{_safe_float(lvl.get('size')) or 0:.2f}")
            return out

        print(f"🔎 token_id={token_id}")
        print(f"   best_bid={book.get('best_bid')} best_ask={book.get('best_ask')} mid={book.get('mid')} spread_abs={book.get('spread_abs')}")
        print(f"   bids_top3={fmt_levels(bids) if bids else []}")
        print(f"   asks_top3={fmt_levels(asks) if asks else []}")
        print(f"   cache_headers={book.get('cache_headers')}")
        print(f"   http_ms={book.get('http_ms'):.0f}ms\n")

    def check_complementarity(self, up_book: Dict[str, Any], down_book: Dict[str, Any]) -> Dict[str, Any]:
        """
        For binary complements, we expect:
          up_mid + down_mid ≈ 1
        """
        up_mid = up_book.get("mid")
        dn_mid = down_book.get("mid")
        if up_mid is None or dn_mid is None:
            return {"ok": False, "note": "missing mid(s)", "up_mid": up_mid, "down_mid": dn_mid}

        s = up_mid + dn_mid
        ok = abs(s - 1.0) <= 0.05  # loose tolerance (books can be wide)
        return {"ok": ok, "sum": s, "up_mid": up_mid, "down_mid": dn_mid}

    # -----------------------------
    # Data API (public): Trades
    # -----------------------------
    def get_trades_public(
        self,
        token_id: Optional[str] = None,
        condition_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        endpoint = f"{self.data_url}/trades"
        params: Dict[str, Any] = {"limit": int(limit)}

        if condition_id:
            params["conditionId"] = condition_id
        if token_id:
            params["asset"] = token_id

        try:
            resp = self.data_session.get(endpoint, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            out: List[Dict[str, Any]] = []
            if isinstance(data, list):
                for t in data:
                    out.append(
                        {
                            "timestamp": t.get("timestamp"),
                            "price": _safe_float(t.get("price")),
                            "size": _safe_float(t.get("size")),
                            "side": t.get("side"),
                            "outcome": t.get("outcome"),
                            "conditionId": t.get("conditionId"),
                            "asset": t.get("asset"),
                            "title": t.get("title"),
                            "slug": t.get("slug"),
                        }
                    )
            return out
        except Exception as e:
            print(f"❌ [04] Public trades error (Data API): {e}")
            return []

    # -----------------------------
    # Strategy helpers
    # -----------------------------
    def get_current_price(self, token_id: str) -> Optional[float]:
        book = self.get_orderbook(token_id)
        if not book:
            return None
        mid = book.get("mid")
        if mid is not None:
            return mid
        # fallback
        if book.get("best_bid") is not None:
            return book["best_bid"]
        if book.get("best_ask") is not None:
            return book["best_ask"]
        return None

    def get_recent_trade_prices(self, token_id: str, limit: int = 30) -> List[float]:
        trades = self.get_trades_public(token_id=token_id, limit=limit)
        if not trades:
            return []
        prices = [t["price"] for t in trades if t.get("price") is not None]
        # IMPORTANT: Data API often returns newest-first; reverse to oldest-first for indicators
        return list(reversed(prices))


print("✅ [04] Polymarket client loaded")


if __name__ == "__main__":
    print("\n🧪 Testing [04] - Orderbook freshness + sorting\n" + "=" * 90)

    client = PolymarketClient(debug=True)

    markets = client.get_active_btc_eth_15m_updown_markets(window_minutes=45, include_eth=True, print_markets=False)
    if not markets:
        print("⚠️ No markets found.")
        raise SystemExit(0)

    m = markets[0]
    token_ids = client.get_token_ids_from_market(m)
    outcomes = client.get_outcomes_from_market(m)

    print(f"Market: {m.get('slug')}")
    for i, tid in enumerate(token_ids[:2]):
        label = outcomes[i] if i < len(outcomes) else f"Outcome{i}"
        print(f" - {label}: {tid}")

    if len(token_ids) >= 2:
        print("\n--- Debug orderbooks (top 3 levels) ---")
        client.debug_orderbook(token_ids[0])
        time.sleep(1)
        client.debug_orderbook(token_ids[0])
        time.sleep(1)
        client.debug_orderbook(token_ids[0])

        b1 = client.get_orderbook(token_ids[0])
        b2 = client.get_orderbook(token_ids[1])
        if b1 and b2:
            comp = client.check_complementarity(b1, b2)
            print(f"Complementarity check: {comp}")

    print("\n✅ Test complete\n")
