from playwright.sync_api import sync_playwright
import time, json, re

def test_lookintobitcoin():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()
        
        print("Navigating to LookIntoBitcoin MVRV Z-Score...")
        page.goto('https://www.lookintobitcoin.com/charts/mvrv-zscore/', timeout=60000)
        print("Waiting for page load...")
        time.sleep(8)  # 等Dash图表加载
        
        # 截图看看页面状态
        page.screenshot(path='D:/AIDev/btc_daily_report/lookintobitcoin.png', full_page=True)
        print("Screenshot saved")
        
        # 获取页面文本
        text = page.inner_text('body')
        print("\n=== Page text (MVRV related lines) ===")
        for line in text.split('\n'):
            line = line.strip()
            if line and any(kw in line.lower() for kw in ['mvrv', 'z-score', 'zscore', 'bottom', 'overvalued', 'undervalued']):
                print(f"  {line}")
        
        # 尝试从Plotly图表对象提取数据
        print("\n=== Trying to extract chart data via JS ===")
        try:
            # 查找所有Plotly图表
            chart_data = page.evaluate("""
                () => {
                    const results = [];
                    // 查找所有plotly图表
                    document.querySelectorAll('.js-plotly-plot, .plotly').forEach(el => {
                        if (window.Plotly && el.data) {
                            results.push({
                                id: el.id,
                                dataLength: el.data ? el.data.length : 0,
                                traces: el.data ? el.data.map(t => ({
                                    name: t.name,
                                    xLen: t.x ? t.x.length : 0,
                                    yLen: t.y ? t.y.length : 0,
                                    lastX: t.x && t.x.length ? t.x[t.x.length-1] : null,
                                    lastY: t.y && t.y.length ? t.y[t.y.length-1] : null
                                })) : []
                            });
                        }
                    });
                    return results;
                }
            """)
            print(json.dumps(chart_data, indent=2, default=str)[:2000])
        except Exception as e:
            print(f"JS eval error: {e}")
        
        # 尝试找页面上显示的数值
        print("\n=== Looking for numeric values near MVRV labels ===")
        try:
            values = page.evaluate("""
                () => {
                    const results = [];
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    let node;
                    while (node = walker.nextNode()) {
                        const text = node.textContent.trim();
                        if (text && /mvrv|z-score/i.test(text)) {
                            // 获取这个元素的父元素和相邻元素的文本
                            const parent = node.parentElement;
                            if (parent) {
                                const parentText = parent.innerText;
                                results.push({label: text, context: parentText.substring(0, 200)});
                            }
                        }
                    }
                    return results;
                }
            """)
            print(json.dumps(values, indent=2, ensure_ascii=False)[:2000])
        except Exception as e:
            print(f"Error: {e}")
        
        browser.close()

if __name__ == '__main__':
    test_lookintobitcoin()
