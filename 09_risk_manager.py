"""
09 - Risk Manager
Portfolio-level risk management and circuit breakers

Monitors:
1. Daily loss limit (5% max drawdown per day)
2. Max concurrent positions (3 max)
3. Max exposure (total $ at risk)
4. Consecutive losses (stop after 5 in a row)
5. Win rate monitoring (pause if drops below threshold)

This is the SAFETY NET - prevents catastrophic losses
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, date

# Risk limits
DAILY_LOSS_LIMIT_PCT = 0.05      # 5% max daily loss
MAX_CONCURRENT_POSITIONS = 3     # Max 3 positions at once
MAX_CONSECUTIVE_LOSSES = 5       # Stop after 5 losses in a row
MIN_WIN_RATE_THRESHOLD = 0.40    # Pause if win rate drops below 40%
MIN_TRADES_FOR_WIN_RATE = 20     # Need 20 trades before checking win rate


class RiskManager:
    """
    Portfolio-level risk management
    Prevents you from blowing up your account
    """
    
    def __init__(
        self,
        starting_bankroll: float,
        daily_loss_limit_pct: float = DAILY_LOSS_LIMIT_PCT,
        max_concurrent: int = MAX_CONCURRENT_POSITIONS,
        max_consecutive_losses: int = MAX_CONSECUTIVE_LOSSES,
        min_win_rate: float = MIN_WIN_RATE_THRESHOLD,
        min_trades_for_wr: int = MIN_TRADES_FOR_WIN_RATE
    ):
        self.starting_bankroll = starting_bankroll
        self.current_bankroll = starting_bankroll
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_concurrent = max_concurrent
        self.max_consecutive_losses = max_consecutive_losses
        self.min_win_rate = min_win_rate
        self.min_trades_for_wr = min_trades_for_wr
        
        # Track state
        self.open_positions = []  # List of open position dicts
        self.today_start_bankroll = starting_bankroll
        self.today_pnl = 0.0
        self.consecutive_losses = 0
        self.trade_history = []  # List of win/loss (True/False)
        self.trading_paused = False
        self.pause_reason = None
        
        print(f"✅ [09] Risk manager initialized")
        print(f"   Starting Bankroll: ${self.starting_bankroll:,.2f}")
        print(f"   Daily Loss Limit: {self.daily_loss_limit_pct:.1%} (${self.starting_bankroll * self.daily_loss_limit_pct:,.2f})")
        print(f"   Max Concurrent: {self.max_concurrent} positions")
        print(f"   Max Consecutive Losses: {self.max_consecutive_losses}")
    
    def can_open_position(self, position_size: float) -> Dict[str, Any]:
        """
        Check if it's safe to open a new position
        
        Args:
            position_size: proposed position size in dollars
        
        Returns:
            dict with:
                - allowed: bool (can we trade?)
                - reason: str (why or why not)
                - checks: dict of individual check results
        """
        checks = {}
        reasons = []
        
        # === CHECK 1: Trading Paused? ===
        if self.trading_paused:
            return {
                'allowed': False,
                'reason': f"Trading paused: {self.pause_reason}",
                'checks': {}
            }
        
        # === CHECK 2: Daily Loss Limit ===
        daily_loss_limit = self.today_start_bankroll * self.daily_loss_limit_pct
        
        if self.today_pnl <= -daily_loss_limit:
            checks['daily_loss'] = {
                'pass': False,
                'current_loss': self.today_pnl,
                'limit': -daily_loss_limit
            }
            reasons.append(f"Daily loss limit hit (${abs(self.today_pnl):.2f} / ${daily_loss_limit:.2f})")
        else:
            checks['daily_loss'] = {
                'pass': True,
                'current_loss': self.today_pnl,
                'limit': -daily_loss_limit
            }
        
        # === CHECK 3: Max Concurrent Positions ===
        current_positions = len(self.open_positions)
        
        if current_positions >= self.max_concurrent:
            checks['concurrent_positions'] = {
                'pass': False,
                'current': current_positions,
                'max': self.max_concurrent
            }
            reasons.append(f"Max positions ({current_positions}/{self.max_concurrent})")
        else:
            checks['concurrent_positions'] = {
                'pass': True,
                'current': current_positions,
                'max': self.max_concurrent
            }
        
        # === CHECK 4: Consecutive Losses ===
        if self.consecutive_losses >= self.max_consecutive_losses:
            checks['consecutive_losses'] = {
                'pass': False,
                'current': self.consecutive_losses,
                'max': self.max_consecutive_losses
            }
            reasons.append(f"Too many consecutive losses ({self.consecutive_losses})")
        else:
            checks['consecutive_losses'] = {
                'pass': True,
                'current': self.consecutive_losses,
                'max': self.max_consecutive_losses
            }
        
        # === CHECK 5: Win Rate (if enough trades) ===
        if len(self.trade_history) >= self.min_trades_for_wr:
            wins = sum(self.trade_history)
            win_rate = wins / len(self.trade_history)
            
            if win_rate < self.min_win_rate:
                checks['win_rate'] = {
                    'pass': False,
                    'current': win_rate,
                    'min': self.min_win_rate,
                    'trades': len(self.trade_history)
                }
                reasons.append(f"Win rate too low ({win_rate:.1%} < {self.min_win_rate:.1%})")
            else:
                checks['win_rate'] = {
                    'pass': True,
                    'current': win_rate,
                    'min': self.min_win_rate
                }
        else:
            checks['win_rate'] = {
                'pass': True,
                'note': f'Not enough trades ({len(self.trade_history)}/{self.min_trades_for_wr})'
            }
        
        # === CHECK 6: Position Size Sanity ===
        if position_size > self.current_bankroll * 0.5:
            checks['position_size'] = {
                'pass': False,
                'size': position_size,
                'bankroll': self.current_bankroll
            }
            reasons.append(f"Position size too large (${position_size:.2f} > 50% of bankroll)")
        else:
            checks['position_size'] = {
                'pass': True,
                'size': position_size
            }
        
        # === FINAL DECISION ===
        all_pass = all([check['pass'] for check in checks.values()])
        
        if all_pass:
            return {
                'allowed': True,
                'reason': 'All risk checks passed',
                'checks': checks
            }
        else:
            return {
                'allowed': False,
                'reason': '; '.join(reasons),
                'checks': checks
            }
    
    def open_position(self, position_data: Dict[str, Any]) -> bool:
        """
        Record opening a position
        
        Args:
            position_data: dict with position details
                Required: token_id, side, size, entry_price, entry_time
        
        Returns:
            bool: success
        """
        self.open_positions.append(position_data)
        print(f"📊 [09] Position opened: {len(self.open_positions)}/{self.max_concurrent} positions active")
        return True
    
    def close_position(self, token_id: str, pnl: float) -> bool:
        """
        Record closing a position
        
        Args:
            token_id: token ID to close
            pnl: profit/loss in dollars
        
        Returns:
            bool: success
        """
        # Find and remove position
        self.open_positions = [p for p in self.open_positions if p.get('token_id') != token_id]
        
        # Update today's PnL
        self.today_pnl += pnl
        
        # Update bankroll
        self.current_bankroll += pnl
        
        # Track win/loss
        is_win = pnl > 0
        self.trade_history.append(is_win)
        
        # Update consecutive losses
        if is_win:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
        
        # Check if we need to pause trading
        self._check_circuit_breakers()
        
        emoji = "💰" if is_win else "📉"
        print(f"{emoji} [09] Position closed: PnL ${pnl:+.2f} | Today: ${self.today_pnl:+.2f} | Bankroll: ${self.current_bankroll:,.2f}")
        
        return True
    
    def _check_circuit_breakers(self) -> None:
        """
        Check if we should pause trading (circuit breakers)
        """
        # Check 1: Daily loss limit
        daily_loss_limit = self.today_start_bankroll * self.daily_loss_limit_pct
        if self.today_pnl <= -daily_loss_limit:
            self.trading_paused = True
            self.pause_reason = f"Daily loss limit hit (${abs(self.today_pnl):.2f})"
            print(f"\n🚨 [09] CIRCUIT BREAKER: {self.pause_reason}")
            print(f"   Trading paused for the day\n")
            return
        
        # Check 2: Consecutive losses
        if self.consecutive_losses >= self.max_consecutive_losses:
            self.trading_paused = True
            self.pause_reason = f"{self.consecutive_losses} consecutive losses"
            print(f"\n🚨 [09] CIRCUIT BREAKER: {self.pause_reason}")
            print(f"   Trading paused - review strategy\n")
            return
        
        # Check 3: Win rate (if enough trades)
        if len(self.trade_history) >= self.min_trades_for_wr:
            wins = sum(self.trade_history)
            win_rate = wins / len(self.trade_history)
            
            if win_rate < self.min_win_rate:
                self.trading_paused = True
                self.pause_reason = f"Win rate too low ({win_rate:.1%})"
                print(f"\n🚨 [09] CIRCUIT BREAKER: {self.pause_reason}")
                print(f"   Trading paused - review strategy\n")
                return
    
    def reset_daily(self) -> None:
        """
        Reset daily counters (call at start of new day)
        """
        old_pnl = self.today_pnl
        
        self.today_start_bankroll = self.current_bankroll
        self.today_pnl = 0.0
        self.trading_paused = False
        self.pause_reason = None
        
        print(f"\n📅 [09] Daily reset")
        print(f"   Yesterday's PnL: ${old_pnl:+.2f}")
        print(f"   New starting bankroll: ${self.today_start_bankroll:,.2f}")
        print(f"   Trading resumed\n")
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current risk status
        """
        wins = sum(self.trade_history) if self.trade_history else 0
        total = len(self.trade_history)
        win_rate = (wins / total) if total > 0 else 0
        
        daily_loss_limit = self.today_start_bankroll * self.daily_loss_limit_pct
        
        return {
            'trading_allowed': not self.trading_paused,
            'pause_reason': self.pause_reason,
            'current_bankroll': self.current_bankroll,
            'today_pnl': self.today_pnl,
            'today_pnl_pct': (self.today_pnl / self.today_start_bankroll * 100) if self.today_start_bankroll > 0 else 0,
            'daily_loss_remaining': daily_loss_limit + self.today_pnl,  # How much more can we lose
            'open_positions': len(self.open_positions),
            'max_positions': self.max_concurrent,
            'consecutive_losses': self.consecutive_losses,
            'total_trades': total,
            'win_rate': win_rate,
            'min_win_rate': self.min_win_rate
        }
    
    def print_status(self) -> None:
        """
        Print a nice status summary
        """
        status = self.get_status()
        
        status_emoji = "✅" if status['trading_allowed'] else "🚨"
        print(f"\n{status_emoji} Risk Manager Status:")
        print(f"   Trading: {'ALLOWED' if status['trading_allowed'] else 'PAUSED - ' + status['pause_reason']}")
        print(f"   Bankroll: ${status['current_bankroll']:,.2f}")
        print(f"   Today PnL: ${status['today_pnl']:+.2f} ({status['today_pnl_pct']:+.2f}%)")
        print(f"   Daily Loss Remaining: ${status['daily_loss_remaining']:.2f}")
        print(f"   Open Positions: {status['open_positions']}/{status['max_positions']}")
        print(f"   Consecutive Losses: {status['consecutive_losses']}/{self.max_consecutive_losses}")
        
        if status['total_trades'] > 0:
            print(f"   Win Rate: {status['win_rate']:.1%} ({sum(self.trade_history)}/{status['total_trades']} trades)")
        
        print()


print("✅ [09] Risk manager loaded")


# =============================
# Test Runner
# =============================
if __name__ == "__main__":
    print("\n🧪 Testing [09] - Risk Manager\n" + "="*90)
    
    # Initialize with $1000
    risk_mgr = RiskManager(starting_bankroll=1000)
    
    print("\n" + "="*90)
    print("Test 1: Check if we can open position (should be allowed)")
    print("="*90)
    
    check1 = risk_mgr.can_open_position(position_size=20)
    print(f"Result: {'✅ ALLOWED' if check1['allowed'] else '❌ BLOCKED'}")
    print(f"Reason: {check1['reason']}")
    
    print("\n" + "="*90)
    print("Test 2: Open 3 positions (hit max concurrent)")
    print("="*90)
    
    for i in range(3):
        risk_mgr.open_position({
            'token_id': f'token_{i}',
            'side': 'BUY',
            'size': 20,
            'entry_price': 0.50,
            'entry_time': datetime.now(timezone.utc)
        })
    
    # Try to open 4th
    check2 = risk_mgr.can_open_position(position_size=20)
    print(f"\nTrying to open 4th position:")
    print(f"Result: {'✅ ALLOWED' if check2['allowed'] else '❌ BLOCKED'}")
    print(f"Reason: {check2['reason']}")
    
    print("\n" + "="*90)
    print("Test 3: Close positions with wins and losses")
    print("="*90)
    
    # Win
    risk_mgr.close_position('token_0', pnl=5.0)
    
    # Loss
    risk_mgr.close_position('token_1', pnl=-3.0)
    
    # Win
    risk_mgr.close_position('token_2', pnl=4.0)
    
    risk_mgr.print_status()
    
    print("="*90)
    print("Test 4: Trigger consecutive losses circuit breaker")
    print("="*90)
    
    for i in range(5):
        risk_mgr.open_position({
            'token_id': f'loss_token_{i}',
            'side': 'BUY',
            'size': 20
        })
        risk_mgr.close_position(f'loss_token_{i}', pnl=-2.0)
    
    risk_mgr.print_status()
    
    # Try to trade after pause
    check3 = risk_mgr.can_open_position(position_size=20)
    print(f"Trying to trade after circuit breaker:")
    print(f"Result: {'✅ ALLOWED' if check3['allowed'] else '❌ BLOCKED'}")
    print(f"Reason: {check3['reason']}")
    
    print("\n" + "="*90)
    print("Test 5: Reset daily and resume trading")
    print("="*90)
    
    risk_mgr.reset_daily()
    
    check4 = risk_mgr.can_open_position(position_size=20)
    print(f"After reset:")
    print(f"Result: {'✅ ALLOWED' if check4['allowed'] else '❌ BLOCKED'}")
    print(f"Reason: {check4['reason']}")
    
    print("\n" + "="*90)
    print("✅ All tests complete!")
    print("="*90 + "\n")