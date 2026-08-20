from playwright.sync_api import sync_playwright
import time, json

def intercept_bitbo():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        )
        page = context.new_page()
        
        # 收集所有API请求
        api_requests = []
        
        def on_request(request):
            url = request.url
            if any(kw in url.lower() for kw in ['api', 'data', 'json', 'metric', 'chart', 'mvrv', 'price', 'market']):
                if not url.endswith(('.png', '.jpg', '.css', '.js', '.svg', '.woff', '.woff2', '.ico')):
                    api_requests.append({
                        'url': url,
                        'method': request.method,
                        'resource_type': request.resource_type
                    })
        
        page.on('request', on_request)
        
        print("Navigating to bitbo.io...")
        page.goto('https://bitbo.io/', timeout=60000, wait_until='networkidle')
        time.sleep(8)
        
        print(f"\n=== Found {len(api_requests)} API requests ===")
        for req in api_requests[:30]:
            print(f"  [{req['method']}] {req['url'][:120]}")
        
        # 对看起来像数据API的请求，尝试获取响应
        print("\n=== Trying to fetch data from potential API endpoints ===")
        for req in api_requests:
            url = req['url']
            if any(kw in url.lower() for kw in ['api', 'data', 'json']) and 'bitbo' in url:
                try:
                    response = page.request.get(url, timeout=10000)
                    if response.ok:
                        ct = response.headers.get('content-type', '')
                        if 'json' in ct:
                            data = response.json()
                            print(f"\n  URL: {url[:100]}")
                            if isinstance(data, list):
                                print(f"    Type: list, length: {len(data)}")
                                if data:
                                    print(f"    First item: {json.dumps(data[0])[:200]}")
                                    print(f"    Last item: {json.dumps(data[-1])[:200]}")
                            elif isinstance(data, dict):
                                print(f"    Type: dict, keys: {list(data.keys())[:10]}")
                                print(f"    Content: {json.dumps(data)[:300]}")
                except Exception as e:
                    pass
        
        browser.close()

if __name__ == '__main__':
    intercept_bitbo()
