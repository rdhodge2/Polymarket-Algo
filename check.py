"""
Quick script to check current market spreads
"""
from importlib import import_module

poly = import_module('04_polymarket_client').PolymarketClient()

print('\n🔍 Checking actual spreads in live markets...\n')

markets = poly.get_active_btc_eth_15m_updown_markets(
    window_minutes=30,
    include_eth=True,
    print_markets=False
)

if not markets:
    print('❌ No active markets found')
    print('   Markets may not be active right now')
    print('   Try again during US market hours (10am-4pm EST)')
    exit()

print(f'Found {len(markets)} markets\n')
print('='*70)

valid_markets = 0

for m in markets:
    print(f"\nMarket: {m.get('slug')}")
    
    tokens = poly.get_token_ids_from_market(m)
    outcomes = poly.get_outcomes_from_market(m)
    
    for i, token_id in enumerate(tokens):
        outcome = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
        
        book = poly.get_orderbook(token_id)
        
        if book:
            # Handle missing bid/ask data
            best_bid = book.get('best_bid')
            best_ask = book.get('best_ask')
            spread = book.get('spread', 0)
            bid_depth = book.get('bid_depth', 0)
            ask_depth = book.get('ask_depth', 0)
            
            if best_bid is None or best_ask is None:
                print(f"  {outcome}:")
                print(f"    ⚠️  No liquidity (empty orderbook)")
                continue
            
            spread_pct = spread * 100
            
            print(f"  {outcome}:")
            print(f"    Best Bid: ${best_bid:.4f}")
            print(f"    Best Ask: ${best_ask:.4f}")
            
            # Visual indicator
            if spread_pct < 8:
                indicator = "✅ GOOD"
            elif spread_pct < 15:
                indicator = "⚠️  MEDIUM"
            else:
                indicator = "❌ WIDE"
            
            print(f"    Spread:   {spread_pct:.1f}% {indicator}")
            print(f"    Depth:    ${bid_depth + ask_depth:.2f}")
            
            if best_bid and best_ask and spread_pct < 50:
                valid_markets += 1

print('\n' + '='*70)
print(f'\nValid markets found: {valid_markets}')
print('\nSpread Guidelines:')
print('  ✅ 0-8%:   Good spreads - use MAX_SPREAD = 0.08')
print('  ⚠️  8-15%:  Medium spreads - use MAX_SPREAD = 0.12-0.15')
print('  ❌ 15%+:   Wide spreads - use MAX_SPREAD = 0.20 or wait for better hours')
print('='*70 + '\n')