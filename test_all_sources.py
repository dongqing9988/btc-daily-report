import requests, json
from bs4 import BeautifulSoup

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# 1. Binance - BTC价格+24h涨跌幅
print("=== 1. Binance Price ===")
try:
    r = requests.get('https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT', headers=headers, timeout=15)
    d = r.json()
    print(f"Price: ${float(d['lastPrice']):,.2f}")
    print(f"24h Change: {float(d['priceChangePercent']):.2f}%")
except Exception as e:
    print(f"Error: {e}")

# 2. 恐慌贪婪指数
print("\n=== 2. Fear & Greed Index ===")
try:
    r = requests.get('https://api.alternative.me/fng/?limit=1', headers=headers, timeout=15)
    d = r.json()
    item = d['data'][0]
    print(f"Value: {item['value']} ({item['value_classification']})")
except Exception as e:
    print(f"Error: {e}")

# 3. Binance永续合约资金费率
print("\n=== 3. Funding Rate ===")
try:
    r = requests.get('https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT', headers=headers, timeout=15)
    d = r.json()
    print(f"Funding Rate: {float(d['lastFundingRate'])*100:.4f}%")
    print(f"Next Funding: {d['nextFundingTime']}")
except Exception as e:
    print(f"Error: {e}")

# 4. ETF资金流 - farside.co.uk
print("\n=== 4. BTC ETF Flows (farside.co.uk) ===")
try:
    r = requests.get('https://farside.co.uk/btc/', headers=headers, timeout=20)
    print(f"Status: {r.status_code}")
    soup = BeautifulSoup(r.text, 'lxml')
    # 找表格
    tables = soup.find_all('table')
    print(f"Found {len(tables)} tables")
    # 打印第一个表格的前几行
    if tables:
        rows = tables[0].find_all('tr')
        print(f"First table has {len(rows)} rows")
        for row in rows[:5]:
            cells = [c.get_text(strip=True) for c in row.find_all(['th', 'td'])]
            print(f"  {cells}")
except Exception as e:
    print(f"Error: {e}")

# 5. bitbo.io MVRV
print("\n=== 5. MVRV from bitbo.io ===")
try:
    r = requests.get('https://bitbo.io/', headers=headers, timeout=20)
    soup = BeautifulSoup(r.text, 'lxml')
    text = soup.get_text(separator='\n')
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for i, line in enumerate(lines):
        if line == 'MVRV' and i+1 < len(lines):
            print(f"MVRV: {lines[i+1]}")
        if 'Realized Price' in line and i+2 < len(lines):
            # 找数值
            for j in range(i, min(i+5, len(lines))):
                if lines[j].startswith('$') and ',' in lines[j]:
                    print(f"Realized Price: {lines[j]}")
                    break
except Exception as e:
    print(f"Error: {e}")
