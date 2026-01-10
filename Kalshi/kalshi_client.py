# kalshi_client.py
# ============================================================
# Minimal Kalshi client:
# - Public market data: GET /trade-api/v2/markets/{ticker}
# - Signed trading requests (RSA-PSS): portfolio endpoints
#
# Signing spec:
# message = timestamp_ms + HTTP_METHOD + path_without_query
# signature = RSA-PSS SHA256, base64
# :contentReference[oaicite:1]{index=1}
# ============================================================

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend


PUBLIC_BASE_URL = "https://api.elections.kalshi.com"  # public market data server :contentReference[oaicite:2]{index=2}


@dataclass
class KalshiAuth:
    api_key_id: str
    private_key_pem: str
    base_url: str  # demo/prod trading host (demo-api.kalshi.co / api.kalshi.com)

    def _load_private_key(self):
        return serialization.load_pem_private_key(
            self.private_key_pem.encode("utf-8"),
            password=None,
            backend=default_backend(),
        )

    def _timestamp_ms(self) -> str:
        return str(int(time.time() * 1000))

    def _sign(self, private_key, timestamp_ms: str, method: str, path: str) -> str:
        path_wo_query = path.split("?")[0]
        message = f"{timestamp_ms}{method.upper()}{path_wo_query}".encode("utf-8")
        sig = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode("utf-8")

    def headers(self, method: str, path: str) -> dict:
        import time
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        method = method.upper()

        # IMPORTANT: Kalshi commonly expects ms timestamps
        ts = str(int(time.time() * 1000))

        pk = self._load_private_key()

        # String to sign (typical Kalshi pattern): timestamp + method + path
        message = (ts + method + path).encode("utf-8")

        signature = pk.sign(
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(signature).decode("utf-8")

        headers = {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": sig_b64,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "Content-Type": "application/json",
        }

        # DEBUG (safe): do NOT print signature
        print("[AUTH DEBUG]", {
            "method": method,
            "path": path,
            "ts": ts,
            "api_key_id_preview": (self.api_key_id[:4] + "****") if self.api_key_id else None,
            "base_url": self.base_url,
        })
        print("[AUTH DEBUG] header keys:", sorted(headers.keys()))

        return headers

class KalshiClient:
    def __init__(self, auth: Optional[KalshiAuth] = None, timeout: int = 10):
        self.auth = auth
        self.timeout = timeout

    # -------------------------
    # Public market data
    # -------------------------
    def get_market(self, market_ticker: str) -> Dict[str, Any]:
        path = f"/trade-api/v2/markets/{market_ticker}"
        url = PUBLIC_BASE_URL + path
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # -------------------------
    # Authenticated portfolio
    # -------------------------
    def _authed(self, method: str, path: str, payload: Optional[dict] = None) -> Dict[str, Any]:
        if not self.auth:
            raise RuntimeError("KalshiClient: auth not configured")

        url = self.auth.base_url + path
        headers = self.auth.headers(method, path)
        data = json.dumps(payload) if payload is not None else None

        r = requests.request(method, url, headers=headers, data=data, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_balance(self) -> Dict[str, Any]:
        return self._authed("GET", "/trade-api/v2/portfolio/balance")

    def get_positions(self) -> Dict[str, Any]:
        return self._authed("GET", "/trade-api/v2/portfolio/positions")

    def create_order(self, market_ticker: str, side: str, action: str, count: int, price: float) -> Dict[str, Any]:
        """
        Minimal order shape.
        Kalshi Create Order endpoint is POST /trade-api/v2/portfolio/orders :contentReference[oaicite:3]{index=3}
        side: "yes" or "no"
        action: "buy" or "sell"
        price: dollars (0.01–0.99)
        """
        payload = {
            "ticker": market_ticker,
            "action": action,
            "side": side,
            "count": int(count),
            "price": float(price),
        }
        return self._authed("POST", "/trade-api/v2/portfolio/orders", payload=payload)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return self._authed("DELETE", f"/trade-api/v2/portfolio/orders/{order_id}")
