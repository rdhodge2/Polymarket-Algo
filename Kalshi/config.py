# config.py
# ============================================================
# Kalshi MVP – Sell Premature Certainty
#
# Official Kalshi API key model:
# - You generate an API key in Account settings
# - You receive:
#   * API Key ID (string / UUID-like)
#   * Private key file downloaded (PEM, often .key or .txt)
#
# Authenticated requests are signed and sent to:
#   demo: https://demo-api.kalshi.co
#   prod: https://api.kalshi.com
#
# Public market data (no auth) is available at:
#   https://api.elections.kalshi.com/trade-api/v2
# ============================================================

from __future__ import annotations

from pathlib import Path
import os
import re
import time


# ============================================================
# ENVIRONMENT
# ============================================================

KALSHI_ENV = os.getenv("KALSHI_ENV", "demo").strip().lower()  # "demo" or "prod"

# ---- Kalshi auth (required for trading/portfolio endpoints) ----
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")  # Kalshi "API Key ID" :contentReference[oaicite:4]{index=4}
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH")  # path to downloaded key file (.key/.txt) :contentReference[oaicite:5]{index=5}

# ---- Alpaca (anchor) ----
# Alpaca commonly uses APCA_API_KEY_ID / APCA_API_SECRET_KEY, but keep your current names too.
ALPACA_API_KEY = os.getenv("APCA_API_KEY") or os.getenv("APCA_API_KEY_ID")
ALPACA_API_SECRET = os.getenv("APCA_API_SECRET") or os.getenv("APCA_API_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("APCA_BASE_URL", "https://api.alpaca.markets")


# ============================================================
# BASE URLS
# ============================================================

# Public market data (no auth) – Kalshi docs explicitly say to use elections host for public endpoints :contentReference[oaicite:6]{index=6}
KALSHI_PUBLIC_API_ROOT = "https://api.elections.kalshi.com/trade-api/v2"

# Authenticated trading/portfolio endpoints – docs show demo host and prod host :contentReference[oaicite:7]{index=7}
KALSHI_TRADE_HOST = "https://demo-api.kalshi.co" if KALSHI_ENV == "demo" else "https://api.kalshi.com"
KALSHI_TRADE_API_ROOT = KALSHI_TRADE_HOST + "/trade-api/v2"


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

MARKET_CACHE_FILE = DATA_DIR / "markets_cache.json"
ANCHOR_FILE = DATA_DIR / "anchor_metrics.json"
ROLLING_QUOTES_FILE = DATA_DIR / "rolling_quotes.json"
SIGNAL_FILE = DATA_DIR / "signal.json"
POSITIONS_FILE = DATA_DIR / "positions.json"

# Excel logs (recommended MVP)
SIGNALS_XLSX = LOG_DIR / "signals.xlsx"
TRADES_XLSX  = LOG_DIR / "trades.xlsx"
STATE_XLSX   = LOG_DIR / "state.xlsx"


# ============================================================
# PRIVATE KEY LOADER (PEM)
# ============================================================

def load_kalshi_private_key() -> str:
    """
    Load the Kalshi private key from file.

    Kalshi provides a downloaded private key file in PEM format (often .key or .txt). :contentReference[oaicite:8]{index=8}
    We:
      - validate presence
      - normalize line endings
      - remove whitespace INSIDE the base64 payload (fixes accidental spaces)
      - re-wrap payload to 64 chars per line

    Returns: PEM string (with BEGIN/END lines).
    """
    if not KALSHI_PRIVATE_KEY_PATH:
        raise RuntimeError("Missing env var: KALSHI_PRIVATE_KEY_PATH")

    path = Path(KALSHI_PRIVATE_KEY_PATH)
    if not path.exists():
        raise RuntimeError(f"Kalshi private key file not found: {path}")

    raw = path.read_text(encoding="utf-8", errors="ignore").strip()

    if not raw.startswith("-----BEGIN"):
        # Per Kalshi docs the downloaded key is a private key file; if it isn't PEM, it's not usable for signing. :contentReference[oaicite:9]{index=9}
        raise RuntimeError(
            "Kalshi private key file does not look like PEM. "
            "It should start with '-----BEGIN ... PRIVATE KEY-----'."
        )

    # Normalize & clean payload between BEGIN/END
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    begin = lines[0]
    end = lines[-1]
    middle = "".join(lines[1:-1])

    # Remove all whitespace inside the base64 payload (spaces/newlines/tabs)
    middle = re.sub(r"\s+", "", middle)

    # Rewrap at 64 chars/line
    wrapped = "\n".join(middle[i:i + 64] for i in range(0, len(middle), 64))

    return f"{begin}\n{wrapped}\n{end}\n"


def require_kalshi_env() -> None:
    """
    Call once at startup if PAPER_TRADING=False.
    """
    missing = []
    if not KALSHI_API_KEY_ID:
        missing.append("KALSHI_API_KEY_ID")
    if not KALSHI_PRIVATE_KEY_PATH:
        missing.append("KALSHI_PRIVATE_KEY_PATH")
    if missing:
        raise RuntimeError("Missing required env vars: " + ", ".join(missing))


# ============================================================
# MODE
# ============================================================

PAPER_TRADING = True
USE_MARKET_CACHE_ON_FAIL = True


# ============================================================
# MARKET UNIVERSE
# ============================================================

ASSETS = ["BTC", "ETH", "SOL"]


# ============================================================
# LOOP CADENCE (API-SAFE)
# ============================================================

DISCOVERY_REFRESH_SECONDS = 15 * 60
ANCHOR_REFRESH_SECONDS = 60
QUOTE_POLL_SECONDS_NORMAL = 60
QUOTE_POLL_SECONDS_HOT = 30

EXEC_LOOP_SECONDS_FLAT = 30
EXEC_LOOP_SECONDS_HOLD = 15


# ============================================================
# ROLLING WINDOW (BELIEF SPEED)
# ============================================================

ROLLING_WINDOW_MINUTES = 10
ROLLING_SAMPLE_SECONDS = 60


# ============================================================
# MVP STRATEGY THRESHOLDS
# ============================================================

P_EXTREME_HIGH = 0.70
P_EXTREME_LOW = 0.30

MIN_TIME_REMAINING_MIN = 25
BELIEF_SPEED_MIN_MOVE = 0.10  # 10¢ move over rolling window

MAX_SPREAD_ALLOWED = 0.15

ANCHOR_STRENGTH_MAX = 1.00
ANCHOR_STRENGTH_BREAK = 1.30


# ============================================================
# EXITS
# ============================================================

PROFIT_TAKE = 0.12
STOP_LOSS_MOVE = 0.10
EXIT_LAST_N_MINUTES = 15


# ============================================================
# RISK / SIZING
# ============================================================

BANKROLL_USD = float(os.getenv("BANKROLL_USD", "10000"))
RISK_PER_TRADE = 0.015
MAX_POSITIONS_PER_ASSET = 1


# ============================================================
# RATE LIMIT / SAFETY
# ============================================================

HTTP_TIMEOUT_SECONDS = 10
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.5


# ============================================================
# UTIL
# ============================================================

def now_ts_ms() -> int:
    """Kalshi auth timestamps are in milliseconds in their examples. :contentReference[oaicite:10]{index=10}"""
    return int(time.time() * 1000)
