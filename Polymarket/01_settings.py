"""
01 - Configuration Settings
All constants and thresholds for the trading system
"""

import os

# === API CONFIGURATION ===
ALPACA_API_KEY = os.getenv('APCA_API_KEY_ID')
ALPACA_SECRET_KEY = os.getenv('APCA_API_SECRET_KEY')
POLYMARKET_API_KEY = os.getenv('POLYMARKET_API_KEY')
POLYMARKET_SECRET = os.getenv('POLYMARKET_SECRET')
POLYMARKET_WALLET_ADDRESS = os.getenv('POLYMARKET_WALLET')

# === API ENDPOINTS ===
ALPACA_BASE_URL = "https://data.alpaca.markets"
POLYMARKET_CLOB_ENDPOINT = "https://clob.polymarket.com"

# === MARKET SETTINGS ===
SUPPORTED_MARKETS = ["BTC", "ETH"]
MARKET_REFRESH_INTERVAL = 5  # seconds
PRICE_UPDATE_INTERVAL = 1  # seconds

# === REGIME FILTER THRESHOLDS ===
MAX_BTC_ATR = 0.015              # 1.5% max ATR
MAX_BB_WIDTH = 0.020             # 2% max Bollinger Band width
MAX_SPREAD = 0.03                # 3% max bid-ask spread
MIN_ORDERBOOK_BALANCE = 0.40     # 40/60 min balance
MAX_ORDERBOOK_BALANCE = 0.60     # 40/60 max balance

# === OVERREACTION THRESHOLDS ===
MIN_PRICE_CHANGE = 0.05          # 5% move triggers signal
VOLUME_SPIKE_MULTIPLIER = 2.0    # 2x normal volume
SMALL_TRADE_SIZE = 50            # $50 avg = retail
MIN_OVERREACTION_SCORE = 60      # Min score to trade
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# === POSITION SIZING ===
MAX_POSITION_PCT = 0.02          # 2% of bankroll
KELLY_FRACTION = 0.25            # Use 1/4 Kelly
MAX_MARKET_DEPTH_PCT = 0.05      # Max 5% of depth

# === RISK MANAGEMENT ===
STOP_LOSS_PCT = 0.02             # 2% stop
TAKE_PROFIT_PCT = 0.05           # 5% target
MAX_HOLD_TIME = 720              # 12 minutes (seconds)
MAX_CONCURRENT_POSITIONS = 3
DAILY_LOSS_LIMIT_PCT = 0.05      # 5% daily stop

# === EXIT CONDITIONS ===
MEAN_REVERSION_THRESHOLD = 0.02  # 2% from fair value
REGIME_BREAK_ATR = 0.020
ORDER_TIMEOUT = 30               # Cancel after 30s

# === EXCEL LOGGING ===
LOG_DIR = "logs"
LOG_MARKET_DATA = True
MARKET_DATA_LOG_INTERVAL = 60

# === BOT SETTINGS ===
STARTING_BANKROLL = 200
MIN_TRADE_SIZE = 5
MAX_TRADE_SIZE = 40

# === SAFETY ===
DRY_RUN = False                  # Set True for testing
VERBOSE_LOGGING = True

print("✅ [01] Settings loaded")