"""
11 - Main Trading Bot (Robust Signal Schema + YES/NO-safe Regime)

FULL DROP-IN REPLACEMENT for 11_main.py

Fixes:
- No more KeyError on signal['side'] (supports BOTH old + new detector schemas)
- Handles detector returning:
    * old:  {"side": "BUY"/"SELL", ...}
    * new:  {"action": "BUY"/"SELL" (or "BUY" only), "fade_direction": "...", "recommended_outcome": "...", ...}
- Avoids formatting None as float (mid/spread safety)
- Adds deterministic mapping for binary markets:
    * We ALWAYS "BUY" a token (since your PolymarketClient has order stubs)
    * If detector says "SELL" or "FADE_UP/FADE_DOWN", we map to which outcome token to BUY

Strategy:
- Scans CURRENT (1.5-14 min), NEXT (14-28 min), FUTURE (28-40 min)
- Adaptive spread threshold by time-to-expiry
- Uses RegimeFilter.check_regime_market (YES/NO-safe) when available
- Runs OverreactionDetector on the selected tradable token
- If signal triggers, sizes with PositionSizer, checks RiskManager, then logs + (dry-run) records a position

NOTE:
- This script does NOT place real orders unless you implement L2 auth in PolymarketClient.
"""

import sys
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from importlib import import_module

# =============================
# Import all components
# =============================
try:
    alpaca_module = import_module("03_alpaca_client")
    poly_module = import_module("04_polymarket_client")
    logger_module = import_module("05_excel_logger")
    regime_module = import_module("06_regime_filter")
    detector_module = import_module("07_overreaction_detector")
    sizer_module = import_module("08_position_sizer")
    risk_module = import_module("09_risk_manager")
    exit_module = import_module("10_exit_manager")

    AlpacaClient = alpaca_module.AlpacaClient
    PolymarketClient = poly_module.PolymarketClient
    ExcelLogger = logger_module.ExcelLogger
    RegimeFilter = regime_module.RegimeFilter
    OverreactionDetector = detector_module.OverreactionDetector
    PositionSizer = sizer_module.PositionSizer
    RiskManager = risk_module.RiskManager
    ExitManager = exit_module.ExitManager

except ImportError as e:
    print(f"❌ Error importing modules: {e}")
    print("   Make sure scripts 03-10 are in the same directory as 11_main.py")
    sys.exit(1)


# =============================
# Configuration
# =============================
STARTING_BANKROLL = 250
SCAN_INTERVAL_SECONDS = 30
EXIT_CHECK_INTERVAL_SECONDS = 10
DRY_RUN = True

# Market timing
MIN_TIME_BEFORE_EXPIRY = 1.5     # stop trading 90s before expiry
MAX_TIME_BEFORE_EXPIRY = 40
MARKET_LOOKUP_WINDOW = 45

CURRENT_MARKET_MAX = 14
NEXT_MARKET_MAX = 28
# 28-40 = FUTURE

# Adaptive spread thresholds (ABS cents)
SPREAD_THRESHOLD_CURRENT = 0.12
SPREAD_THRESHOLD_NEXT = 0.20
SPREAD_THRESHOLD_FUTURE = 0.30


# =============================
# Small helpers
# =============================
def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _fmt_price(v: Any, places: int = 4, default: str = "None") -> str:
    x = _safe_float(v, None)
    if x is None:
        return default
    return f"{x:.{places}f}"


def _extract_slug_unix_end_dt(slug: str) -> Optional[datetime]:
    """
    Format expected: btc-updown-15m-1767645900
    """
    try:
        ts = int(slug.split("-")[-1])
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None


def _resolve_signal_action(signal: Dict[str, Any]) -> str:
    """
    Supports:
      - old schema: signal['side']
      - new schema: signal['action']
    Defaults to BUY.
    """
    side = signal.get("side")
    action = signal.get("action")

    s = (side or action or "BUY")
    s = str(s).upper().strip()
    if s not in ("BUY", "SELL"):
        s = "BUY"
    return s


def _resolve_signal_confidence(signal: Dict[str, Any]) -> int:
    c = signal.get("confidence")
    try:
        if c is None:
            return 0
        return int(c)
    except Exception:
        return 0


def _resolve_expected_edge(signal: Dict[str, Any]) -> float:
    return float(signal.get("expected_edge", 0.0) or 0.0)


def _signal_recommended_outcome(signal: Dict[str, Any]) -> Optional[str]:
    """
    If your detector returns recommended_outcome, prefer it.
    Otherwise infer from fade_direction if present.
    Returns a label like "Up"/"Down"/"YES"/"NO" (case-insensitive).
    """
    ro = signal.get("recommended_outcome")
    if ro:
        return str(ro)

    fade = (signal.get("fade_direction") or "").upper().strip()
    # If token price moved UP and we fade it, we want the OPPOSITE outcome -> "Down"/"NO"
    if fade == "FADE_UP":
        return "Down"
    if fade == "FADE_DOWN":
        return "Up"
    return None


def _pick_token_id_for_outcome(
    market: Dict[str, Any],
    poly: Any,
    desired_label: Optional[str],
    fallback_token_id: str,
    fallback_label: Optional[str],
) -> str:
    """
    Given a market and a desired outcome label (Up/Down or YES/NO),
    return the token_id that corresponds to that label.
    Falls back to the currently selected token_id if no match.
    """
    if not desired_label:
        return fallback_token_id

    desired = desired_label.strip().lower()
    token_ids = poly.get_token_ids_from_market(market) or []
    outcomes = poly.get_outcomes_from_market(market) or []

    # Build label->token map
    for i, tid in enumerate(token_ids):
        label = outcomes[i] if i < len(outcomes) else ""
        if str(label).strip().lower() == desired:
            return tid

    # Heuristic for Up/Down when labels might differ (e.g. "Yes"/"No")
    # If desired is "down"/"no", and fallback_label is "up"/"yes", then pick the other token.
    if len(token_ids) >= 2 and fallback_label:
        fb = str(fallback_label).strip().lower()
        if (desired in ("down", "no") and fb in ("up", "yes")) or (desired in ("up", "yes") and fb in ("down", "no")):
            # pick other
            return token_ids[1] if token_ids[0] == fallback_token_id else token_ids[0]

    return fallback_token_id


# =============================
# Main Bot
# =============================
class PolymarketTradingBot:
    def __init__(self, starting_bankroll: float, dry_run: bool = True):
        self.dry_run = bool(dry_run)

        print("\n" + "=" * 90)
        print("🤖 POLYMARKET 15-MINUTE TRADING BOT")
        print("=" * 90)
        print("⚠️  DRY RUN MODE - No real trades will be placed" if self.dry_run else "💰 LIVE TRADING MODE - Real money at risk!")
        print("=" * 90 + "\n")

        print("Initializing components...\n")
        self.alpaca = AlpacaClient()
        self.poly = PolymarketClient()
        self.logger = ExcelLogger(log_dir="logs")
        self.regime_filter = RegimeFilter()
        self.detector = OverreactionDetector()
        self.sizer = PositionSizer(bankroll=starting_bankroll)
        self.risk_mgr = RiskManager(starting_bankroll=starting_bankroll)
        self.exit_mgr = ExitManager()

        # State
        self.open_positions: List[Dict[str, Any]] = []
        self.last_scan_time: Optional[datetime] = None
        self.last_exit_check_time: Optional[datetime] = None

        print("\n" + "=" * 90)
        print("✅ All components initialized - Ready to run!")
        print("=" * 90 + "\n")

    def run(self):
        print(f"🚀 Starting trading loop at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🎯 Scanning window: {MIN_TIME_BEFORE_EXPIRY:.1f}-{MAX_TIME_BEFORE_EXPIRY:.1f} minutes")
        print(f"   - CURRENT markets (1.5-14 min): {int(SPREAD_THRESHOLD_CURRENT*100)}¢ max spread")
        print(f"   - NEXT markets (14-28 min):     {int(SPREAD_THRESHOLD_NEXT*100)}¢ max spread")
        print(f"   - FUTURE markets (28-40 min):   {int(SPREAD_THRESHOLD_FUTURE*100)}¢ max spread\n")

        cycle_count = 0

        try:
            while True:
                cycle_count += 1
                current_time = datetime.now(timezone.utc)

                print(f"\n{'=' * 90}")
                print(f"📊 Cycle {cycle_count} - {current_time.strftime('%H:%M:%S')} UTC")
                print(f"{'=' * 90}\n")

                # Exits
                if self.last_exit_check_time is None or (current_time - self.last_exit_check_time).total_seconds() >= EXIT_CHECK_INTERVAL_SECONDS:
                    self._check_exits()
                    self.last_exit_check_time = current_time

                # Scan
                if self.last_scan_time is None or (current_time - self.last_scan_time).total_seconds() >= SCAN_INTERVAL_SECONDS:
                    self._scan_for_signals()
                    self.last_scan_time = current_time

                # Status
                self._print_status()

                time.sleep(5)

        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down gracefully...")
            self._shutdown()
        except Exception as e:
            print(f"\n\n❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            self._shutdown()

    def _scan_for_signals(self):
        print("🔍 Scanning for trading opportunities...")

        btc_prices = self.alpaca.get_price_series(timeframe="1Min", limit=60)
        if not btc_prices:
            print("⚠️  Could not get BTC prices, skipping scan")
            return

        btc_current = btc_prices[-1]
        btc_5min_ago = btc_prices[-5] if len(btc_prices) >= 5 else btc_prices[0]
        btc_change_5min = (btc_current - btc_5min_ago) / btc_5min_ago if btc_5min_ago > 0 else 0.0
        print(f"   BTC: ${btc_current:,.2f} ({btc_change_5min:+.2%} over 5min)")

        all_markets = self.poly.get_active_btc_eth_15m_updown_markets(
            window_minutes=MARKET_LOOKUP_WINDOW,
            print_markets=False
        )
        if not all_markets:
            print("   ⚠️  No active 15-min markets found")
            return

        now = datetime.now(timezone.utc)

        # Categorize
        current_markets: List[Dict[str, Any]] = []
        next_markets: List[Dict[str, Any]] = []
        future_markets: List[Dict[str, Any]] = []

        for m in all_markets:
            slug = m.get("slug", "")
            end_dt = _extract_slug_unix_end_dt(slug) or None

            if not end_dt:
                # fallback: use Gamma endDateIso if present
                end_iso = m.get("endDateIso") or m.get("endDate")
                if end_iso:
                    try:
                        end_dt = datetime.fromisoformat(str(end_iso).replace("Z", "+00:00"))
                        if end_dt.tzinfo is None:
                            end_dt = end_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        end_dt = None

            if not end_dt:
                continue

            minutes_left = (end_dt - now).total_seconds() / 60.0

            if minutes_left < MIN_TIME_BEFORE_EXPIRY:
                continue
            if minutes_left <= CURRENT_MARKET_MAX:
                current_markets.append(m)
            elif minutes_left <= NEXT_MARKET_MAX:
                next_markets.append(m)
            elif minutes_left <= MAX_TIME_BEFORE_EXPIRY:
                future_markets.append(m)

        tradeable_markets = current_markets + next_markets + future_markets
        print(f"\n   🎯 Checking {len(tradeable_markets)} markets for signals\n")

        if not tradeable_markets:
            print("   ❌ No markets in tradeable window")
            return

        signals_found = 0

        for market in tradeable_markets:
            slug = market.get("slug", "N/A")
            end_dt = _extract_slug_unix_end_dt(slug)
            minutes_left = (end_dt - now).total_seconds() / 60.0 if end_dt else None

            # classify + spread threshold
            if minutes_left is None:
                market_type = "UNKNOWN"
                spread_threshold = SPREAD_THRESHOLD_CURRENT
            elif minutes_left <= CURRENT_MARKET_MAX:
                market_type = "CURRENT"
                spread_threshold = SPREAD_THRESHOLD_CURRENT
            elif minutes_left <= NEXT_MARKET_MAX:
                market_type = "NEXT"
                spread_threshold = SPREAD_THRESHOLD_NEXT
            else:
                market_type = "FUTURE"
                spread_threshold = SPREAD_THRESHOLD_FUTURE

            original_max_spread = getattr(self.regime_filter, "max_spread_abs", SPREAD_THRESHOLD_CURRENT)
            self.regime_filter.max_spread_abs = spread_threshold

            print(f"   Checking {slug} [{market_type}, max spread: {int(spread_threshold*100)}¢]...")

            # Regime check (YES/NO-safe)
            try:
                regime = self.regime_filter.check_regime_market(btc_prices, self.poly, market)
            except Exception as e:
                # fallback to old method below
                print(f"      ⚠️  Regime market-check failed ({e}); falling back to old method")
                self.regime_filter.max_spread_abs = original_max_spread
                signal = self._check_market_old_method(market, btc_prices, btc_change_5min)
                if signal:
                    signals_found += 1
                    self._execute_signal(signal)
                continue

            # restore spread threshold
            self.regime_filter.max_spread_abs = original_max_spread

            # Debug selected token info without None formatting crashes
            selected_token = regime.get("selected_token") or {}
            sel_label = selected_token.get("label")
            sel_mid = selected_token.get("mid")
            if sel_label:
                print(f"      📊 Selected: {sel_label} (mid=${_fmt_price(sel_mid, 4)})")

            if not regime.get("regime_ok", False):
                reason = regime.get("reason") or "Regime failed"
                print(f"      ❌ Regime filtered: {reason}")

                self.logger.log_signal({
                    "market_slug": market.get("slug"),
                    "market_question": market.get("question"),
                    "token_id": selected_token.get("token_id", "N/A"),
                    "outcome": selected_token.get("label", "N/A"),
                    "signal_type": "REGIME_FILTERED",
                    "side": "N/A",
                    "traded": False,
                    "regime_ok": False,
                    "regime_score": regime.get("regime_score", 0.0),
                    "skip_reason": reason,
                })
                continue

            print(f"      ✅ Regime passed (score: {regime.get('regime_score', 0.0):.1%})")

            token_id_selected = selected_token.get("token_id")
            outcome_selected = selected_token.get("label")

            if not token_id_selected:
                continue

            # Detect signal on selected token
            signal = self._check_token_for_signal(
                market=market,
                token_id=token_id_selected,
                outcome=str(outcome_selected) if outcome_selected else "Selected",
                btc_prices=btc_prices,
                btc_change_5min=btc_change_5min,
                regime_passed=True
            )

            if not signal:
                print("      ⏭️  No overreaction signal")
                continue

            # If detector suggests different outcome, map to correct token id
            desired_outcome = _signal_recommended_outcome(signal)
            token_id_trade = _pick_token_id_for_outcome(
                market=market,
                poly=self.poly,
                desired_label=desired_outcome,
                fallback_token_id=token_id_selected,
                fallback_label=outcome_selected
            )
            signal["token_id"] = token_id_trade  # enforce the token we intend to trade
            signal["outcome"] = desired_outcome or outcome_selected or signal.get("outcome") or "Selected"

            signals_found += 1
            print("      🎯 SIGNAL DETECTED!")
            self._execute_signal(signal)

        print()
        if signals_found == 0:
            print("   No actionable signals detected this scan")
        else:
            print(f"   🎉 Found {signals_found} signal(s)")

    def _check_market_old_method(
        self,
        market: Dict[str, Any],
        btc_prices: List[float],
        btc_change_5min: float
    ) -> Optional[Dict[str, Any]]:
        token_ids = self.poly.get_token_ids_from_market(market)
        outcomes = self.poly.get_outcomes_from_market(market)
        if not token_ids:
            return None

        for i, token_id in enumerate(token_ids):
            outcome = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
            signal = self._check_token_for_signal(
                market=market,
                token_id=token_id,
                outcome=outcome,
                btc_prices=btc_prices,
                btc_change_5min=btc_change_5min,
                regime_passed=False
            )
            if signal:
                return signal
        return None

    def _check_token_for_signal(
        self,
        market: Dict[str, Any],
        token_id: str,
        outcome: str,
        btc_prices: List[float],
        btc_change_5min: float,
        regime_passed: bool = False
    ) -> Optional[Dict[str, Any]]:
        orderbook = self.poly.get_orderbook(token_id)
        if not orderbook:
            return None

        if not regime_passed:
            regime = self.regime_filter.check_regime(btc_prices, orderbook)
            if not regime.get("regime_ok", False):
                self.logger.log_signal({
                    "market_slug": market.get("slug"),
                    "market_question": market.get("question"),
                    "token_id": token_id,
                    "outcome": outcome,
                    "signal_type": "REGIME_FILTERED",
                    "side": "N/A",
                    "traded": False,
                    "regime_ok": False,
                    "regime_score": regime.get("regime_score", 0.0),
                    "skip_reason": regime.get("reason"),
                })
                return None
        else:
            regime = {"regime_ok": True, "regime_score": 1.0}

        current_price = self.poly.get_current_price(token_id)
        recent_prices = self.poly.get_recent_trade_prices(token_id, limit=30)
        recent_trades = self.poly.get_trades_public(token_id=token_id, limit=50)

        if current_price is None or not recent_prices:
            return None

        sig = self.detector.detect(
            current_price=float(current_price),
            recent_prices=[float(x) for x in recent_prices if x is not None],
            recent_trades=recent_trades or [],
            orderbook=orderbook,
            btc_price_change_5min=float(btc_change_5min),
        )
        if not sig:
            return None

        # Normalize signal to always include keys that 11_main uses downstream
        sig["market"] = market
        sig["token_id"] = token_id
        sig["outcome"] = outcome
        sig["regime"] = regime
        sig["orderbook"] = orderbook
        sig["btc_prices"] = btc_prices

        # Backward/forward compat:
        # ensure 'side' exists if only 'action' exists
        if "side" not in sig and "action" in sig:
            sig["side"] = sig.get("action")

        # ensure confidence/expected_edge exist
        sig.setdefault("confidence", _resolve_signal_confidence(sig))
        sig.setdefault("expected_edge", _resolve_expected_edge(sig))
        sig.setdefault("current_price", current_price)

        return sig

    def _execute_signal(self, signal: Dict[str, Any]):
        market = signal.get("market", {})
        token_id = signal.get("token_id", "N/A")

        side = _resolve_signal_action(signal)  # ✅ supports side OR action
        confidence = _resolve_signal_confidence(signal)
        expected_edge = _resolve_expected_edge(signal)

        print("\n🎯 SIGNAL DETECTED:")
        print(f"   Market: {market.get('slug')}")
        print(f"   Outcome(Token): {signal.get('outcome')}")
        print(f"   Side/Action: {side}")
        print(f"   Confidence: {confidence}/100")
        print(f"   Expected Edge: {expected_edge:.2%}")

        orderbook = signal.get("orderbook") or {}
        market_depth = float(orderbook.get("bid_depth", 0) or 0) + float(orderbook.get("ask_depth", 0) or 0)

        sizing = self.sizer.calculate_size(
            edge=expected_edge,
            confidence=confidence / 100.0 if confidence else 0.0,
            market_depth=market_depth,
            regime_score=float(signal.get("regime", {}).get("regime_score", 1.0) or 1.0),
        )

        if not sizing.get("tradeable", False):
            print(f"   ⏭️  Skipped: {sizing.get('reasoning')}")
            self.logger.log_signal({
                "market_slug": market.get("slug"),
                "market_question": market.get("question"),
                "token_id": token_id,
                "outcome": signal.get("outcome"),
                "signal_type": "OVERREACTION",
                "side": side,
                "confidence": confidence,
                "regime_ok": True,
                "regime_score": float(signal.get("regime", {}).get("regime_score", 1.0) or 1.0),
                "overreaction_score": signal.get("score"),
                "traded": False,
                "skip_reason": sizing.get("reasoning"),
            })
            return

        final_size = float(sizing.get("final_size", 0.0) or 0.0)
        print(f"   Position Size: ${final_size:.2f}")

        risk_check = self.risk_mgr.can_open_position(final_size)
        if not risk_check.get("allowed", False):
            print(f"   🚫 Blocked by risk manager: {risk_check.get('reason')}")
            self.logger.log_signal({
                "market_slug": market.get("slug"),
                "market_question": market.get("question"),
                "token_id": token_id,
                "outcome": signal.get("outcome"),
                "signal_type": "OVERREACTION",
                "side": side,
                "confidence": confidence,
                "regime_ok": True,
                "regime_score": float(signal.get("regime", {}).get("regime_score", 1.0) or 1.0),
                "overreaction_score": signal.get("score"),
                "traded": False,
                "skip_reason": f"Risk: {risk_check.get('reason')}",
            })
            return

        # Execute trade (stubbed)
        if self.dry_run:
            print("   🧪 DRY RUN: Would place order (BUY token)")
            trade_executed = True
            entry_price = float(signal.get("current_price", 0.0) or 0.0)
        else:
            # Real order placement needs L2 auth; keep stub
            order = self.poly.place_order_stub(
                token_id=token_id,
                side=side,
                price=float(signal.get("current_price", 0.0) or 0.0),
                size=final_size,
            )
            trade_executed = bool(order)
            entry_price = float(signal.get("current_price", 0.0) or 0.0)
            print("   ✅ Order placed" if trade_executed else "   ❌ Order failed")

        if not trade_executed:
            return

        position = {
            "token_id": token_id,
            "market_slug": market.get("slug"),
            "market_question": market.get("question"),
            "outcome": signal.get("outcome"),
            "side": side,
            "entry_price": entry_price,
            "entry_time": datetime.now(timezone.utc),
            "size": final_size,
            "signal": signal,
        }
        self.open_positions.append(position)
        self.risk_mgr.open_position(position)

        self.logger.log_signal({
            "market_slug": market.get("slug"),
            "market_question": market.get("question"),
            "token_id": token_id,
            "outcome": signal.get("outcome"),
            "signal_type": "OVERREACTION",
            "side": side,
            "confidence": confidence,
            "regime_ok": True,
            "regime_score": float(signal.get("regime", {}).get("regime_score", 1.0) or 1.0),
            "overreaction_score": signal.get("score"),
            "traded": True,
        })

    def _check_exits(self):
        if not self.open_positions:
            return

        print(f"🔍 Checking {len(self.open_positions)} open positions for exits...")

        btc_prices = self.alpaca.get_price_series(timeframe="1Min", limit=15)
        btc_atr = None
        try:
            if btc_prices and len(btc_prices) >= 15:
                indicators = import_module("02_indicators")
                btc_atr = indicators.calculate_atr(btc_prices, period=15)
        except Exception:
            btc_atr = None

        positions_to_close = []

        for position in list(self.open_positions):
            token_id = position["token_id"]
            current_price = self.poly.get_current_price(token_id)
            if current_price is None:
                print(f"   ⚠️  Could not get price for {str(token_id)[:20]}...")
                continue

            exit_check = self.exit_mgr.check_exit(
                position=position,
                current_price=float(current_price),
                current_time=datetime.now(timezone.utc),
                btc_atr=btc_atr
            )

            if exit_check.get("should_exit", False):
                positions_to_close.append((position, exit_check, float(current_price)))

        for position, exit_check, exit_price in positions_to_close:
            self._close_position(position, exit_check, exit_price)

    def _close_position(self, position: Dict[str, Any], exit_check: Dict[str, Any], exit_price: float):
        print("\n   🚪 CLOSING POSITION:")
        print(f"      Reason: {exit_check.get('reason')}")
        print(f"      Entry: ${position.get('entry_price', 0):.4f} → Exit: ${exit_price:.4f}")
        print(f"      PnL: ${exit_check.get('pnl', 0):+.2f} ({exit_check.get('pnl_pct', 0):+.2%})")

        self.open_positions = [p for p in self.open_positions if p["token_id"] != position["token_id"]]

        self.risk_mgr.close_position(position["token_id"], exit_check.get("pnl", 0.0))
        self.sizer.update_bankroll(self.risk_mgr.current_bankroll)

        self.logger.log_trade({
            "entry_time": position.get("entry_time"),
            "exit_time": datetime.now(timezone.utc),
            "market_slug": position.get("market_slug"),
            "market_question": position.get("market_question"),
            "token_id": position.get("token_id"),
            "outcome": position.get("outcome"),
            "side": position.get("side"),
            "entry_price": position.get("entry_price"),
            "exit_price": exit_price,
            "position_size": position.get("size"),
            "exit_reason": exit_check.get("reason"),
            "regime_score": position.get("signal", {}).get("regime", {}).get("regime_score"),
            "overreaction_score": position.get("signal", {}).get("score"),
            "notes": f"Priority {exit_check.get('priority')}",
        })

        self.logger.update_daily_performance()

    def _print_status(self):
        status = self.risk_mgr.get_status()

        print("\n📊 Status:")
        print(f"   Open Positions: {len(self.open_positions)}/{status.get('max_positions')}")
        print(f"   Bankroll: ${status.get('current_bankroll', 0):,.2f}")
        print(f"   Today PnL: ${status.get('today_pnl', 0):+.2f} ({status.get('today_pnl_pct', 0):+.2f}%)")

        if self.open_positions:
            print("\n   Open Positions:")
            for pos in self.open_positions:
                cp = self.poly.get_current_price(pos["token_id"])
                if cp is None:
                    continue
                ep = float(pos.get("entry_price", 0.0) or 0.0)
                pnl_pct = (float(cp) - ep) / ep if ep > 0 else 0.0
                print(f"      • {pos.get('outcome')}: ${ep:.4f} → ${float(cp):.4f} ({pnl_pct:+.2%})")

    def _shutdown(self):
        print("\n" + "=" * 90)
        print("📊 Final Status")
        print("=" * 90)

        self.risk_mgr.print_status()

        if self.open_positions:
            print(f"⚠️  {len(self.open_positions)} positions still open")
            for pos in self.open_positions:
                print(f"   • {pos.get('market_slug')} - {pos.get('outcome')}")

        print("\n✅ Shutdown complete")
        print("=" * 90 + "\n")


# =============================
# Main Entry Point
# =============================
if __name__ == "__main__":
    print("\n" + "=" * 90)
    print("🚀 POLYMARKET 15-MINUTE TRADING BOT")
    print("=" * 90)

    print("\nConfiguration:")
    print(f"   Starting Bankroll: ${STARTING_BANKROLL:,.2f}")
    print(f"   Scan Interval: {SCAN_INTERVAL_SECONDS}s")
    print(f"   Exit Check Interval: {EXIT_CHECK_INTERVAL_SECONDS}s")
    print(f"   Trading Window: {MIN_TIME_BEFORE_EXPIRY:.1f}-{MAX_TIME_BEFORE_EXPIRY:.1f} min before expiry")
    print("   Spread Thresholds:")
    print(f"      CURRENT markets (1.5-14 min):  {int(SPREAD_THRESHOLD_CURRENT*100)}¢ max")
    print(f"      NEXT markets (14-28 min):      {int(SPREAD_THRESHOLD_NEXT*100)}¢ max")
    print(f"      FUTURE markets (28-40 min):    {int(SPREAD_THRESHOLD_FUTURE*100)}¢ max")
    print(f"   Dry Run: {DRY_RUN}")

    if not DRY_RUN:
        print("\n⚠️  WARNING: LIVE TRADING MODE")
        print("   Real money will be at risk!")
        response = input("\n   Type 'CONFIRM' to proceed: ")
        if response != "CONFIRM":
            print("\n❌ Aborted")
            sys.exit(0)

    print("\n" + "=" * 90 + "\n")

    bot = PolymarketTradingBot(
        starting_bankroll=STARTING_BANKROLL,
        dry_run=DRY_RUN
    )
    bot.run()
