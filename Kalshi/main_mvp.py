# main_mvp.py
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
import json

import config
from kalshi_client import KalshiClient
from logger import append_excel

import anchor as anchor_mod
import quotes as quote_mod
import signals as signal_mod
import execution as exec_mod


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def main():
    quote_client = KalshiClient(auth=None)

    # Force first-run immediately
    last_anchor = 0.0
    last_quotes = 0.0
    last_signal = 0.0
    last_state_log = 0.0

    print("[MVP] starting loop...", utcnow_iso())
    print(f"[MVP] PAPER_TRADING={config.PAPER_TRADING} | KALSHI_ENV={config.KALSHI_ENV}")

    while True:
        now = time.time()

        # 1) Anchor (every 60s)
        if now - last_anchor >= config.ANCHOR_REFRESH_SECONDS:
            try:
                anchor_out = anchor_mod.run_once()
                last_anchor = now
            except Exception as e:
                print(f"[MVP] anchor error: {e}")

        # 2) Quotes (every 60s)
        if now - last_quotes >= config.QUOTE_POLL_SECONDS_NORMAL:
            try:
                quote_out = quote_mod.run_once(quote_client)
                last_quotes = now
            except Exception as e:
                print(f"[MVP] quotes error: {e}")

        # 3) Signals (every 60s)
        if now - last_signal >= config.QUOTE_POLL_SECONDS_NORMAL:
            try:
                sig_out = signal_mod.run_once()
                last_signal = now
                if sig_out.get("signals"):
                    print(f"[MVP] signals found: {len(sig_out['signals'])}")
            except Exception as e:
                print(f"[MVP] signal error: {e}")

        # 4) Execution / mgmt (every loop)
        try:
            exec_mod.run_once(exec_mod.get_client())
        except Exception as e:
            print(f"[MVP] exec error: {e}")

        # 5) State logging once per minute (Excel)
        if now - last_state_log >= 60:
            ts = utcnow_iso()

            a = read_json(Path(config.ANCHOR_FILE)) or {"assets": {}}
            rq = read_json(Path(config.ROLLING_QUOTES_FILE)) or {"markets": {}}
            sg = read_json(Path(config.SIGNAL_FILE)) or {"signals": [], "debug": {}}
            ps = read_json(Path(config.POSITIONS_FILE)) or {"positions": {}}

            assets = a.get("assets") or {}
            row = {
                "ts_utc": ts,
                "paper": config.PAPER_TRADING,
                "rolling_markets": len(rq.get("markets") or {}),
                "signals_now": len(sg.get("signals") or []),
                "positions_open": len((ps.get("positions") or {})),
                "evaluated": (sg.get("debug") or {}).get("evaluated", ""),
                "passed_assets": (sg.get("debug") or {}).get("passed", ""),
                "anchor_BTC": (assets.get("BTC") or {}).get("anchor_strength", ""),
                "anchor_ETH": (assets.get("ETH") or {}).get("anchor_strength", ""),
                "anchor_SOL": (assets.get("SOL") or {}).get("anchor_strength", ""),
            }

            append_excel(config.STATE_CSV, sheet="state", row=row)
            last_state_log = now

        time.sleep(5)


if __name__ == "__main__":
    main()
