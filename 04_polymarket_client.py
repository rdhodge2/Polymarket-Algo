"""
04 - Polymarket Client (Gamma + CLOB + Data API)

What this script does (reliably, without pulling thousands of markets):
- Uses Gamma API to find ACTIVE BTC/ETH 15-minute "Up or Down" markets by filtering on an end-date window
- Uses CLOB public endpoint to pull the orderbook (/book) for a given token_id
- Uses Data API (public) to pull recent trades (/trades) WITHOUT L2 auth
  (Your 401 was because CLOB /data/trades is an authenticated "my trades" endpoint)

Docs (for reference):
- Gamma markets: https://docs.polymarket.com/developers/gamma-markets-api/fetch-markets-guide
- Gamma get markets: https://docs.polymarket.com/developers/gamma-markets-api/get-markets
- Quickstart fetching data: https://docs.polymarket.com/quickstart/fetching-data
- CLOB trades (auth): https://docs.polymarket.com/developers/CLOB/trades/trades
- Data API trades (public): https://docs.polymarket.com/developers/CLOB/trades/trades-data-api
- Data API trades (api reference): https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets

NOTE about placing orders:
- Placing/canceling orders uses CLOB L1/L2 auth (not a simple Bearer token).
- This script keeps place_order/cancel_order as "stubs" unless you wire proper L2 headers.
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

# Endpoints
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"

# Optional env vars (only needed for authenticated trading, NOT needed for orderbook/public trades)
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY")
POLYMARKET_WALLET_ADDRESS = os.getenv("POLYMARKET_WALLET")


class PolymarketClient:
    def __init__(self):
        self.gamma_url = GAMMA_API_URL
        self.clob_url = CLOB_API_URL
        self.data_url = DATA_API_URL

        self.api_key = POLYMARKET_API_KEY
        self.wallet = POLYMARKET_WALLET_ADDRESS

        # Gamma is public
        self.gamma_session = requests.Session()

        # CLOB public endpoints (book/price/etc) are public
        self.clob_public_session = requests.Session()

        # Data API (public for market trades; some endpoints may require additional params)
        self.data_session = requests.Session()

        # Placeholder "private" session (NOT sufficient for real orders without proper L2 headers)
        self.clob_private_session = requests.Session()
        if self.api_key:
            # This Bearer header is NOT the standard L2 header scheme.
            # Keep it here only if you're experimenting; expect 401 for L2-required endpoints.
            self.clob_private_session.headers.update(
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
            )

        print("✅ [04] Polymarket client initialized")

    # -----------------------------
    # Helpers
    # -----------------------------
    def _parse_jsonish(self, v: Any) -> Any:
        """
        Gamma sometimes returns list-like fields as JSON strings, e.g. '["a","b"]'.
        This makes parsing robust either way.
        """
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
        # Gamma commonly returns endDateIso or endDate
        return market.get("endDateIso") or market.get("endDate") or market.get("end_date_iso")

    def _calculate_spread(self, best_bid: float, best_ask: float) -> float:
        """
        Calculate bid-ask spread as a percentage (returned as decimal)
        
        Formula: (Ask - Bid) / Mid Price
        
        Args:
            best_bid: Best bid price
            best_ask: Best ask price
        
        Returns:
            float: Spread as decimal (e.g., 0.10 = 10% spread)
        """
        # Handle None values
        if best_bid is None or best_ask is None:
            return 999.0  # Very high spread to filter out
        
        # Handle zero values
        if best_bid == 0 and best_ask == 0:
            return 999.0
        
        # Calculate mid price
        mid_price = (best_bid + best_ask) / 2.0
        
        # Avoid division by zero
        if mid_price == 0:
            return 999.0
        
        # Calculate spread as decimal
        # Example: bid=$0.45, ask=$0.55, mid=$0.50
        # spread = (0.55 - 0.45) / 0.50 = 0.20 (which is 20%)
        spread = (best_ask - best_bid) / mid_price
        
        return spread

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
        """
        Fetch markets from Gamma API with filters.
        """
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
            # API accepts slug array; requests will serialize list properly
            params["slug"] = [slug]

        try:
            resp = self.gamma_session.get(endpoint, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"❌ [04] Error fetching markets: {e}")
            return []

    def get_market_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        markets = self.get_markets(limit=1, offset=0, active=None, closed=None, archived=None, slug=slug)
        return markets[0] if markets else None

    def get_active_btc_eth_15m_updown_markets(
        self,
        window_minutes: int = 180,
        include_eth: bool = True,
        print_markets: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Finds active BTC/ETH 15-minute "Up or Down" markets by:
        - limiting Gamma query to markets ending soon (end_date_min/end_date_max)
        - filtering question/slug text locally
        """
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
                "15 minute" in t
                or "15-minute" in t
                or "15 min" in t
                or "15m" in t
                or "15m" in u
                or "15min" in u
                or "15-min" in u
            )

        def is_updown(q: str, s: str) -> bool:
            t = (q or "").lower()
            u = (s or "").lower()
            return ("up or down" in t) or ("updown" in u) or ("up-or-down" in u) or ("up or down" in u)

        def is_btc(q: str, s: str) -> bool:
            t = (q or "").lower()
            u = (s or "").lower()
            return ("bitcoin" in t) or u.startswith("btc") or (" btc" in t)

        def is_eth(q: str, s: str) -> bool:
            t = (q or "").lower()
            u = (s or "").lower()
            return ("ethereum" in t) or u.startswith("eth") or (" eth" in t)

        results: List[Dict[str, Any]] = []
        relaxed_results: List[Dict[str, Any]] = []
        for m in candidates:
            q = m.get("question", "")
            s = m.get("slug", "")

            if is_btc(q, s):
                pass
            elif include_eth and is_eth(q, s):
                pass
            else:
                continue

            if not looks_15m(q, s):
                continue

            if not is_updown(q, s):
                relaxed_results.append(m)
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
            print("⚠️  No active BTC/ETH 15-min Up/Down markets found in this window.")
            print("💡 Try increasing window_minutes, or loosen the 15m filter if the series is labeled differently.\n")
            if relaxed_results:
                print("⚠️  No strict Up/Down markets found; using relaxed 15m BTC/ETH set.\n")
                results = relaxed_results
                for m in results:
                    if print_markets:
                        self.print_market(m, now)
            else:
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
    # CLOB (public): Orderbook
    # -----------------------------
    def get_orderbook(self, token_id: str) -> Optional[Dict[str, Any]]:
        endpoint = f"{self.clob_url}/book"
        params = {"token_id": token_id}

        try:
            resp = self.clob_public_session.get(endpoint, params=params, timeout=15)
            resp.raise_for_status()
            book = resp.json()

            bids = book.get("bids", []) or []
            asks = book.get("asks", []) or []

            best_bid = float(bids[0]["price"]) if bids else None
            best_ask = float(asks[0]["price"]) if asks else None

            return {
                "timestamp": datetime.now(timezone.utc),
                "token_id": token_id,
                "bids": bids,
                "asks": asks,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": self._calculate_spread(best_bid, best_ask),
                "bid_depth": sum(float(b.get("size", 0)) for b in bids[:5]),
                "ask_depth": sum(float(a.get("size", 0)) for a in asks[:5]),
            }
        except Exception as e:
            print(f"❌ [04] Orderbook error: {e}")
            return None

    # -----------------------------
    # Data API (public): Trades
    # -----------------------------
    def get_trades_public(
        self,
        token_id: Optional[str] = None,
        condition_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Public trade history via Data API.
        You can filter by:
          - asset (token_id)
          - conditionId (market/condition id)
        """
        endpoint = f"{self.data_url}/trades"
        params: Dict[str, Any] = {"limit": int(limit)}

        # You can pass both; Data API will filter accordingly if supported.
        if condition_id:
            params["conditionId"] = condition_id
        if token_id:
            params["asset"] = token_id

        try:
            resp = self.data_session.get(endpoint, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            out: List[Dict[str, Any]] = []
            for t in data:
                out.append(
                    {
                        "timestamp": t.get("timestamp"),
                        "price": float(t["price"]) if t.get("price") is not None else None,
                        "size": float(t["size"]) if t.get("size") is not None else None,
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
    # Helper Methods for Trading Strategy
    # -----------------------------
    def get_current_price(self, token_id: str) -> Optional[float]:
        """
        Get current mid-price for a token (average of best bid/ask)
        Used by strategy to determine entry/exit prices
        """
        book = self.get_orderbook(token_id)
        if not book:
            return None
        
        best_bid = book.get('best_bid')
        best_ask = book.get('best_ask')
        
        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) / 2
        elif best_bid is not None:
            return best_bid
        elif best_ask is not None:
            return best_ask
        
        return None

    def get_recent_trade_prices(self, token_id: str, limit: int = 30) -> List[float]:
        """
        Get recent trade prices for calculating indicators (RSI, etc.)
        Returns: list of prices (oldest first, newest last)
        """
        trades = self.get_trades_public(token_id=token_id, limit=limit)
        
        if not trades:
            return []
        
        # Extract prices, filter out None values
        prices = [t['price'] for t in trades if t.get('price') is not None]
        
        # Reverse so oldest is first (for indicator calculations)
        return list(reversed(prices))

    def get_market_volume(self, token_id: str, limit: int = 50) -> float:
        """
        Calculate recent trading volume
        Returns: total volume in last N trades
        """
        trades = self.get_trades_public(token_id=token_id, limit=limit)
        
        if not trades:
            return 0.0
        
        total_volume = sum(t.get('size', 0) for t in trades if t.get('size') is not None)
        return total_volume

    def is_market_active(self, market: Dict[str, Any]) -> bool:
        """
        Check if a market is still active (not expired)
        """
        now = datetime.now(timezone.utc)
        end_iso = self._get_end_iso(market)
        
        if not end_iso:
            return True  # Assume active if no end date
        
        try:
            end_dt = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
            return end_dt > now
        except:
            return True

    def get_time_until_expiry(self, market: Dict[str, Any]) -> Optional[float]:
        """
        Get minutes until market expires
        Returns: minutes (float) or None if can't determine
        """
        now = datetime.now(timezone.utc)
        end_iso = self._get_end_iso(market)
        
        if not end_iso:
            return None
        
        try:
            end_dt = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
            seconds_left = (end_dt - now).total_seconds()
            return seconds_left / 60
        except:
            return None

    def get_token_ids_from_market(self, market: Dict[str, Any]) -> List[str]:
        """
        Extract token IDs from a market
        Returns: list of token IDs (usually [YES_token, NO_token])
        """
        clob_ids = self._parse_jsonish(market.get('clobTokenIds'))
        
        if isinstance(clob_ids, list):
            return clob_ids
        
        return []

    def get_outcomes_from_market(self, market: Dict[str, Any]) -> List[str]:
        """
        Extract outcome labels from a market
        Returns: list of outcome names (usually ['YES', 'NO'])
        """
        outcomes = self._parse_jsonish(market.get('outcomes'))
        
        if isinstance(outcomes, list):
            return outcomes
        
        return []

    # -----------------------------
    # CLOB (private): Orders (STUBS)
    # -----------------------------
    def place_order_stub(self, token_id: str, side: str, price: float, size: float) -> Optional[Dict[str, Any]]:
        """
        IMPORTANT:
        Real order placement requires proper CLOB L1/L2 auth headers (not Bearer).
        Leaving this as a stub so you don't keep hitting 401s unexpectedly.
        """
        print("⚠️  place_order_stub: Not implemented (requires proper CLOB L2 auth headers).")
        return None

    def cancel_order_stub(self, order_id: str) -> bool:
        print("⚠️  cancel_order_stub: Not implemented (requires proper CLOB L2 auth headers).")
        return False


print("✅ [04] Polymarket client loaded")


# -----------------------------
# Test Runner
# -----------------------------
if __name__ == "__main__":
    print("\n🧪 Testing [04] - Gamma + CLOB (book) + Data API (trades)\n" + "=" * 90)

    client = PolymarketClient()

    markets = client.get_active_btc_eth_15m_updown_markets(window_minutes=180, include_eth=True, print_markets=True)

    print("=" * 90)
    print(f"✅ RESULT: Found {len(markets)} active BTC/ETH 15-min Up/Down markets")
    print("=" * 90)

    if not markets:
        print("\n⏰ No matching markets right now.")
        print("   Try widening window_minutes or loosening the 15m detection.\n")
        print("✅ [04] Tests complete (no markets to test with)\n")
        raise SystemExit(0)

    # Pick first market
    m = markets[0]
    condition_id = m.get("conditionId") or m.get("condition_id")
    
    token_ids = client.get_token_ids_from_market(m)
    outcomes = client.get_outcomes_from_market(m)

    if not token_ids:
        print("⚠️ No token IDs on this market. Raw keys:")
        print(m.keys())
        raise SystemExit(0)

    # Pick first token (usually YES or UP)
    token_id = token_ids[0]
    label = outcomes[0] if outcomes else "Outcome 0"

    print(f"\n🎯 Selected market: {m.get('slug')}")
    print(f"   Token: {label}")
    print(f"   Token ID: {token_id}")
    print(f"   Condition ID: {condition_id}\n")

    # Test new helper methods
    print("="*90)
    print("Testing Helper Methods")
    print("="*90)
    
    # Current price
    current_price = client.get_current_price(token_id)
    if current_price:
        print(f"✅ Current Price: ${current_price:.4f}")
    
    # Recent trade prices
    recent_prices = client.get_recent_trade_prices(token_id, limit=10)
    if recent_prices:
        print(f"✅ Recent Prices (last 10): {[f'{p:.4f}' for p in recent_prices[-5:]]}")
    
    # Volume
    volume = client.get_market_volume(token_id, limit=50)
    print(f"✅ Recent Volume: {volume:.2f}")
    
    # Market active
    is_active = client.is_market_active(m)
    print(f"✅ Market Active: {is_active}")
    
    # Time until expiry
    time_left = client.get_time_until_expiry(m)
    if time_left:
        print(f"✅ Time Until Expiry: {time_left:.1f} minutes")
    
    print()

    # Orderbook
    book = client.get_orderbook(token_id)
    if book and book["best_bid"] is not None and book["best_ask"] is not None:
        print("✅ Orderbook:")
        print(f"   Best Bid: ${book['best_bid']:.4f}")
        print(f"   Best Ask: ${book['best_ask']:.4f}")
        print(f"   Spread: {book['spread']*100:.2f}%")
        print(f"   Bid Depth (top 5): ${book['bid_depth']:.2f}")
        print(f"   Ask Depth (top 5): ${book['ask_depth']:.2f}")
    else:
        print("⚠️  Orderbook unavailable or empty.")

    # Public trades (Data API)
    trades = client.get_trades_public(token_id=token_id, condition_id=condition_id, limit=10)
    if trades:
        print("\n✅ Recent public trades (Data API, up to 10):")
        for i, t in enumerate(trades[:10]):
            print(
                f"  {i+1}. ts={t.get('timestamp')} | side={t.get('side')} | size={t.get('size')} | "
                f"price={t.get('price')} | outcome={t.get('outcome')}"
            )
    else:
        print("\n⚠️  No trades returned from Data API for this filter.")

    print("\n✅ [04] Tests complete\n")