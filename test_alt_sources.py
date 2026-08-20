import requests, json

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# 1. CoinGecko - BTC价格
print("=== CoinGecko Price ===")
try:
    r = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true', headers=headers, timeout=15)
    d = r.json()
    print(f"Price: ${d['bitcoin']['usd']:,.2f}")
    print(f"24h Change: {d['bitcoin']['usd_24h_change']:.2f}%")
except Exception as e:
    print(f"Error: {e}")

# 2. OKX - 资金费率
print("\n=== OKX Funding Rate ===")
try:
    r = requests.get('https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP', headers=headers, timeout=15)
    d = r.json()
    if d.get('code') == '0' and d.get('data'):
        item = d['data'][0]
        print(f"Funding Rate: {float(item['fundingRate'])*100:.4f}%")
        print(f"Next Funding: {item['nextFundingTime']}")
    else:
        print(f"Response: {json.dumps(d)[:300]}")
except Exception as e:
    print(f"Error: {e}")

# 3. bitbo.io price-history API - 最新价格
print("\n=== bitbo.io Price ===")
try:
    r = requests.get('https://api.bitbo.io/price-history?interval=5_min&limit=2', headers=headers, timeout=15)
    d = r.json()
    data = d.get('5_min', [])
    if data:
        latest = data[-1]
        from datetime import datetime
        ts = latest['t']
        print(f"Price: ${latest['p']:,.2f}")
        print(f"Time: {datetime.fromtimestamp(ts)}")
except Exception as e:
    print(f"Error: {e}")

# 4. OKX BTC价格
print("\n=== OKX Price ===")
try:
    r = requests.get('https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT', headers=headers, timeout=15)
    d = r.json()
    if d.get('code') == '0' and d.get('data'):
        item = d['data'][0]
        print(f"Price: ${float(item['last']):,.2f}")
        print(f"24h Change: {float(item['last'])/float(item['open24h'])*100-100:.2f}%")
except Exception as e:
    print(f"Error: {e}")
