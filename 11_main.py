"""
13 - Main Trading Bot
The complete automated trading system

This is the orchestrator that brings together:
- 03: Alpaca (BTC prices)
- 04: Polymarket (market data)
- 05: Excel Logger (record keeping)
- 06: Regime Filter (safety gate)
- 07: Overreaction Detector (signal generator)
- 08: Position Sizer (money management)
- 09: Risk Manager (portfolio safety)
- 10: Exit Manager (exit timing)

Run this to start trading!
"""

import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from importlib import import_module

# Import all our components
try:
    alpaca_module = import_module('03_alpaca_client')
    poly_module = import_module('04_polymarket_client')
    logger_module = import_module('05_excel_logger')
    regime_module = import_module('06_regime_filter')
    detector_module = import_module('07_overreaction_detector')
    sizer_module = import_module('08_position_sizer')
    risk_module = import_module('09_risk_manager')
    exit_module = import_module('10_exit_manager')
    
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
    print("   Make sure all scripts 03-10 are in the same directory")
    sys.exit(1)


# Configuration
STARTING_BANKROLL = 250         # Starting with $1000
SCAN_INTERVAL_SECONDS = 30       # Check for new signals every 30s
EXIT_CHECK_INTERVAL_SECONDS = 10 # Check exits every 10s
DRY_RUN = True                   # Set False to trade real money
MARKET_WINDOW_MINUTES = 30       # Look for markets ending in next 30 min


class PolymarketTradingBot:
    """
    The complete automated trading system
    """
    
    def __init__(self, starting_bankroll: float, dry_run: bool = True):
        self.dry_run = dry_run
        
        print("\n" + "="*90)
        print("🤖 POLYMARKET 15-MINUTE TRADING BOT")
        print("="*90)
        
        if self.dry_run:
            print("⚠️  DRY RUN MODE - No real trades will be placed")
        else:
            print("💰 LIVE TRADING MODE - Real money at risk!")
        
        print("="*90 + "\n")
        
        # Initialize all components
        print("Initializing components...\n")
        
        self.alpaca = AlpacaClient()
        self.poly = PolymarketClient()
        self.logger = ExcelLogger(log_dir='logs')
        self.regime_filter = RegimeFilter()
        self.detector = OverreactionDetector()
        self.sizer = PositionSizer(bankroll=starting_bankroll)
        self.risk_mgr = RiskManager(starting_bankroll=starting_bankroll)
        self.exit_mgr = ExitManager()
        
        # State
        self.open_positions = []
        self.last_scan_time = None
        self.last_exit_check_time = None
        
        print("\n" + "="*90)
        print("✅ All components initialized - Ready to trade!")
        print("="*90 + "\n")
    
    def run(self):
        """
        Main trading loop
        """
        print(f"🚀 Starting trading loop at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        cycle_count = 0
        
        try:
            while True:
                cycle_count += 1
                current_time = datetime.now(timezone.utc)
                
                print(f"\n{'='*90}")
                print(f"📊 Cycle {cycle_count} - {current_time.strftime('%H:%M:%S')}")
                print(f"{'='*90}\n")
                
                # === STEP 1: Check Exits First (every 10s) ===
                if (self.last_exit_check_time is None or 
                    (current_time - self.last_exit_check_time).total_seconds() >= EXIT_CHECK_INTERVAL_SECONDS):
                    
                    self._check_exits()
                    self.last_exit_check_time = current_time
                
                # === STEP 2: Scan for New Signals (every 30s) ===
                if (self.last_scan_time is None or 
                    (current_time - self.last_scan_time).total_seconds() >= SCAN_INTERVAL_SECONDS):
                    
                    self._scan_for_signals()
                    self.last_scan_time = current_time
                
                # === STEP 3: Status Update ===
                self._print_status()
                
                # Wait before next cycle
                time.sleep(5)  # Check every 5 seconds
                
        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down gracefully...")
            self._shutdown()
        except Exception as e:
            print(f"\n\n❌ ERROR: {e}")
            self._shutdown()
    
    def _scan_for_signals(self):
        """
        Scan markets for trading opportunities
        """
        print("🔍 Scanning for trading opportunities...")
        
        # Get BTC prices for regime checks
        btc_prices = self.alpaca.get_price_series(timeframe='1Min', limit=60)
        
        if not btc_prices:
            print("⚠️  Could not get BTC prices, skipping scan")
            return
        
        # Calculate BTC change for overreaction detection
        btc_current = btc_prices[-1]
        btc_5min_ago = btc_prices[-5] if len(btc_prices) >= 5 else btc_prices[0]
        btc_change_5min = (btc_current - btc_5min_ago) / btc_5min_ago if btc_5min_ago > 0 else 0
        
        # Get active 15-min markets
        markets = self.poly.get_active_btc_eth_15m_updown_markets(
            window_minutes=MARKET_WINDOW_MINUTES,
            print_markets=False
        )
        
        if not markets:
            print("   No active 15-min markets found")
            return
        
        print(f"   Found {len(markets)} active markets\n")
        
        # Check each market
        signals_found = 0
        
        for market in markets:
            # Check if market is still active
            if not self.poly.is_market_active(market):
                continue
            
            # Get time until expiry
            time_left = self.poly.get_time_until_expiry(market)
            if time_left and time_left < 2:
                continue  # Skip if less than 2 minutes left
            
            # Get token IDs
            token_ids = self.poly.get_token_ids_from_market(market)
            outcomes = self.poly.get_outcomes_from_market(market)
            
            if not token_ids:
                continue
            
            # Check each token (YES and NO)
            for i, token_id in enumerate(token_ids):
                outcome = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
                
                signal = self._check_token_for_signal(
                    market=market,
                    token_id=token_id,
                    outcome=outcome,
                    btc_prices=btc_prices,
                    btc_change_5min=btc_change_5min
                )
                
                if signal:
                    signals_found += 1
                    self._execute_signal(signal)
        
        if signals_found == 0:
            print("   No signals detected this scan")
    
    def _check_token_for_signal(
        self,
        market: Dict[str, Any],
        token_id: str,
        outcome: str,
        btc_prices: List[float],
        btc_change_5min: float
    ) -> Optional[Dict[str, Any]]:
        """
        Check a specific token for trading signal
        """
        # Get orderbook
        orderbook = self.poly.get_orderbook(token_id)
        if not orderbook:
            return None
        
        # === STEP 1: Regime Filter ===
        regime = self.regime_filter.check_regime(btc_prices, orderbook)
        
        if not regime['regime_ok']:
            # Log skipped signal
            self.logger.log_signal({
                'market_slug': market.get('slug'),
                'market_question': market.get('question'),
                'token_id': token_id,
                'outcome': outcome,
                'signal_type': 'REGIME_FILTERED',
                'side': 'N/A',
                'traded': False,
                'regime_ok': False,
                'regime_score': regime['regime_score'],
                'skip_reason': regime['reason']
            })
            return None
        
        # === STEP 2: Get Market Data ===
        current_price = self.poly.get_current_price(token_id)
        recent_prices = self.poly.get_recent_trade_prices(token_id, limit=30)
        recent_trades = self.poly.get_trades_public(token_id=token_id, limit=50)
        
        if not current_price or not recent_prices:
            return None
        
        # === STEP 3: Overreaction Detector ===
        signal = self.detector.detect(
            current_price=current_price,
            recent_prices=recent_prices,
            recent_trades=recent_trades,
            orderbook=orderbook,
            btc_price_change_5min=btc_change_5min
        )
        
        if not signal:
            return None
        
        # Signal found! Add context
        signal['market'] = market
        signal['token_id'] = token_id
        signal['outcome'] = outcome
        signal['regime'] = regime
        signal['orderbook'] = orderbook
        signal['btc_prices'] = btc_prices
        
        return signal
    
    def _execute_signal(self, signal: Dict[str, Any]):
        """
        Execute a trading signal
        """
        market = signal['market']
        token_id = signal['token_id']
        
        print(f"\n🎯 SIGNAL DETECTED:")
        print(f"   Market: {market.get('slug')}")
        print(f"   Outcome: {signal['outcome']}")
        print(f"   Side: {signal['side']}")
        print(f"   Confidence: {signal['confidence']}/100")
        print(f"   Expected Edge: {signal['expected_edge']:.2%}")
        
        # === STEP 4: Position Sizing ===
        market_depth = signal['orderbook']['bid_depth'] + signal['orderbook']['ask_depth']
        
        sizing = self.sizer.calculate_size(
            edge=signal['expected_edge'],
            confidence=signal['confidence'] / 100,
            market_depth=market_depth,
            regime_score=signal['regime']['regime_score']
        )
        
        if not sizing['tradeable']:
            print(f"   ⏭️  Skipped: {sizing['reasoning']}")
            
            self.logger.log_signal({
                'market_slug': market.get('slug'),
                'market_question': market.get('question'),
                'token_id': token_id,
                'outcome': signal['outcome'],
                'signal_type': 'OVERREACTION',
                'side': signal['side'],
                'confidence': signal['confidence'],
                'regime_ok': True,
                'regime_score': signal['regime']['regime_score'],
                'overreaction_score': signal['score'],
                'traded': False,
                'skip_reason': sizing['reasoning']
            })
            return
        
        print(f"   Position Size: ${sizing['final_size']:.2f}")
        
        # === STEP 5: Risk Manager ===
        risk_check = self.risk_mgr.can_open_position(sizing['final_size'])
        
        if not risk_check['allowed']:
            print(f"   🚫 Blocked by risk manager: {risk_check['reason']}")
            
            self.logger.log_signal({
                'market_slug': market.get('slug'),
                'market_question': market.get('question'),
                'token_id': token_id,
                'outcome': signal['outcome'],
                'signal_type': 'OVERREACTION',
                'side': signal['side'],
                'confidence': signal['confidence'],
                'regime_ok': True,
                'regime_score': signal['regime']['regime_score'],
                'overreaction_score': signal['score'],
                'traded': False,
                'skip_reason': f"Risk: {risk_check['reason']}"
            })
            return
        
        # === STEP 6: Execute Trade ===
        if self.dry_run:
            print(f"   🧪 DRY RUN: Would place order")
            trade_executed = True
            entry_price = signal['current_price']
        else:
            # Place real order
            order = self.poly.place_order_stub(
                token_id=token_id,
                side=signal['side'],
                price=signal['current_price'],
                size=sizing['final_size']
            )
            
            if order:
                trade_executed = True
                entry_price = signal['current_price']
                print(f"   ✅ Order placed")
            else:
                trade_executed = False
                print(f"   ❌ Order failed")
        
        if trade_executed:
            # Record position
            position = {
                'token_id': token_id,
                'market_slug': market.get('slug'),
                'market_question': market.get('question'),
                'outcome': signal['outcome'],
                'side': signal['side'],
                'entry_price': entry_price,
                'entry_time': datetime.now(timezone.utc),
                'size': sizing['final_size'],
                'signal': signal
            }
            
            self.open_positions.append(position)
            self.risk_mgr.open_position(position)
            
            # Log signal
            self.logger.log_signal({
                'market_slug': market.get('slug'),
                'market_question': market.get('question'),
                'token_id': token_id,
                'outcome': signal['outcome'],
                'signal_type': 'OVERREACTION',
                'side': signal['side'],
                'confidence': signal['confidence'],
                'regime_ok': True,
                'regime_score': signal['regime']['regime_score'],
                'overreaction_score': signal['score'],
                'traded': True
            })
    
    def _check_exits(self):
        """
        Check all open positions for exit conditions
        """
        if not self.open_positions:
            return
        
        print(f"\n🔍 Checking {len(self.open_positions)} open positions for exits...")
        
        # Get current BTC prices for regime check
        btc_prices = self.alpaca.get_price_series(timeframe='1Min', limit=15)
        
        if btc_prices and len(btc_prices) >= 15:
            from importlib import import_module
            indicators = import_module('02_indicators')
            btc_atr = indicators.calculate_atr(btc_prices, period=15)
        else:
            btc_atr = None
        
        # Check each position
        positions_to_close = []
        
        for position in self.open_positions:
            token_id = position['token_id']
            
            # Get current price
            current_price = self.poly.get_current_price(token_id)
            
            if current_price is None:
                print(f"   ⚠️  Could not get price for {token_id[:20]}...")
                continue
            
            # Check exit
            exit_check = self.exit_mgr.check_exit(
                position=position,
                current_price=current_price,
                current_time=datetime.now(timezone.utc),
                btc_atr=btc_atr
            )
            
            if exit_check['should_exit']:
                positions_to_close.append({
                    'position': position,
                    'exit_check': exit_check,
                    'current_price': current_price
                })
        
        # Close positions
        for item in positions_to_close:
            self._close_position(item['position'], item['exit_check'], item['current_price'])
    
    def _close_position(self, position: Dict[str, Any], exit_check: Dict[str, Any], exit_price: float):
        """
        Close a position
        """
        print(f"\n   🚪 CLOSING POSITION:")
        print(f"      Reason: {exit_check['reason']}")
        print(f"      Entry: ${position['entry_price']:.4f} → Exit: ${exit_price:.4f}")
        print(f"      PnL: ${exit_check['pnl']:+.2f} ({exit_check['pnl_pct']:+.2%})")
        
        # Remove from open positions
        self.open_positions = [p for p in self.open_positions if p['token_id'] != position['token_id']]
        
        # Update risk manager
        self.risk_mgr.close_position(position['token_id'], exit_check['pnl'])
        
        # Update position sizer bankroll
        self.sizer.update_bankroll(self.risk_mgr.current_bankroll)
        
        # Log trade
        self.logger.log_trade({
            'entry_time': position['entry_time'],
            'exit_time': datetime.now(timezone.utc),
            'market_slug': position['market_slug'],
            'market_question': position['market_question'],
            'token_id': position['token_id'],
            'outcome': position['outcome'],
            'side': position['side'],
            'entry_price': position['entry_price'],
            'exit_price': exit_price,
            'position_size': position['size'],
            'exit_reason': exit_check['reason'],
            'regime_score': position.get('signal', {}).get('regime', {}).get('regime_score'),
            'overreaction_score': position.get('signal', {}).get('score'),
            'notes': f"Priority {exit_check['priority']}"
        })
        
        # Update daily performance
        self.logger.update_daily_performance()
    
    def _print_status(self):
        """
        Print current bot status
        """
        status = self.risk_mgr.get_status()
        
        print(f"\n📊 Status:")
        print(f"   Open Positions: {len(self.open_positions)}/{status['max_positions']}")
        print(f"   Bankroll: ${status['current_bankroll']:,.2f}")
        print(f"   Today PnL: ${status['today_pnl']:+.2f} ({status['today_pnl_pct']:+.2f}%)")
        
        if self.open_positions:
            print(f"\n   Open Positions:")
            for pos in self.open_positions:
                current_price = self.poly.get_current_price(pos['token_id'])
                if current_price:
                    pnl_pct = (current_price - pos['entry_price']) / pos['entry_price'] if pos['entry_price'] > 0 else 0
                    print(f"      • {pos['outcome']}: ${pos['entry_price']:.4f} → ${current_price:.4f} ({pnl_pct:+.2%})")
    
    def _shutdown(self):
        """
        Graceful shutdown
        """
        print("\n" + "="*90)
        print("📊 Final Status")
        print("="*90)
        
        self.risk_mgr.print_status()
        
        if self.open_positions:
            print(f"⚠️  {len(self.open_positions)} positions still open")
            for pos in self.open_positions:
                print(f"   • {pos['market_slug']} - {pos['outcome']}")
        
        print("\n✅ Shutdown complete")
        print("="*90 + "\n")


# =============================
# Main Entry Point
# =============================
if __name__ == "__main__":
    print("\n" + "="*90)
    print("🚀 POLYMARKET 15-MINUTE TRADING BOT")
    print("="*90)
    
    print("\nConfiguration:")
    print(f"   Starting Bankroll: ${STARTING_BANKROLL:,.2f}")
    print(f"   Scan Interval: {SCAN_INTERVAL_SECONDS}s")
    print(f"   Exit Check Interval: {EXIT_CHECK_INTERVAL_SECONDS}s")
    print(f"   Market Window: {MARKET_WINDOW_MINUTES} minutes")
    print(f"   Dry Run: {DRY_RUN}")
    
    if not DRY_RUN:
        print("\n⚠️  WARNING: LIVE TRADING MODE")
        print("   Real money will be at risk!")
        response = input("\n   Type 'CONFIRM' to proceed: ")
        if response != 'CONFIRM':
            print("\n❌ Aborted")
            sys.exit(0)
    
    print("\n" + "="*90 + "\n")
    
    # Initialize and run bot
    bot = PolymarketTradingBot(
        starting_bankroll=STARTING_BANKROLL,
        dry_run=DRY_RUN
    )
    
    bot.run()