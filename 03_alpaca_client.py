"""
03 - Alpaca Client
Real-time BTC price data from Alpaca
"""

import requests
import os
from datetime import datetime, timedelta

ALPACA_API_KEY = os.getenv('APCA_API_KEY_ID')
ALPACA_SECRET_KEY = os.getenv('APCA_API_SECRET_KEY')
ALPACA_BASE_URL = "https://data.alpaca.markets"

class AlpacaClient:
    """Fetch BTC price data"""
    
    def __init__(self):
        self.base_url = ALPACA_BASE_URL
        self.headers = {
            'APCA-API-KEY-ID': ALPACA_API_KEY,
            'APCA-API-SECRET-KEY': ALPACA_SECRET_KEY
        }
        self.price_cache = []
        self.last_update = None
        
        print("✅ [03] Alpaca client initialized")
    
    def get_current_price(self):
        """Get latest BTC price"""
        endpoint = f"{self.base_url}/v1beta3/crypto/us/latest/bars"
        params = {'symbols': 'BTC/USD'}
        
        try:
            response = requests.get(endpoint, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            if 'bars' in data and 'BTC/USD' in data['bars']:
                bar = data['bars']['BTC/USD']
                
                price_data = {
                    'price': bar['c'],
                    'timestamp': datetime.fromisoformat(bar['t'].replace('Z', '+00:00')),
                    'open': bar['o'],
                    'high': bar['h'],
                    'low': bar['l'],
                    'volume': bar['v']
                }
                
                self.price_cache.append(price_data)
                if len(self.price_cache) > 100:
                    self.price_cache.pop(0)
                
                self.last_update = datetime.utcnow()
                return price_data
            
            return None
        except Exception as e:
            print(f"❌ [03] Error: {e}")
            return None
    
    def get_historical_bars(self, timeframe='1Min', limit=60):
        """Get historical bars"""
        end = datetime.utcnow()
        
        if timeframe == '1Min':
            start = end - timedelta(minutes=limit + 5)
        elif timeframe == '5Min':
            start = end - timedelta(minutes=(limit * 5) + 10)
        elif timeframe == '15Min':
            start = end - timedelta(minutes=(limit * 15) + 30)
        else:
            start = end - timedelta(hours=limit + 1)
        
        endpoint = f"{self.base_url}/v1beta3/crypto/us/bars"
        params = {
            'symbols': 'BTC/USD',
            'timeframe': timeframe,
            'start': start.isoformat() + 'Z',
            'end': end.isoformat() + 'Z',
            'limit': limit
        }
        
        try:
            response = requests.get(endpoint, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            if 'bars' not in data or 'BTC/USD' not in data['bars']:
                return []
            
            bars = []
            for bar in data['bars']['BTC/USD']:
                bars.append({
                    'timestamp': datetime.fromisoformat(bar['t'].replace('Z', '+00:00')),
                    'open': bar['o'],
                    'high': bar['h'],
                    'low': bar['l'],
                    'close': bar['c'],
                    'volume': bar['v']
                })
            
            return bars
        except Exception as e:
            print(f"❌ [03] Error: {e}")
            return []
    
    def get_price_series(self, timeframe='1Min', limit=60):
        """Get closing prices only"""
        bars = self.get_historical_bars(timeframe=timeframe, limit=limit)
        return [bar['close'] for bar in bars]


print("✅ [03] Alpaca client loaded")


# Testing
if __name__ == "__main__":
    print("\n🧪 Testing [03] Alpaca Client\n")
    
    client = AlpacaClient()
    
    current = client.get_current_price()
    if current:
        print(f"✅ BTC Price: ${current['price']:,.2f}")
    
    bars = client.get_historical_bars(timeframe='1Min', limit=30)
    if bars:
        print(f"✅ Got {len(bars)} bars")
    
    print("\n✅ [03] Tests complete")