import requests, re, json
from bs4 import BeautifulSoup

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
r = requests.get('https://bitbo.io/', headers=headers, timeout=20)

# 检查所有script标签里的JSON数据
soup = BeautifulSoup(r.text, 'lxml')
print("=== Script tags with potential data ===")
for i, s in enumerate(soup.find_all('script')):
    content = s.string or s.get_text() or ''
    if len(content) > 100:
        # 检查是否包含MVRV或realized相关数据
        if any(kw in content.lower() for kw in ['mvrv', 'realized', 'zscore', 'z-score']):
            print(f"\nScript[{i}] length={len(content)}, contains MVRV data!")
            # 尝试找JSON数组
            arrays = re.findall(r'\[\s*\{[^}]*"t"\s*:[^}]*\}[^]]*\]', content)
            print(f"  Found {len(arrays)} potential data arrays")
            if arrays:
                print(f"  First array preview: {arrays[0][:300]}")
        # 也检查大的JSON对象
        if 'price' in content.lower() and len(content) > 5000:
            print(f"\nScript[{i}] length={len(content)}, contains price data")

# 检查HTML注释或data属性
print("\n=== Checking for inline JSON in HTML ===")
# 找所有包含大括号的元素
for tag in soup.find_all(attrs={'data-*': True}):
    pass

# 直接搜索HTML中的MVRV数值模式
print("\n=== MVRV values in HTML ===")
mvrv_patterns = re.findall(r'(?:mvrv|MVRV)[^0-9-]{0,20}([-\d.]+)', r.text)
print(f"Found MVRV values: {mvrv_patterns}")

# 搜索日期+数值模式（可能是历史数据）
date_value_patterns = re.findall(r'\{[^{}]*"t"\s*:\s*\d+[^{}]*"[a-z]+"\s*:\s*[-\d.]+[^{}]*\}', r.text)
print(f"\nFound {len(date_value_patterns)} date-value objects in HTML")
if date_value_patterns:
    print(f"First: {date_value_patterns[0][:200]}")
    print(f"Last: {date_value_patterns[-1][:200]}")
