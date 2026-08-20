from playwright.sync_api import sync_playwright
import time, json

def test_bitbo():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()
        
        print("Navigating to bitbo.io...")
        page.goto('https://bitbo.io/', timeout=60000, wait_until='networkidle')
        print("Page loaded, waiting for data...")
        time.sleep(5)
        
        # 截图
        page.screenshot(path='D:/AIDev/btc_daily_report/bitbo.png', full_page=True)
        print("Screenshot saved")
        
        # 尝试从页面JS变量提取数据
        print("\n=== Extracting data from page JS ===")
        try:
            # 查找所有全局变量中的数组/对象数据
            data = page.evaluate("""
                () => {
                    const results = {};
                    // 检查window上的常见数据变量
                    const keys = Object.keys(window).filter(k => 
                        /data|chart|mvrv|price|market|realized|metric/i.test(k)
                    );
                    results.windowKeys = keys.slice(0, 30);
                    
                    // 尝试找Highcharts/Chart.js/ECharts实例
                    if (window.Highcharts) {
                        results.hasHighcharts = true;
                        results.highchartsCount = window.Highcharts.charts ? window.Highcharts.charts.length : 0;
                    }
                    
                    // 查找所有canvas/svg图表
                    const canvases = document.querySelectorAll('canvas');
                    results.canvasCount = canvases.length;
                    
                    return results;
                }
            """)
            print(json.dumps(data, indent=2, default=str)[:2000])
        except Exception as e:
            print(f"Error: {e}")
        
        # 获取页面文本中的MVRV相关数据
        print("\n=== MVRV data from page text ===")
        text = page.inner_text('body')
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for i, line in enumerate(lines):
            if any(kw in line.lower() for kw in ['mvrv', 'realized', 'market cap']):
                start = max(0, i-1)
                end = min(len(lines), i+3)
                print(f"  --- Line {i} ---")
                for j in range(start, end):
                    print(f"    [{j}] {lines[j]}")
        
        browser.close()

if __name__ == '__main__':
    test_bitbo()
