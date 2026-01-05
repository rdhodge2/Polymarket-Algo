"""
08 - Position Sizer
Calculates optimal position size based on edge and Kelly Criterion

Key principles:
1. Size based on EDGE, not conviction
2. Use fractional Kelly (1/4 Kelly) for safety
3. Cap at max % of bankroll (2%)
4. Cap at max % of market depth (5%)
5. Enforce minimum trade size ($10)

Formula: Position = (Edge * Confidence) / Variance * Kelly_Fraction * Bankroll
But capped by risk limits
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone

# Position sizing limits
MAX_POSITION_PCT = 0.02          # 2% of bankroll max
KELLY_FRACTION = 0.25            # Use 1/4 Kelly (conservative)
MAX_MARKET_DEPTH_PCT = 0.05      # Max 5% of market depth
MIN_TRADE_SIZE = 10              # Minimum $10 per trade
MAX_TRADE_SIZE = 200             # Maximum $200 per trade (initially)


class PositionSizer:
    """
    Calculate optimal position size using Kelly Criterion
    """
    
    def __init__(
        self,
        bankroll: float,
        max_position_pct: float = MAX_POSITION_PCT,
        kelly_fraction: float = KELLY_FRACTION,
        max_depth_pct: float = MAX_MARKET_DEPTH_PCT,
        min_trade_size: float = MIN_TRADE_SIZE,
        max_trade_size: float = MAX_TRADE_SIZE
    ):
        self.bankroll = bankroll
        self.max_position_pct = max_position_pct
        self.kelly_fraction = kelly_fraction
        self.max_depth_pct = max_depth_pct
        self.min_trade_size = min_trade_size
        self.max_trade_size = max_trade_size
        
        print(f"✅ [08] Position sizer initialized")
        print(f"   Bankroll: ${self.bankroll:,.2f}")
        print(f"   Max Position: {self.max_position_pct:.1%} (${self.bankroll * self.max_position_pct:,.2f})")
        print(f"   Kelly Fraction: {self.kelly_fraction:.2f} (1/{int(1/self.kelly_fraction)} Kelly)")
        print(f"   Trade Range: ${self.min_trade_size:.0f} - ${self.max_trade_size:.0f}")
    
    def calculate_size(
        self,
        edge: float,
        confidence: float,
        market_depth: float,
        regime_score: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Calculate position size
        
        Args:
            edge: expected edge (e.g., 0.05 for 5% edge)
            confidence: signal confidence 0-1 (e.g., 0.75 for 75%)
            market_depth: total market depth in dollars
            regime_score: optional regime score 0-1 (reduces size if regime weak)
        
        Returns:
            dict with:
                - size: position size in dollars
                - reasoning: explanation of sizing
                - kelly_size: raw Kelly size (before caps)
                - final_size: actual size to trade
                - size_pct_bankroll: % of bankroll
                - size_pct_depth: % of market depth
        """
        # === STEP 1: Kelly Criterion Calculation ===
        # Kelly = (Edge * Confidence) / Variance
        # Assuming variance of ~0.5 for binary outcomes
        variance = 0.5
        
        kelly_fraction_calc = (edge * confidence) / variance
        
        # Apply fractional Kelly (1/4 Kelly for safety)
        fractional_kelly = kelly_fraction_calc * self.kelly_fraction
        
        # Kelly size in dollars
        kelly_size = self.bankroll * fractional_kelly
        
        # === STEP 2: Apply Regime Adjustment ===
        # If regime score is provided and weak, reduce size
        regime_multiplier = 1.0
        if regime_score is not None:
            # If regime score is 75%, use 75% of calculated size
            regime_multiplier = regime_score
        
        adjusted_size = kelly_size * regime_multiplier
        
        # === STEP 3: Apply Hard Caps ===
        caps_applied = []
        
        # Cap 1: Max % of bankroll
        max_bankroll_size = self.bankroll * self.max_position_pct
        if adjusted_size > max_bankroll_size:
            adjusted_size = max_bankroll_size
            caps_applied.append(f"Bankroll cap ({self.max_position_pct:.1%})")
        
        # Cap 2: Max % of market depth
        max_depth_size = market_depth * self.max_depth_pct
        if adjusted_size > max_depth_size:
            adjusted_size = max_depth_size
            caps_applied.append(f"Market depth cap ({self.max_depth_pct:.1%})")
        
        # Cap 3: Absolute max trade size
        if adjusted_size > self.max_trade_size:
            adjusted_size = self.max_trade_size
            caps_applied.append(f"Max trade cap (${self.max_trade_size:.0f})")
        
        # === STEP 4: Check Minimum ===
        if adjusted_size < self.min_trade_size:
            # Size too small to trade
            return {
                'size': 0,
                'final_size': 0,
                'tradeable': False,
                'reasoning': f"Size ${adjusted_size:.2f} below minimum ${self.min_trade_size:.0f}",
                'kelly_size': kelly_size,
                'size_pct_bankroll': 0,
                'size_pct_depth': 0,
                'caps_applied': caps_applied
            }
        
        # === STEP 5: Calculate Percentages ===
        size_pct_bankroll = (adjusted_size / self.bankroll) * 100
        size_pct_depth = (adjusted_size / market_depth * 100) if market_depth > 0 else 0
        
        # === STEP 6: Build Reasoning ===
        reasoning_parts = []
        
        reasoning_parts.append(f"Kelly: ${kelly_size:.2f}")
        
        if regime_score is not None and regime_score < 1.0:
            reasoning_parts.append(f"Regime adj: {regime_score:.1%}")
        
        if caps_applied:
            reasoning_parts.append(f"Caps: {', '.join(caps_applied)}")
        else:
            reasoning_parts.append("No caps hit")
        
        reasoning = " | ".join(reasoning_parts)
        
        return {
            'size': adjusted_size,
            'final_size': adjusted_size,
            'tradeable': True,
            'reasoning': reasoning,
            'kelly_size': kelly_size,
            'size_pct_bankroll': size_pct_bankroll,
            'size_pct_depth': size_pct_depth,
            'caps_applied': caps_applied,
            'edge': edge,
            'confidence': confidence,
            'regime_multiplier': regime_multiplier
        }
    
    def update_bankroll(self, new_bankroll: float) -> None:
        """
        Update bankroll after wins/losses
        Call this after each trade or daily
        """
        old_bankroll = self.bankroll
        self.bankroll = new_bankroll
        change = new_bankroll - old_bankroll
        change_pct = (change / old_bankroll * 100) if old_bankroll > 0 else 0
        
        print(f"💰 [08] Bankroll updated: ${old_bankroll:,.2f} → ${new_bankroll:,.2f} ({change_pct:+.2f}%)")
    
    def print_sizing(self, sizing_result: Dict[str, Any]) -> None:
        """
        Print a nice summary of position sizing
        """
        if not sizing_result['tradeable']:
            print(f"\n❌ Position Size: TOO SMALL")
            print(f"   {sizing_result['reasoning']}")
            return
        
        size = sizing_result['final_size']
        
        print(f"\n💵 Position Size: ${size:.2f}")
        print(f"   {sizing_result['reasoning']}")
        print(f"   % of Bankroll: {sizing_result['size_pct_bankroll']:.2f}%")
        print(f"   % of Market Depth: {sizing_result['size_pct_depth']:.2f}%")
        
        if sizing_result.get('caps_applied'):
            print(f"   ⚠️  Caps Applied: {', '.join(sizing_result['caps_applied'])}")
        
        print()


print("✅ [08] Position sizer loaded")


# =============================
# Test Runner
# =============================
if __name__ == "__main__":
    print("\n🧪 Testing [08] - Position Sizer\n" + "="*90)
    
    # Initialize with $1000 bankroll
    sizer = PositionSizer(bankroll=1000)
    
    print("\n" + "="*90)
    print("Test 1: Normal signal (5% edge, 75% confidence)")
    print("="*90)
    
    result1 = sizer.calculate_size(
        edge=0.05,           # 5% edge
        confidence=0.75,     # 75% confidence
        market_depth=5000,   # $5000 market depth
        regime_score=1.0     # Perfect regime
    )
    
    sizer.print_sizing(result1)
    
    print("="*90)
    print("Test 2: High confidence signal (8% edge, 90% confidence)")
    print("="*90)
    
    result2 = sizer.calculate_size(
        edge=0.08,
        confidence=0.90,
        market_depth=5000,
        regime_score=1.0
    )
    
    sizer.print_sizing(result2)
    
    print("="*90)
    print("Test 3: Weak regime (5% edge, 75% confidence, 60% regime)")
    print("="*90)
    
    result3 = sizer.calculate_size(
        edge=0.05,
        confidence=0.75,
        market_depth=5000,
        regime_score=0.60  # Only 60% regime score
    )
    
    sizer.print_sizing(result3)
    
    print("="*90)
    print("Test 4: Thin market (depth too low)")
    print("="*90)
    
    result4 = sizer.calculate_size(
        edge=0.05,
        confidence=0.75,
        market_depth=300,  # Only $300 depth
        regime_score=1.0
    )
    
    sizer.print_sizing(result4)
    
    print("="*90)
    print("Test 5: Low edge signal (below minimum trade size)")
    print("="*90)
    
    result5 = sizer.calculate_size(
        edge=0.01,         # Only 1% edge
        confidence=0.50,   # Low confidence
        market_depth=5000,
        regime_score=1.0
    )
    
    sizer.print_sizing(result5)
    
    print("="*90)
    print("Test 6: Update bankroll after winning trade")
    print("="*90)
    
    # Simulate a winning trade
    trade_pnl = 5.00  # Made $5
    sizer.update_bankroll(1000 + trade_pnl)
    
    # Check new position size
    result6 = sizer.calculate_size(
        edge=0.05,
        confidence=0.75,
        market_depth=5000,
        regime_score=1.0
    )
    
    print(f"\nNew position size: ${result6['final_size']:.2f}")
    print(f"(Increased from ${result1['final_size']:.2f} due to larger bankroll)")
    
    print("\n" + "="*90)
    print("Test 7: Edge cases")
    print("="*90)
    
    # Very large bankroll
    big_sizer = PositionSizer(bankroll=100000)
    result7 = big_sizer.calculate_size(edge=0.05, confidence=0.75, market_depth=5000, regime_score=1.0)
    print(f"\n💰 Large bankroll ($100k):")
    print(f"   Position size: ${result7['final_size']:.2f}")
    print(f"   Limited by: {', '.join(result7['caps_applied']) if result7['caps_applied'] else 'Edge calculation'}")
    
    # Very small market
    tiny_market = sizer.calculate_size(edge=0.05, confidence=0.75, market_depth=50, regime_score=1.0)
    print(f"\n📉 Tiny market ($50 depth):")
    print(f"   Position size: ${tiny_market['final_size']:.2f}")
    if tiny_market['tradeable']:
        print(f"   Limited by: {', '.join(tiny_market['caps_applied']) if tiny_market['caps_applied'] else 'Edge calculation'}")
    else:
        print(f"   Not tradeable: {tiny_market['reasoning']}")
    
    print("\n" + "="*90)
    print("✅ All tests complete!")
    print("="*90)
    
    print("\nSummary:")
    test1_size = result1['final_size'] if result1['tradeable'] else 0.00
    test2_size = result2['final_size'] if result2['tradeable'] else 0.00
    test3_size = result3['final_size'] if result3['tradeable'] else 0.00
    test4_size = result4['final_size'] if result4['tradeable'] else 0.00
    test5_size = result5['final_size'] if result5['tradeable'] else 0.00
    test6_size = result6['final_size'] if result6['tradeable'] else 0.00
    
    print(f"   Test 1 (normal):       ${test1_size:.2f} - {result1['size_pct_bankroll']:.2f}% of bankroll")
    print(f"   Test 2 (high conf):    ${test2_size:.2f} - {result2['size_pct_bankroll']:.2f}% of bankroll")
    print(f"   Test 3 (weak regime):  ${test3_size:.2f} - {result3['size_pct_bankroll']:.2f}% of bankroll")
    print(f"   Test 4 (thin market):  ${test4_size:.2f} - capped by depth")
    print(f"   Test 5 (low edge):     ${test5_size:.2f} - below minimum")
    print(f"   Test 6 (after win):    ${test6_size:.2f} - bankroll grew")
    
    print("\n" + "="*90 + "\n")