# test_keys.py
# ============================================================
# Safe env/key verification for Kalshi + Alpaca
# - Confirms env vars are loaded
# - Confirms Kalshi private key file is readable + PEM-formatted
# - Does NOT print the full private key
# ============================================================

import os
from pathlib import Path

import config


def mask(s: str, keep: int = 4) -> str:
    if not s:
        return ""
    if len(s) <= keep:
        return s + "****"
    return s[:keep] + "****"


def main():
    print("=== ENV CHECK ===")
    print("KALSHI_ENV:", getattr(config, "KALSHI_ENV", None))
    print("KALSHI_API_KEY_ID present:", bool(config.KALSHI_API_KEY_ID))
    if config.KALSHI_API_KEY_ID:
        print("KALSHI_API_KEY_ID preview:", mask(config.KALSHI_API_KEY_ID))

    print("KALSHI_PRIVATE_KEY_PATH:", config.KALSHI_PRIVATE_KEY_PATH)
    print("ALPACA_API_KEY present:", bool(getattr(config, "ALPACA_API_KEY", None)))
    print("ALPACA_API_SECRET present:", bool(getattr(config, "ALPACA_API_SECRET", None)))

    print("\n=== PRIVATE KEY FILE CHECK ===")
    if not config.KALSHI_PRIVATE_KEY_PATH:
        raise RuntimeError("Missing env var: KALSHI_PRIVATE_KEY_PATH")

    key_path = Path(config.KALSHI_PRIVATE_KEY_PATH)

    print("File exists:", key_path.exists())
    print("File path:", str(key_path))

    if not key_path.exists():
        raise RuntimeError(f"Private key file not found: {key_path}")

    # Load via your config helper (this is the same path live trading uses)
    pem = config.load_kalshi_private_key()

    lines = pem.splitlines()
    print("Loaded PEM: True")
    print("Starts with BEGIN:", pem.startswith("-----BEGIN"))
    print("Ends with END:", pem.strip().endswith("-----END PRIVATE KEY-----") or pem.strip().endswith("-----END RSA PRIVATE KEY-----"))
    print("Line count:", len(lines))
    print("First line:", lines[0] if lines else "<empty>")
    print("Last line:", lines[-1] if lines else "<empty>")
    print("PEM length (chars):", len(pem))

    print("\n✅ Looks good. If PAPER_TRADING=False, live auth should be able to sign requests.")


if __name__ == "__main__":
    main()
