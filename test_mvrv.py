import requests, json, re

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# 1. 查看Dash layout结构
print("=== Dash Layout Structure ===")
url = 'https://www.lookintobitcoin.com/django_plotly_dash/app/mvrv_zscore/_dash-layout'
r = requests.get(url, headers=headers, timeout=15)
d = r.json()
print("Top keys:", list(d.keys()))
print("type:", d.get('type'))
print("namespace:", d.get('namespace'))
props = d.get('props', {})
print("props keys:", list(props.keys()))

def walk(obj, depth=0, max_depth=4):
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        if 'type' in obj and 'id' in obj:
            print("  " * depth + f"id={obj['id']}, type={obj['type']}")
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                walk(v, depth+1, max_depth)
    elif isinstance(obj, list):
        for item in obj:
            walk(item, depth, max_depth)

print("\n=== Components ===")
walk(d)

# 2. 试试Dash依赖端点获取回调配置
print("\n=== Dash Dependencies ===")
url2 = 'https://www.lookintobitcoin.com/django_plotly_dash/app/mvrv_zscore/_dash-dependencies'
r2 = requests.get(url2, headers=headers, timeout=15)
print("Status:", r2.status_code)
if r2.status_code == 200:
    deps = r2.json()
    print(json.dumps(deps, indent=2)[:2000])
