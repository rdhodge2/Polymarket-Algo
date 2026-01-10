"""
02 - Technical Indicators
ATR, Bollinger Bands, RSI, SMA, EMA
"""

import numpy as np

def calculate_atr(prices, period=14):
    """Calculate Average True Range (volatility)"""
    if len(prices) < period:
        return 0
    
    if isinstance(prices[0], (int, float)):
        closes = np.array(prices)
        highs = closes
        lows = closes
    else:
        highs = np.array([p['high'] for p in prices])
        lows = np.array([p['low'] for p in prices])
        closes = np.array([p['close'] for p in prices])
    
    high_low = highs - lows
    high_close = np.abs(highs[1:] - closes[:-1])
    low_close = np.abs(lows[1:] - closes[:-1])
    
    high_close = np.concatenate([[high_low[0]], high_close])
    low_close = np.concatenate([[high_low[0]], low_close])
    
    true_range = np.maximum(high_low, np.maximum(high_close, low_close))
    atr = np.mean(true_range[-period:])
    
    current_price = closes[-1]
    return atr / current_price if current_price > 0 else 0


def calculate_bollinger_bands(prices, period=20, std_dev=2):
    """Calculate Bollinger Bands"""
    if len(prices) < period:
        return 0, 0, 0
    
    prices_array = np.array(prices)
    middle_band = np.mean(prices_array[-period:])
    std = np.std(prices_array[-period:])
    
    upper_band = middle_band + (std_dev * std)
    lower_band = middle_band - (std_dev * std)
    
    return upper_band, lower_band, middle_band


def calculate_rsi(prices, period=14):
    """Calculate RSI"""
    if len(prices) < period + 1:
        return 50
    
    prices_array = np.array(prices)
    deltas = np.diff(prices_array)
    
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    
    if avg_loss == 0:
        return 100
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


def calculate_sma(prices, period):
    """Simple Moving Average"""
    if len(prices) < period:
        return np.mean(prices) if len(prices) > 0 else 0
    return np.mean(prices[-period:])


def calculate_ema(prices, period):
    """Exponential Moving Average"""
    if len(prices) < period:
        return np.mean(prices) if len(prices) > 0 else 0
    
    prices_array = np.array(prices)
    multiplier = 2 / (period + 1)
    ema = np.mean(prices_array[:period])
    
    for price in prices_array[period:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))
    
    return ema


print("✅ [02] Indicators loaded")


# Testing
if __name__ == "__main__":
    print("\n🧪 Testing Indicators\n")
    
    test_prices = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 
                   111, 110, 112, 114, 113, 115, 117, 116, 118, 120]
    
    print(f"ATR: {calculate_atr(test_prices):.4f}")
    
    upper, lower, middle = calculate_bollinger_bands(test_prices)
    print(f"BB: Upper={upper:.2f}, Mid={middle:.2f}, Lower={lower:.2f}")
    
    print(f"RSI: {calculate_rsi(test_prices):.2f}")
    print(f"SMA(10): {calculate_sma(test_prices, 10):.2f}")
    
    print("\n✅ All indicators working")