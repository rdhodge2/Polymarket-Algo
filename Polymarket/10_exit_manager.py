"""
10 - Exit Manager
Monitors open positions and triggers exits

Exit conditions:
1. Stop loss hit (2% below entry)
2. Take profit hit (5% above entry)
3. Mean reversion occurred (price within 2% of 0.50)
4. Max time elapsed (12 minutes - exit before final rush)
5. Regime breaks (volatility spike)

Checks every position every cycle and returns exit signals
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta

# Exit thresholds
STOP_LOSS_PCT = 0.06             # 2% stop loss
TAKE_PROFIT_PCT = 0.04          # 5% take profit
MEAN_REVERSION_THRESHOLD = 0.04  # Within 2% of fair value (0.50)
MAX_HOLD_TIME_SECONDS = 480      # 12 minutes max hold
REGIME_BREAK_ATR = 0.035         # 2% ATR triggers exit


class ExitManager:
    """
    Monitor open positions and determine when to exit
    """
    
    def __init__(
        self,
        stop_loss_pct: float = STOP_LOSS_PCT,
        take_profit_pct: float = TAKE_PROFIT_PCT,
        mean_reversion_threshold: float = MEAN_REVERSION_THRESHOLD,
        max_hold_seconds: int = MAX_HOLD_TIME_SECONDS,
        regime_break_atr: float = REGIME_BREAK_ATR
    ):
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.mean_reversion_threshold = mean_reversion_threshold
        self.max_hold_seconds = max_hold_seconds
        self.regime_break_atr = regime_break_atr
        
        print(f"✅ [10] Exit manager initialized")
        print(f"   Stop Loss: {self.stop_loss_pct:.1%}")
        print(f"   Take Profit: {self.take_profit_pct:.1%}")
        print(f"   Max Hold Time: {self.max_hold_seconds}s ({self.max_hold_seconds/60:.0f} min)")
    
    def check_exit(
        self,
        position: Dict[str, Any],
        current_price: float,
        current_time: datetime,
        btc_atr: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Check if a position should be exited
        
        Args:
            position: dict with position details
                Required: entry_price, entry_time, side
            current_price: current market price
            current_time: current datetime
            btc_atr: optional BTC ATR for regime check
        
        Returns:
            dict with:
                - should_exit: bool
                - reason: str (why to exit)
                - priority: int (1=urgent, 2=normal, 3=optional)
                - pnl: float (estimated PnL)
                - pnl_pct: float (PnL percentage)
        """
        entry_price = position.get('entry_price')
        entry_time = position.get('entry_time')
        side = position.get('side', 'BUY')
        position_size = position.get('size', 0)
        
        # Calculate current PnL
        if side == 'BUY':
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
        else:  # SELL
            pnl_pct = (entry_price - current_price) / entry_price if entry_price > 0 else 0
        
        pnl = pnl_pct * position_size
        
        # Calculate hold time
        if isinstance(entry_time, datetime):
            hold_time_seconds = (current_time - entry_time).total_seconds()
        else:
            hold_time_seconds = 0
        
        # === EXIT CHECK 1: Stop Loss (PRIORITY 1 - URGENT) ===
        if pnl_pct <= -self.stop_loss_pct:
            return {
                'should_exit': True,
                'reason': f'STOP_LOSS (down {abs(pnl_pct):.1%})',
                'priority': 1,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'exit_price': current_price
            }
        
        # === EXIT CHECK 2: Take Profit (PRIORITY 1 - URGENT) ===
        if pnl_pct >= self.take_profit_pct:
            return {
                'should_exit': True,
                'reason': f'TAKE_PROFIT (up {pnl_pct:.1%})',
                'priority': 1,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'exit_price': current_price
            }
        
        # === EXIT CHECK 3: Max Hold Time (PRIORITY 1 - URGENT) ===
        if hold_time_seconds >= self.max_hold_seconds:
            return {
                'should_exit': True,
                'reason': f'MAX_TIME ({hold_time_seconds/60:.1f} min)',
                'priority': 1,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'exit_price': current_price
            }
        
        # === EXIT CHECK 4: Mean Reversion (PRIORITY 2 - NORMAL) ===
        # If price has reverted to within 2% of 0.50 (fair value)
        distance_from_fair = abs(current_price - 0.50)
        
        if distance_from_fair <= self.mean_reversion_threshold and pnl_pct > 0:
            # Only exit on mean reversion if we're profitable
            return {
                'should_exit': True,
                'reason': f'MEAN_REVERSION (@ ${current_price:.4f})',
                'priority': 2,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'exit_price': current_price
            }
        
        # === EXIT CHECK 5: Regime Break (PRIORITY 2 - NORMAL) ===
        if btc_atr is not None and btc_atr > self.regime_break_atr:
            return {
                'should_exit': True,
                'reason': f'REGIME_BREAK (ATR {btc_atr:.3f})',
                'priority': 2,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'exit_price': current_price
            }
        
        # === EXIT CHECK 6: Time-based urgency (PRIORITY 3 - OPTIONAL) ===
        # If we're getting close to max time and we're profitable, consider exiting
        time_remaining = self.max_hold_seconds - hold_time_seconds
        
        if time_remaining < 120 and pnl_pct > 0.01:  # Less than 2 min left, up >1%
            return {
                'should_exit': True,
                'reason': f'TIME_PRESSURE ({time_remaining/60:.1f} min left, up {pnl_pct:.1%})',
                'priority': 3,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'exit_price': current_price
            }
        
        # === NO EXIT ===
        return {
            'should_exit': False,
            'reason': None,
            'priority': None,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'hold_time_seconds': hold_time_seconds,
            'time_remaining_seconds': self.max_hold_seconds - hold_time_seconds
        }
    
    def check_all_positions(
        self,
        positions: List[Dict[str, Any]],
        price_getter_fn,
        btc_atr: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Check all positions for exits
        
        Args:
            positions: list of position dicts
            price_getter_fn: function(token_id) -> current_price
            btc_atr: optional BTC ATR
        
        Returns:
            list of exit signals (only positions that should exit)
        """
        current_time = datetime.now(timezone.utc)
        exit_signals = []
        
        for position in positions:
            token_id = position.get('token_id')
            
            # Get current price
            try:
                current_price = price_getter_fn(token_id)
                if current_price is None:
                    continue
            except Exception as e:
                print(f"⚠️  [10] Could not get price for {token_id}: {e}")
                continue
            
            # Check exit conditions
            exit_check = self.check_exit(
                position=position,
                current_price=current_price,
                current_time=current_time,
                btc_atr=btc_atr
            )
            
            if exit_check['should_exit']:
                exit_signals.append({
                    'position': position,
                    'exit_check': exit_check,
                    'token_id': token_id,
                    'current_price': current_price
                })
        
        # Sort by priority (most urgent first)
        exit_signals.sort(key=lambda x: x['exit_check']['priority'])
        
        return exit_signals
    
    def print_exit_signal(self, exit_signal: Dict[str, Any]) -> None:
        """
        Print a nice summary of an exit signal
        """
        position = exit_signal['position']
        check = exit_signal['exit_check']
        
        emoji = "🚨" if check['priority'] == 1 else "⚠️" if check['priority'] == 2 else "💡"
        
        print(f"\n{emoji} EXIT SIGNAL (Priority {check['priority']})")
        print(f"   Token: {position.get('token_id', 'N/A')[:20]}...")
        print(f"   Reason: {check['reason']}")
        print(f"   Entry: ${position.get('entry_price', 0):.4f}")
        print(f"   Current: ${exit_signal['current_price']:.4f}")
        print(f"   PnL: ${check['pnl']:+.2f} ({check['pnl_pct']:+.2%})")
        print()
    
    def get_position_status(self, position: Dict[str, Any], current_price: float) -> str:
        """
        Get a quick status string for a position
        """
        current_time = datetime.now(timezone.utc)
        check = self.check_exit(position, current_price, current_time)
        
        if check['should_exit']:
            return f"⚠️ EXIT: {check['reason']} | PnL: ${check['pnl']:+.2f}"
        else:
            time_left = check.get('time_remaining_seconds', 0)
            return f"✅ HOLDING | PnL: ${check['pnl']:+.2f} | Time: {time_left/60:.1f}min"


print("✅ [10] Exit manager loaded")


# =============================
# Test Runner
# =============================
if __name__ == "__main__":
    print("\n🧪 Testing [10] - Exit Manager\n" + "="*90)
    
    exit_mgr = ExitManager()
    
    current_time = datetime.now(timezone.utc)
    
    print("\n" + "="*90)
    print("Test 1: Stop loss triggered")
    print("="*90)
    
    losing_position = {
        'token_id': 'token_loss',
        'entry_price': 0.50,
        'entry_time': current_time - timedelta(minutes=5),
        'side': 'BUY',
        'size': 100
    }
    
    # Price dropped to 0.48 (4% loss)
    exit_check1 = exit_mgr.check_exit(
        position=losing_position,
        current_price=0.48,
        current_time=current_time
    )
    
    print(f"Entry: $0.50 → Current: $0.48")
    print(f"Should exit: {exit_check1['should_exit']}")
    print(f"Reason: {exit_check1['reason']}")
    print(f"PnL: ${exit_check1['pnl']:+.2f} ({exit_check1['pnl_pct']:+.2%})")
    
    print("\n" + "="*90)
    print("Test 2: Take profit triggered")
    print("="*90)
    
    winning_position = {
        'token_id': 'token_win',
        'entry_price': 0.50,
        'entry_time': current_time - timedelta(minutes=5),
        'side': 'BUY',
        'size': 100
    }
    
    # Price rose to 0.53 (6% gain)
    exit_check2 = exit_mgr.check_exit(
        position=winning_position,
        current_price=0.53,
        current_time=current_time
    )
    
    print(f"Entry: $0.50 → Current: $0.53")
    print(f"Should exit: {exit_check2['should_exit']}")
    print(f"Reason: {exit_check2['reason']}")
    print(f"PnL: ${exit_check2['pnl']:+.2f} ({exit_check2['pnl_pct']:+.2%})")
    
    print("\n" + "="*90)
    print("Test 3: Max time exceeded")
    print("="*90)
    
    old_position = {
        'token_id': 'token_old',
        'entry_price': 0.50,
        'entry_time': current_time - timedelta(minutes=13),  # 13 minutes ago
        'side': 'BUY',
        'size': 100
    }
    
    exit_check3 = exit_mgr.check_exit(
        position=old_position,
        current_price=0.51,
        current_time=current_time
    )
    
    print(f"Hold time: 13 minutes (max: 12 minutes)")
    print(f"Should exit: {exit_check3['should_exit']}")
    print(f"Reason: {exit_check3['reason']}")
    
    print("\n" + "="*90)
    print("Test 4: Mean reversion")
    print("="*90)
    
    reversion_position = {
        'token_id': 'token_revert',
        'entry_price': 0.45,  # Bought at 0.45
        'entry_time': current_time - timedelta(minutes=5),
        'side': 'BUY',
        'size': 100
    }
    
    # Price reverted to 0.50 (up 11%)
    exit_check4 = exit_mgr.check_exit(
        position=reversion_position,
        current_price=0.50,
        current_time=current_time
    )
    
    print(f"Entry: $0.45 → Current: $0.50 (reverted to fair value)")
    print(f"Should exit: {exit_check4['should_exit']}")
    print(f"Reason: {exit_check4['reason']}")
    print(f"PnL: ${exit_check4['pnl']:+.2f} ({exit_check4['pnl_pct']:+.2%})")
    
    print("\n" + "="*90)
    print("Test 5: Regime break (high volatility)")
    print("="*90)
    
    regime_position = {
        'token_id': 'token_regime',
        'entry_price': 0.50,
        'entry_time': current_time - timedelta(minutes=5),
        'side': 'BUY',
        'size': 100
    }
    
    # BTC ATR spiked to 2.5%
    exit_check5 = exit_mgr.check_exit(
        position=regime_position,
        current_price=0.51,
        current_time=current_time,
        btc_atr=0.025  # 2.5% ATR
    )
    
    print(f"BTC ATR: 2.5% (threshold: 2.0%)")
    print(f"Should exit: {exit_check5['should_exit']}")
    print(f"Reason: {exit_check5['reason']}")
    
    print("\n" + "="*90)
    print("Test 6: No exit (position healthy)")
    print("="*90)
    
    healthy_position = {
        'token_id': 'token_healthy',
        'entry_price': 0.50,
        'entry_time': current_time - timedelta(minutes=5),
        'side': 'BUY',
        'size': 100
    }
    
    exit_check6 = exit_mgr.check_exit(
        position=healthy_position,
        current_price=0.52,  # Up 4% (between stop and target)
        current_time=current_time
    )
    
    print(f"Entry: $0.50 → Current: $0.52 (up 4%)")
    print(f"Should exit: {exit_check6['should_exit']}")
    print(f"PnL: ${exit_check6['pnl']:+.2f} ({exit_check6['pnl_pct']:+.2%})")
    print(f"Time remaining: {exit_check6['time_remaining_seconds']/60:.1f} minutes")
    
    print("\n" + "="*90)
    print("Test 7: Check multiple positions at once")
    print("="*90)
    
    positions = [
        losing_position,
        winning_position,
        healthy_position
    ]
    
    # Mock price getter
    def mock_price_getter(token_id):
        prices = {
            'token_loss': 0.48,
            'token_win': 0.53,
            'token_healthy': 0.52
        }
        return prices.get(token_id, 0.50)
    
    exit_signals = exit_mgr.check_all_positions(
        positions=positions,
        price_getter_fn=mock_price_getter
    )
    
    print(f"\nFound {len(exit_signals)} positions to exit:\n")
    
    for signal in exit_signals:
        exit_mgr.print_exit_signal(signal)
    
    print("="*90)
    print("✅ All tests complete!")
    print("="*90)
    
    print("\nSummary:")
    print(f"   Test 1 (stop loss):      {'✅ EXIT' if exit_check1['should_exit'] else '❌ HOLD'} - {exit_check1['reason']}")
    print(f"   Test 2 (take profit):    {'✅ EXIT' if exit_check2['should_exit'] else '❌ HOLD'} - {exit_check2['reason']}")
    print(f"   Test 3 (max time):       {'✅ EXIT' if exit_check3['should_exit'] else '❌ HOLD'} - {exit_check3['reason']}")
    print(f"   Test 4 (mean reversion): {'✅ EXIT' if exit_check4['should_exit'] else '❌ HOLD'} - {exit_check4['reason']}")
    print(f"   Test 5 (regime break):   {'✅ EXIT' if exit_check5['should_exit'] else '❌ HOLD'} - {exit_check5['reason']}")
    print(f"   Test 6 (healthy):        {'❌ HOLD' if not exit_check6['should_exit'] else '⚠️ EXIT'} - Normal")
    print(f"   Test 7 (batch check):    Found {len(exit_signals)}/3 positions to exit")
    
    print("\n" + "="*90 + "\n")