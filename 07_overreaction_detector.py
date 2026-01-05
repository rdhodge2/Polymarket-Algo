"""
07 - Overreaction Detector
Finds mean reversion opportunities by detecting when retail traders overreact

This is the CORE SIGNAL GENERATOR for our strategy.

How it works:
1. Detects sharp price moves (5%+ in 5 minutes)
2. Confirms it's retail panic (small trade sizes, volume spike)
3. Checks if BTC didn't actually move much (overreaction vs reality)
4. Verifies orderbook is thin on the moved side (exhaustion)
5. Checks RSI for extremes (overbought/oversold)

Scores signals 0-100, only trade if score >= 60
"""

import sys
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

# Import our indicators
from importlib import import_module
indicators = import_module('02_indicators')

# Thresholds (can be tuned later)
MIN_PRICE_CHANGE = 0.05          # 5% move triggers signal
VOLUME_SPIKE_MULTIPLIER = 2.0    # 2x normal volume
SMALL_TRADE_SIZE = 50            # $50 avg = retail
MIN_OVERREACTION_SCORE = 60      # Minimum score to trade
RSI_OVERSOLD = 30                # RSI below this = oversold
RSI_OVERBOUGHT = 70              # RSI above this = overbought


class OverreactionDetector:
    """
    Detect when Polymarket prices overreact to BTC moves
    Find mean reversion opportunities
    """
    
    def __init__(
        self,
        min_price_change: float = MIN_PRICE_CHANGE,
        volume_multiplier: float = VOLUME_SPIKE_MULTIPLIER,
        small_trade_size: float = SMALL_TRADE_SIZE,
        min_score: int = MIN_OVERREACTION_SCORE,
        rsi_oversold: int = RSI_OVERSOLD,
        rsi_overbought: int = RSI_OVERBOUGHT
    ):
        self.min_price_change = min_price_change
        self.volume_multiplier = volume_multiplier
        self.small_trade_size = small_trade_size
        self.min_score = min_score
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        
        print(f"✅ [07] Overreaction detector initialized")
        print(f"   Min Price Change: {self.min_price_change:.1%}")
        print(f"   Volume Multiplier: {self.volume_multiplier}x")
        print(f"   Min Score: {self.min_score}/100")
    
    def detect(
        self,
        current_price: float,
        recent_prices: List[float],
        recent_trades: List[Dict[str, Any]],
        orderbook: Dict[str, Any],
        btc_price_change_5min: float
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze market for overreaction signals
        
        Args:
            current_price: current token price (0.01 to 0.99)
            recent_prices: list of recent prices (for RSI calculation)
            recent_trades: list of recent trade dicts with 'size' field
            orderbook: orderbook dict with depths
            btc_price_change_5min: BTC % change in last 5 minutes
        
        Returns:
            dict with signal details or None if no signal
        """
        score = 0
        signals = {}
        side = None  # Will be 'BUY' (fade the move)
        
        # Need at least 10 recent prices for analysis
        if len(recent_prices) < 10:
            return None
        
        # ===== SIGNAL 1: Sharp Price Move =====
        # Compare current price to 5 minutes ago (or ~10 trades ago)
        if len(recent_prices) >= 10:
            price_5min_ago = recent_prices[-10] if len(recent_prices) >= 10 else recent_prices[0]
            price_change = (current_price - price_5min_ago) / price_5min_ago if price_5min_ago > 0 else 0
            
            if abs(price_change) > self.min_price_change:
                score += 30
                signals['sharp_move'] = {
                    'triggered': True,
                    'price_change': price_change,
                    'threshold': self.min_price_change
                }
                
                # Determine which side to trade (FADE the move)
                if price_change > 0:
                    side = 'BUY'  # Price went up, we buy the opposite (NO/DOWN)
                else:
                    side = 'SELL'  # Price went down, we sell (or buy YES/UP)
            else:
                signals['sharp_move'] = {
                    'triggered': False,
                    'price_change': price_change,
                    'threshold': self.min_price_change
                }
        
        # If no sharp move, no signal
        if score == 0:
            return None
        
        # ===== SIGNAL 2: Volume Spike Without BTC Confirmation =====
        if recent_trades and len(recent_trades) >= 20:
            # Recent volume (last 10 trades)
            recent_volume = sum([t.get('size', 0) for t in recent_trades[-10:]])
            
            # Historical average volume (per 10 trades)
            total_volume = sum([t.get('size', 0) for t in recent_trades])
            avg_volume_per_10 = (total_volume / len(recent_trades)) * 10
            
            volume_ratio = recent_volume / avg_volume_per_10 if avg_volume_per_10 > 0 else 1
            
            if volume_ratio > self.volume_multiplier:
                # Volume spiked, now check if BTC also moved
                if abs(btc_price_change_5min) < 0.002:  # BTC moved <0.2%
                    score += 25
                    signals['volume_spike'] = {
                        'triggered': True,
                        'volume_ratio': volume_ratio,
                        'btc_change': btc_price_change_5min,
                        'note': 'Volume spike without BTC move'
                    }
                else:
                    signals['volume_spike'] = {
                        'triggered': False,
                        'volume_ratio': volume_ratio,
                        'btc_change': btc_price_change_5min,
                        'note': 'BTC also moved, not pure overreaction'
                    }
            else:
                signals['volume_spike'] = {
                    'triggered': False,
                    'volume_ratio': volume_ratio
                }
        
        # ===== SIGNAL 3: Small Trade Sizes (Retail Panic) =====
        if recent_trades and len(recent_trades) >= 10:
            recent_10_trades = recent_trades[-10:]
            avg_trade_size = sum([t.get('size', 0) for t in recent_10_trades]) / len(recent_10_trades)
            
            if avg_trade_size < self.small_trade_size:
                score += 20
                signals['small_trades'] = {
                    'triggered': True,
                    'avg_size': avg_trade_size,
                    'threshold': self.small_trade_size
                }
            else:
                signals['small_trades'] = {
                    'triggered': False,
                    'avg_size': avg_trade_size,
                    'threshold': self.small_trade_size
                }
        
        # ===== SIGNAL 4: Orderbook Depth Thinning =====
        bid_depth = orderbook.get('bid_depth', 0)
        ask_depth = orderbook.get('ask_depth', 0)
        
        # Assume historical depth is roughly equal to current total depth
        # (In production, you'd track this over time)
        total_depth = bid_depth + ask_depth
        historical_avg_depth = total_depth / 2  # Rough estimate
        
        if side == 'BUY':
            # Price went up (people bought), check if ask depth is thin
            if ask_depth < historical_avg_depth * 0.7:  # 30% thinner than average
                score += 15
                signals['depth_thin'] = {
                    'triggered': True,
                    'side_depth': ask_depth,
                    'avg_depth': historical_avg_depth
                }
            else:
                signals['depth_thin'] = {'triggered': False}
        
        elif side == 'SELL':
            # Price went down (people sold), check if bid depth is thin
            if bid_depth < historical_avg_depth * 0.7:
                score += 15
                signals['depth_thin'] = {
                    'triggered': True,
                    'side_depth': bid_depth,
                    'avg_depth': historical_avg_depth
                }
            else:
                signals['depth_thin'] = {'triggered': False}
        
        # ===== SIGNAL 5: RSI Extreme =====
        if len(recent_prices) >= 14:
            rsi = indicators.calculate_rsi(recent_prices, period=14)
            
            if rsi > self.rsi_overbought or rsi < self.rsi_oversold:
                score += 10
                signals['rsi_extreme'] = {
                    'triggered': True,
                    'rsi': rsi,
                    'overbought': self.rsi_overbought,
                    'oversold': self.rsi_oversold
                }
            else:
                signals['rsi_extreme'] = {
                    'triggered': False,
                    'rsi': rsi
                }
        
        # ===== FINAL DECISION =====
        if score >= self.min_score and side:
            # Calculate expected edge based on score
            # Higher score = higher confidence = larger edge estimate
            expected_edge = (score / 100) * 0.08  # ~5% edge at score 60, ~8% at score 100
            
            return {
                'signal': True,
                'side': side,
                'confidence': min(score, 100),
                'score': score,
                'signals': signals,
                'expected_edge': expected_edge,
                'current_price': current_price,
                'price_change': signals.get('sharp_move', {}).get('price_change', 0),
                'timestamp': datetime.now(timezone.utc)
            }
        
        # Score too low or no side determined
        return None
    
    def print_signal(self, signal: Dict[str, Any]) -> None:
        """
        Print a nice summary of the signal
        """
        if not signal or not signal.get('signal'):
            print("❌ No signal detected")
            return
        
        print(f"\n🎯 OVERREACTION SIGNAL DETECTED!")
        print(f"   Side: {signal['side']}")
        print(f"   Confidence: {signal['confidence']}/100")
        print(f"   Score: {signal['score']}/100")
        print(f"   Expected Edge: {signal['expected_edge']:.2%}")
        print(f"   Current Price: ${signal['current_price']:.4f}")
        print(f"   Price Change: {signal['price_change']:+.2%}")
        
        print(f"\n   Signal Breakdown:")
        for signal_name, signal_data in signal['signals'].items():
            if signal_data.get('triggered'):
                print(f"   ✓ {signal_name.replace('_', ' ').title()}")
                
                # Show details
                if 'price_change' in signal_data:
                    print(f"      Price change: {signal_data['price_change']:+.2%}")
                if 'volume_ratio' in signal_data:
                    print(f"      Volume: {signal_data['volume_ratio']:.2f}x normal")
                if 'avg_size' in signal_data:
                    print(f"      Avg trade size: ${signal_data['avg_size']:.2f}")
                if 'rsi' in signal_data:
                    print(f"      RSI: {signal_data['rsi']:.1f}")
                if 'note' in signal_data:
                    print(f"      Note: {signal_data['note']}")
            else:
                print(f"   ✗ {signal_name.replace('_', ' ').title()}")
        
        print()


print("✅ [07] Overreaction detector loaded")


# =============================
# Test Runner
# =============================
if __name__ == "__main__":
    print("\n🧪 Testing [07] - Overreaction Detector\n" + "="*90)
    
    # Import clients for testing
    try:
        from importlib import import_module
        alpaca_client_module = import_module('03_alpaca_client')
        poly_client_module = import_module('04_polymarket_client')
        
        AlpacaClient = alpaca_client_module.AlpacaClient
        PolymarketClient = poly_client_module.PolymarketClient
    except ImportError as e:
        print(f"❌ Could not import clients: {e}")
        sys.exit(1)
    
    # Initialize
    detector = OverreactionDetector()
    alpaca = AlpacaClient()
    poly = PolymarketClient()
    
    print("\n" + "="*90)
    print("Test 1: Get real market data")
    print("="*90)
    
    # Get BTC prices
    btc_bars = alpaca.get_historical_bars(timeframe='1Min', limit=10)
    
    if btc_bars and len(btc_bars) >= 2:
        btc_current = btc_bars[-1]['close']
        btc_5min_ago = btc_bars[-5]['close'] if len(btc_bars) >= 5 else btc_bars[0]['close']
        btc_change_5min = (btc_current - btc_5min_ago) / btc_5min_ago
        
        print(f"✅ BTC Current: ${btc_current:,.2f}")
        print(f"   BTC 5min ago: ${btc_5min_ago:,.2f}")
        print(f"   BTC Change: {btc_change_5min:+.2%}")
    else:
        print("⚠️  Using dummy BTC data")
        btc_change_5min = 0.001  # 0.1% change
    
    # Try to get a real market
    markets = poly.get_active_btc_eth_15m_updown_markets(window_minutes=30, print_markets=False)
    
    if markets:
        market = markets[0]
        token_ids = poly.get_token_ids_from_market(market)
        
        if token_ids:
            token_id = token_ids[0]
            print(f"\n✅ Using market: {market.get('slug', 'N/A')}")
            
            # Get current price
            current_price = poly.get_current_price(token_id)
            
            # Get recent prices
            recent_prices = poly.get_recent_trade_prices(token_id, limit=30)
            
            # Get recent trades
            recent_trades = poly.get_trades_public(token_id=token_id, limit=50)
            
            # Get orderbook
            orderbook = poly.get_orderbook(token_id)
            
            if current_price and recent_prices and orderbook:
                print(f"✅ Current Price: ${current_price:.4f}")
                print(f"✅ Got {len(recent_prices)} recent prices")
                print(f"✅ Got {len(recent_trades)} recent trades")
                print(f"✅ Got orderbook")
                
                real_data = True
            else:
                print("⚠️  Missing some data, using dummy values")
                real_data = False
        else:
            real_data = False
    else:
        real_data = False
    
    # If no real data, create dummy data
    if not real_data:
        print("\n⚠️  No active markets, using simulated data for testing")
        current_price = 0.55  # Moved from 0.50 to 0.55 (10% jump)
        recent_prices = [0.48, 0.49, 0.50, 0.50, 0.51, 0.50, 0.50, 0.51, 0.52, 0.53, 0.54, 0.55]
        recent_trades = [{'size': 30} for _ in range(50)]  # Small trades
        orderbook = {'bid_depth': 500, 'ask_depth': 200}  # Thin asks
    
    print("\n" + "="*90)
    print("Test 2: Detect overreaction (simulated scenario)")
    print("="*90)
    
    # Create a clear overreaction scenario
    overreacted_price = 0.58  # Price jumped to 0.58 (from ~0.50)
    overreacted_prices = [0.48, 0.49, 0.50, 0.50, 0.51, 0.50, 0.50, 0.51, 0.52, 0.55, 0.56, 0.58]
    small_trades = [{'size': 25} for _ in range(50)]  # Lots of small trades
    thin_orderbook = {'bid_depth': 500, 'ask_depth': 150}  # Asks are thin
    
    print("Simulated conditions:")
    print(f"  Current price: ${overreacted_price:.4f} (was $0.50)")
    print(f"  Price change: +16%")
    print(f"  BTC change: {btc_change_5min:+.2%}")
    print(f"  Trade sizes: Small (~$25)")
    print(f"  Ask depth: Thin ($150)\n")
    
    signal = detector.detect(
        current_price=overreacted_price,
        recent_prices=overreacted_prices,
        recent_trades=small_trades,
        orderbook=thin_orderbook,
        btc_price_change_5min=btc_change_5min
    )
    
    if signal:
        detector.print_signal(signal)
    else:
        print("❌ No signal detected (score too low)")
    
    print("="*90)
    print("Test 3: No overreaction (stable market)")
    print("="*90)
    
    # Stable market - no signal expected
    stable_price = 0.50
    stable_prices = [0.50, 0.50, 0.51, 0.50, 0.50, 0.49, 0.50, 0.50, 0.51, 0.50, 0.50, 0.50]
    normal_trades = [{'size': 100} for _ in range(50)]
    balanced_orderbook = {'bid_depth': 500, 'ask_depth': 500}
    
    print("Simulated conditions:")
    print(f"  Current price: ${stable_price:.4f}")
    print(f"  Price change: 0%")
    print(f"  Trading: Normal\n")
    
    signal_stable = detector.detect(
        current_price=stable_price,
        recent_prices=stable_prices,
        recent_trades=normal_trades,
        orderbook=balanced_orderbook,
        btc_price_change_5min=0.0
    )
    
    if signal_stable:
        print("⚠️  UNEXPECTED: Signal detected in stable market!")
        detector.print_signal(signal_stable)
    else:
        print("✅ Correctly identified: No overreaction in stable market")
    
    print("\n" + "="*90)
    print("Test 4: Try with real market data (if available)")
    print("="*90)
    
    if real_data:
        print("Testing with real market data...\n")
        
        signal_real = detector.detect(
            current_price=current_price,
            recent_prices=recent_prices,
            recent_trades=recent_trades,
            orderbook=orderbook,
            btc_price_change_5min=btc_change_5min
        )
        
        if signal_real:
            print("🎯 REAL SIGNAL DETECTED!")
            detector.print_signal(signal_real)
        else:
            print("✅ No overreaction detected in current market conditions")
            print("   (This is normal - most of the time there's no signal)")
    else:
        print("⏭️  Skipped (no real market data available)")
    
    print("\n" + "="*90)
    print("✅ All tests complete!")
    print("="*90 + "\n")