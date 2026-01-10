# test_public_market.py
from kalshi_client import KalshiClient

c = KalshiClient(auth=None)
m = c.get_market("KXBTC15M-26JAN061930-30")  # use any real ticker from your cache
print("OK keys:", m.keys())
print("title:", m.get("title"))
