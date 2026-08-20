import requests, json

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Content-Type': 'application/json',
}

base = 'https://www.lookintobitcoin.com/django_plotly_dash/app/mvrv_zscore'

# 构造Dash回调请求
payload = {
    "output": "chart.figure",
    "outputs": {"id": "chart", "property": "figure"},
    "inputs": [
        {"id": "url", "property": "pathname", "value": "/charts/mvrv-zscore/"},
        {"id": "display", "property": "children", "value": ""}
    ],
    "changedPropIds": ["url.pathname"],
    "state": []
}

r = requests.post(f'{base}/_dash-update-component', json=payload, headers=headers, timeout=20)
print("Status:", r.status_code)
if r.status_code == 200:
    data = r.json()
    print("Response keys:", list(data.keys()))
    if 'response' in data:
        resp = data['response']
        print("Response keys:", list(resp.keys()))
        if 'chart' in resp:
            chart = resp['chart']
            print("Chart keys:", list(chart.keys()))
            if 'figure' in chart:
                fig = chart['figure']
                print("Figure keys:", list(fig.keys()))
                if 'data' in fig:
                    traces = fig['data']
                    print("Traces count:", len(traces))
                    for i, t in enumerate(traces):
                        print(f"  Trace[{i}]: name={t.get('name')}, x_len={len(t.get('x',[]))}, y_len={len(t.get('y',[]))}")
                        if t.get('x'):
                            print(f"    last x: {t['x'][-1]}")
                        if t.get('y'):
                            print(f"    last y: {t['y'][-1]}")
else:
    print(r.text[:1000])
