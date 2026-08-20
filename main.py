#!/usr/bin/env python3
"""
BTC 每日数据自动采集 + Telegram 推送
涵盖：价格、链上估值、宏观流动性、资金流、合约市场、情绪技术、信号预警
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple

import requests
from bs4 import BeautifulSoup

# ============ 配置 ============
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
FRED_API_KEY = os.environ.get('FRED_API_KEY', '')

TIMEOUT = 20
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

# 信号阈值
THRESHOLDS = {
    'mvrv_deep_bottom': 1.0,
    'mvrv_low': 1.5,
    'mvrv_top': 3.5,
    'tips_tight': 2.0,      # TIPS实际收益率>2%强紧缩
    'tips_loose': 1.0,      # <1%宽松
    'vix_high': 30,
    'vix_low': 15,
    'nupl_bottom': 0.0,
    'nupl_top': 0.75,
    'etf_consecutive': 3,
    'etf_daily_inflow_m': 200,
    'etf_daily_outflow_m': -200,
    'funding_high': 0.05,
    'funding_low': -0.05,
    'long_short_high': 1.5,
    'long_short_low': 0.7,
    'fear_extreme': 25,
    'greed_extreme': 75,
    'deviation_overbought': 30,   # 偏离200周均线+30%超买
    'deviation_oversold': -10,     # -10%超卖
}


# ============ 工具函数 ============

def safe_float(s, default=None):
    """安全转float，处理括号负数、逗号、破折号"""
    if s is None:
        return default
    s = str(s).strip().replace(',', '')
    if s in ('', '-', '—', 'N/A', 'NA'):
        return default
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return default


# ============ 1. BTC价格 ============

def fetch_price() -> Dict[str, Any]:
    result = {'price': None, 'change_24h': None, 'source': None}
    # Binance
    try:
        r = requests.get('https://api.binance.com/api/v3/ticker/24hr',
                         params={'symbol': 'BTCUSDT'}, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            d = r.json()
            result['price'] = float(d['lastPrice'])
            result['change_24h'] = float(d['priceChangePercent'])
            result['source'] = 'Binance'
            logger.info(f"价格(Binance): ${result['price']:,.2f} ({result['change_24h']:+.2f}%)")
            return result
    except Exception as e:
        logger.warning(f"Binance价格失败: {e}")
    # bitbo.io
    try:
        r = requests.get('https://api.bitbo.io/price-history',
                         params={'interval': '5_min', 'limit': 288}, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json().get('5_min', [])
            if len(data) >= 2:
                result['price'] = float(data[-1]['p'])
                result['change_24h'] = (float(data[-1]['p']) / float(data[0]['p']) - 1) * 100
                result['source'] = 'bitbo.io'
                logger.info(f"价格(bitbo): ${result['price']:,.2f} ({result['change_24h']:+.2f}%)")
                return result
    except Exception as e:
        logger.warning(f"bitbo价格失败: {e}")
    return result


# ============ 2. 恐慌贪婪指数 ============

def fetch_fear_greed() -> Dict[str, Any]:
    result = {'value': None, 'classification': None}
    try:
        r = requests.get('https://api.alternative.me/fng/', params={'limit': 1},
                         headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            item = r.json()['data'][0]
            result['value'] = int(item['value'])
            result['classification'] = item['value_classification']
            logger.info(f"恐慌贪婪: {result['value']} ({result['classification']})")
    except Exception as e:
        logger.warning(f"恐慌贪婪失败: {e}")
    return result


# ============ 3. MVRV + 已实现价格 + 市值 ============

def fetch_mvrv() -> Dict[str, Any]:
    result = {'mvrv': None, 'realized_price': None, 'market_cap': None,
              'realized_cap': None, 'zone': None, 'zone_label': None}
    try:
        r = requests.get('https://bitbo.io/', headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return result
        soup = BeautifulSoup(r.text, 'lxml')
        lines = [l.strip() for l in soup.get_text(separator='\n').split('\n') if l.strip()]

        for i, line in enumerate(lines):
            if line == 'MVRV' and i + 1 < len(lines):
                result['mvrv'] = safe_float(lines[i + 1])
            if line == 'Realized Price' and i + 3 < len(lines):
                for j in range(i + 1, min(i + 5, len(lines))):
                    val = safe_float(lines[j].replace('$', ''))
                    if val is not None:
                        result['realized_price'] = val
                        break
            if line == 'Market Cap' and i + 1 < len(lines):
                # 格式可能是 "1.38" 下一行 "T"
                val = safe_float(lines[i + 1])
                if val is not None and i + 2 < len(lines) and lines[i + 2] == 'T':
                    result['market_cap'] = val * 1e12  # 万亿
                elif val is not None:
                    result['market_cap'] = val

        # MVRV区间判断
        if result['mvrv'] is not None:
            m = result['mvrv']
            if m < 1.0:
                result['zone'], result['zone_label'] = 'deep_bottom', '深度底部'
            elif m < 1.5:
                result['zone'], result['zone_label'] = 'low', '偏低'
            elif m < 2.5:
                result['zone'], result['zone_label'] = 'neutral', '中性'
            elif m < 3.5:
                result['zone'], result['zone_label'] = 'high', '偏高'
            else:
                result['zone'], result['zone_label'] = 'top', '顶部'

        # 估算已实现市值 = 已实现价格 × 流通量（从market_cap/price推算流通量）
        if result['realized_price'] and result['market_cap'] and result.get('mvrv'):
            # realized_cap = market_cap / mvrv
            result['realized_cap'] = result['market_cap'] / result['mvrv']

        logger.info(f"MVRV: {result['mvrv']} ({result['zone_label']}), RP: ${result['realized_price']:,.0f}" if result['mvrv'] else "MVRV失败")
    except Exception as e:
        logger.warning(f"MVRV失败: {e}")
    return result


# ============ 4. NUPL计算（从已有数据） ============

def calculate_nupl(mvrv_data) -> Optional[float]:
    """NUPL = (市值 - 已实现市值) / 市值 = 1 - 1/MVRV"""
    if mvrv_data.get('mvrv') and mvrv_data['mvrv'] > 0:
        nupl = 1 - 1 / mvrv_data['mvrv']
        logger.info(f"NUPL: {nupl:.4f} ({nupl*100:.1f}%)")
        return nupl
    return None


# ============ 5. ETF资金流（增强反爬） ============

def fetch_etf_flows() -> Dict[str, Any]:
    result = {'daily_flow_m': None, 'consecutive_days': 0, 'cumulative_m': None,
              'latest_date': None, 'recent_flows': [], 'source': None, 'error': None}

    def parse_farside_table(html):
        soup = BeautifulSoup(html, 'lxml')
        tables = soup.find_all('table')
        if not tables:
            return []
        daily_data = []
        for table in tables:
            for row in table.find_all('tr'):
                cells = [c.get_text(strip=True) for c in row.find_all(['th', 'td'])]
                if len(cells) < 14:
                    continue
                date_str = cells[0]
                if not any(m in date_str for m in ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']):
                    continue
                total = safe_float(cells[-1])
                if total is not None:
                    daily_data.append((date_str, total))
        return daily_data

    def fetch_tftc():
        """从TFTC.io获取ETF flow作为备选"""
        try:
            r = requests.get('https://www.tftc.io/bitcoin-etf-flows', headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                return None
            import re
            text = BeautifulSoup(r.text, 'lxml').get_text()
            # 找最近的日期和flow，格式如 "Monday, Aug 18, 2026 · +$226.9M"
            pattern = r'([A-Za-z]+,\s*[A-Za-z]+\s*\d+,\s*\d{4})\s*[·•]\s*([+-]?\$[\d,.]+[MB])'
            matches = re.findall(pattern, text)
            if not matches:
                # 尝试另一种格式
                pattern2 = r'([A-Z][a-z]+,\s*\d{1,2}\s*[A-Z][a-z]+,\s*\d{4})\s*([+-]?\$[\d,.]+[MB])'
                matches = re.findall(pattern2, text)
            if matches:
                # 解析所有flow
                all_flows = []
                for _, fs in matches:
                    fv = safe_float(fs.replace('$', '').replace('M', '').replace('B', ''))
                    if fv is not None:
                        if 'B' in fs:
                            fv *= 1000
                        all_flows.append(fv)
                # 最新的flow
                latest_date, flow_str = matches[0]
                flow_val = all_flows[0] if all_flows else None
                # 连续流入天数
                consecutive = 0
                for fv in all_flows:
                    if fv > 0:
                        consecutive += 1
                    else:
                        break
                # 累计（从matches中累加）
                cumulative = sum(all_flows) if all_flows else None
                return {'daily_flow_m': flow_val, 'latest_date': latest_date,
                        'consecutive_days': consecutive, 'cumulative_m': cumulative,
                        'cumulative_type': f'近{len(all_flows)}个交易日', 'source': 'TFTC'}
        except Exception as e:
            logger.warning(f"TFTC ETF获取失败: {e}")
        return None

    # 用session保持cookies
    session = requests.Session()
    session.headers.update({
        **HEADERS,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://farside.co.uk/',
    })

    # 方案1: farside /btc/ 页面（小页面，加载快）
    for attempt in range(3):
        try:
            # 先访问首页获取cookies
            session.get('https://farside.co.uk/', timeout=TIMEOUT)
            time.sleep(0.5)
            r = session.get('https://farside.co.uk/btc/', timeout=TIMEOUT)
            if r.status_code == 200:
                daily_data = parse_farside_table(r.text)
                if daily_data:
                    latest_date, latest_flow = daily_data[-1]
                    result['daily_flow_m'] = latest_flow
                    result['latest_date'] = latest_date
                    result['recent_flows'] = daily_data[-10:]
                    result['source'] = 'farside'
                    # 连续流入天数
                    consecutive = 0
                    for _, flow in reversed(daily_data[-10:]):
                        if flow > 0:
                            consecutive += 1
                        else:
                            break
                    result['consecutive_days'] = consecutive
                    # 尝试获取累计（用完整数据页）
                    try:
                        r2 = session.get('https://farside.co.uk/bitcoin-etf-flow-all-data/', timeout=TIMEOUT)
                        if r2.status_code == 200:
                            full_data = parse_farside_table(r2.text)
                            if full_data:
                                result['cumulative_m'] = sum(f for _, f in full_data)
                                result['cumulative_type'] = '全部历史'
                    except:
                        pass
                    # 如果完整页失败，用近期数据累加
                    if result['cumulative_m'] is None and daily_data:
                        result['cumulative_m'] = sum(f for _, f in daily_data)
                        result['cumulative_type'] = f'近{len(daily_data)}个交易日'
                    logger.info(f"ETF(farside): {latest_date} ${latest_flow:+.1f}M, 连续{consecutive}天, 累计${result.get('cumulative_m',0):,.0f}M")
                    return result
                else:
                    logger.warning(f"farside未解析到数据 (尝试{attempt+1}/3)")
        except Exception as e:
            logger.warning(f"farside ETF失败 (尝试{attempt+1}/3): {e}")
        if attempt < 2:
            time.sleep(2)

    # 方案2: TFTC.io备选
    logger.info("尝试TFTC.io备选...")
    tftc_result = fetch_tftc()
    if tftc_result:
        result.update(tftc_result)
        logger.info(f"ETF(TFTC): {result['latest_date']} ${result['daily_flow_m']:+.1f}M, 连续{result['consecutive_days']}天")
        return result

    result['error'] = "所有数据源均失败"
    logger.error("ETF所有数据源均失败")
    return result


# ============ 6. 资金费率 ============

def fetch_funding_rate() -> Dict[str, Any]:
    result = {'funding_rate': None, 'source': None}
    # Binance
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                         params={'symbol': 'BTCUSDT'}, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            result['funding_rate'] = float(r.json()['lastFundingRate']) * 100
            result['source'] = 'Binance'
            logger.info(f"资金费率(Binance): {result['funding_rate']:+.4f}%")
            return result
    except Exception as e:
        logger.warning(f"Binance资金费率失败: {e}")
    # OKX
    try:
        r = requests.get('https://www.okx.com/api/v5/public/funding-rate',
                         params={'instId': 'BTC-USDT-SWAP'}, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200 and r.json().get('code') == '0':
            result['funding_rate'] = float(r.json()['data'][0]['fundingRate']) * 100
            result['source'] = 'OKX'
            logger.info(f"资金费率(OKX): {result['funding_rate']:+.4f}%")
            return result
    except Exception as e:
        logger.warning(f"OKX资金费率失败: {e}")

    # Bybit
    try:
        r = requests.get('https://api.bybit.com/v5/market/funding/history',
                         params={'category': 'linear', 'symbol': 'BTCUSDT', 'limit': 1},
                         headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200 and r.json().get('retCode') == 0:
            data = r.json().get('result', {}).get('list', [])
            if data:
                result['funding_rate'] = float(data[0]['fundingRate']) * 100
                result['source'] = 'Bybit'
                logger.info(f"资金费率(Bybit): {result['funding_rate']:+.4f}%")
                return result
    except Exception as e:
        logger.warning(f"Bybit资金费率失败: {e}")

    return result


# ============ 7. FRED宏观数据 ============

def fetch_macro_fred() -> Dict[str, Any]:
    result = {'tips10y': None, 'treasury10y': None, 'vix': None, 'dxy': None, 'fed_funds': None,
              'cpi_yoy': None, 'unrate': None, 'inflation_exp': None}
    if not FRED_API_KEY:
        logger.warning("FRED_API_KEY未配置，跳过宏观数据")
        return result

    # 普通系列：取最新值
    series_map = {
        'tips10y': 'DFII10',       # 10年期TIPS实际收益率
        'treasury10y': 'DGS10',    # 10年期美债名义收益率
        'vix': 'VIXCLS',            # VIX
        'dxy': 'DTWEXBGS',          # 贸易加权美元指数
        'fed_funds': 'FEDFUNDS',    # 联邦基金利率
        'unrate': 'UNRATE',         # 失业率
        'inflation_exp': 'T10YIE',  # 10年期breakeven通胀预期
    }

    for key, series_id in series_map.items():
        try:
            r = requests.get('https://api.stlouisfed.org/fred/series/observations',
                             params={'series_id': series_id, 'api_key': FRED_API_KEY,
                                     'file_type': 'json', 'limit': 5, 'sort_order': 'desc'},
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                obs = r.json().get('observations', [])
                for o in obs:
                    val = safe_float(o.get('value'))
                    if val is not None:
                        result[key] = val
                        break
        except Exception as e:
            logger.warning(f"FRED {key}({series_id})失败: {e}")

    # CPI同比：需要当前值和一年前的值
    try:
        r = requests.get('https://api.stlouisfed.org/fred/series/observations',
                         params={'series_id': 'CPIAUCSL', 'api_key': FRED_API_KEY,
                                 'file_type': 'json', 'limit': 15, 'sort_order': 'desc'},
                         headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            obs = r.json().get('observations', [])
            vals = [(o.get('date'), safe_float(o.get('value'))) for o in obs]
            vals = [(d, v) for d, v in vals if v is not None]
            if len(vals) >= 13:
                current = vals[0][1]
                year_ago = vals[-1][1]
                if year_ago > 0:
                    result['cpi_yoy'] = (current / year_ago - 1) * 100
                    logger.info(f"CPI同比: {result['cpi_yoy']:.2f}% (当前={current}, 一年前={year_ago})")
    except Exception as e:
        logger.warning(f"FRED CPI同比失败: {e}")

    logger.info(f"宏观: TIPS={result['tips10y']}%, 10Y={result['treasury10y']}%, VIX={result['vix']}, DXY={result['dxy']}, FFR={result['fed_funds']}%, CPI={result['cpi_yoy']}%, 失业={result['unrate']}%, 通胀预期={result['inflation_exp']}%")
    return result


# ============ 8. Kalshi美联储利率概率（替代Polymarket） ============

KALSHI_API_KEY = os.getenv('KALSHI_API_KEY', '')
KALSHI_PRIVATE_KEY = os.getenv('KALSHI_PRIVATE_KEY', '')
COINGLASS_API_KEY = os.getenv('COINGLASS_API_KEY', '')

def _kalshi_auth_headers(method: str, path: str) -> Dict[str, str]:
    """生成Kalshi认证headers（RSA-PSS签名）"""
    if not KALSHI_API_KEY or not KALSHI_PRIVATE_KEY:
        return {}
    try:
        import time as _time
        import base64
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        timestamp = str(int(_time.time() * 1000))
        msg = f"{timestamp}{method}{path}"
        # 私钥可能是PEM格式字符串
        if '-----BEGIN' in KALSHI_PRIVATE_KEY:
            private_key = serialization.load_pem_private_key(
                KALSHI_PRIVATE_KEY.encode(), password=None
            )
        else:
            # 假设是base64编码的DER
            private_key = serialization.load_der_private_key(
                base64.b64decode(KALSHI_PRIVATE_KEY), password=None
            )
        signature = private_key.sign(
            msg.encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256()
        )
        sig_b64 = base64.b64encode(signature).decode()
        return {
            'KALSHI-ACCESS-KEY': KALSHI_API_KEY,
            'KALSHI-ACCESS-TIMESTAMP': timestamp,
            'KALSHI-ACCESS-SIGNATURE': sig_b64,
        }
    except Exception as e:
        logger.warning(f"Kalshi签名失败: {e}")
        return {}

def fetch_fed_probability(macro: Dict[str, Any] = None) -> Dict[str, Any]:
    result = {'cut_prob': None, 'hike_prob': None, 'hold_prob': None,
              'theoretical_rate': None, 'current_rate': None, 'cpi_yoy': None,
              'unrate': None, 'inflation_exp': None, 'error': None, 'source': 'FRED泰勒规则'}

    if macro is None:
        result['error'] = '无宏观数据'
        return result

    fed_funds = macro.get('fed_funds')
    cpi_yoy = macro.get('cpi_yoy')
    unrate = macro.get('unrate')
    inflation_exp = macro.get('inflation_exp')

    if fed_funds is None or cpi_yoy is None or unrate is None:
        result['error'] = 'FRED数据不全'
        return result

    result['current_rate'] = fed_funds
    result['cpi_yoy'] = cpi_yoy
    result['unrate'] = unrate
    result['inflation_exp'] = inflation_exp

    # 泰勒规则：理论利率 = 通胀率 + 均衡实际利率(2%) + 0.5*(通胀率-目标2%) + 0.5*(自然失业率4%-当前失业率)
    theoretical = cpi_yoy + 2 + 0.5 * (cpi_yoy - 2) + 0.5 * (4 - unrate)
    result['theoretical_rate'] = round(theoretical, 2)

    # 差值 = 理论利率 - 当前利率（正数=加息压力，负数=降息压力）
    diff = theoretical - fed_funds

    # 通胀预期调整
    if inflation_exp is not None:
        if inflation_exp > 2.5:
            diff += 0.3  # 通胀预期高，加息压力增大
        elif inflation_exp < 1.5:
            diff -= 0.3  # 通胀预期低，降息压力增大

    # 计算概率
    if diff > 0.1:
        hike_prob = min(diff / 1.5 * 100, 80)
        cut_prob = max(5, 100 - hike_prob - 20)
    elif diff < -0.1:
        cut_prob = min(abs(diff) / 1.5 * 100, 80)
        hike_prob = max(5, 100 - cut_prob - 20)
    else:
        hike_prob = 15
        cut_prob = 15

    hold_prob = 100 - hike_prob - cut_prob
    result['hike_prob'] = round(hike_prob, 1)
    result['cut_prob'] = round(cut_prob, 1)
    result['hold_prob'] = round(hold_prob, 1)

    logger.info(f"FRED泰勒规则: 当前={fed_funds}%, 理论={theoretical:.2f}%, 差值={diff:+.2f}%, 加息={hike_prob:.1f}%, 降息={cut_prob:.1f}%, 维持={hold_prob:.1f}%")
    return result


# ============ 9. BTC周线 + 200周均线 + 偏离率 ============

def fetch_btc_weekly() -> Dict[str, Any]:
    result = {'sma200w': None, 'current_price': None, 'deviation_200w': None, 'source': None}

    # 方案1: CoinGecko历史价格（支持全部历史，美国IP友好）
    try:
        r = requests.get('https://api.coingecko.com/api/v3/coins/bitcoin/market_chart',
                         params={'vs_currency': 'usd', 'days': 'max', 'interval': 'daily'},
                         headers={**HEADERS, 'Accept': 'application/json'}, timeout=TIMEOUT)
        if r.status_code == 200:
            prices = r.json().get('prices', [])
            # prices = [[timestamp_ms, price], ...]
            closes = [safe_float(p[1]) for p in prices]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 1400:  # 约200周
                # 取最后1400天，每7天取一个收盘价计算周线SMA
                weekly_closes = closes[-1400::7]
                if len(weekly_closes) >= 200:
                    result['sma200w'] = sum(weekly_closes[-200:]) / 200
                    result['current_price'] = closes[-1]
                    result['deviation_200w'] = (closes[-1] - result['sma200w']) / result['sma200w'] * 100
                    result['source'] = 'CoinGecko'
                    logger.info(f"200周SMA(CoinGecko): ${result['sma200w']:,.0f}, 当前: ${closes[-1]:,.0f}, 偏离: {result['deviation_200w']:+.1f}%")
                    return result
    except Exception as e:
        logger.warning(f"CoinGecko周线失败: {e}")

    # 方案2: Coinbase日线聚合（Coinbase不支持周线granularity，用日线自己聚合）
    try:
        all_closes = []
        # Coinbase每次最多300条，分多次请求
        end = int(time.time())
        for batch in range(5):  # 5*300=1500天
            start = end - 300 * 86400
            r = requests.get('https://api.exchange.coinbase.com/products/BTC-USD/candles',
                             params={'granularity': 86400, 'start': start, 'end': end},
                             headers={**HEADERS, 'Accept': 'application/json'}, timeout=TIMEOUT)
            if r.status_code == 200:
                klines = r.json()
                batch_closes = [safe_float(k[4]) for k in klines]
                batch_closes = [c for c in batch_closes if c is not None]
                all_closes.extend(batch_closes)
                end = start
                if len(batch_closes) < 300:
                    break
            else:
                break
            time.sleep(0.3)
        if len(all_closes) >= 1400:
            all_closes.reverse()  # 正序
            weekly_closes = all_closes[-1400::7]
            if len(weekly_closes) >= 200:
                result['sma200w'] = sum(weekly_closes[-200:]) / 200
                result['current_price'] = all_closes[-1]
                result['deviation_200w'] = (all_closes[-1] - result['sma200w']) / result['sma200w'] * 100
                result['source'] = 'Coinbase'
                logger.info(f"200周SMA(Coinbase): ${result['sma200w']:,.0f}, 当前: ${all_closes[-1]:,.0f}, 偏离: {result['deviation_200w']:+.1f}%")
                return result
    except Exception as e:
        logger.warning(f"Coinbase日线聚合失败: {e}")

    # 方案3: Binance
    try:
        r = requests.get('https://api.binance.com/api/v3/klines',
                         params={'symbol': 'BTCUSDT', 'interval': '1w', 'limit': 210},
                         headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            klines = r.json()
            closes = [safe_float(k[4]) for k in klines]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 200:
                result['sma200w'] = sum(closes[-200:]) / 200
                result['current_price'] = closes[-1]
                result['deviation_200w'] = (closes[-1] - result['sma200w']) / result['sma200w'] * 100
                result['source'] = 'Binance'
                logger.info(f"200周SMA(Binance): ${result['sma200w']:,.0f}, 当前: ${closes[-1]:,.0f}, 偏离: {result['deviation_200w']:+.1f}%")
                return result
    except Exception as e:
        logger.warning(f"Binance周线失败: {e}")

    return result


# ============ 10. 未平仓合约量OI + 多空比 ============

def fetch_derivatives() -> Dict[str, Any]:
    result = {'open_interest': None, 'long_short_ratio': None, 'long_account': None, 'short_account': None, 'oi_source': None, 'ls_source': None}

    # ===== OI =====
    # Binance
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/openInterest',
                         params={'symbol': 'BTCUSDT'}, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            result['open_interest'] = float(r.json()['openInterest'])
            result['oi_source'] = 'Binance'
            logger.info(f"OI(Binance): {result['open_interest']:,.0f} BTC")
    except Exception as e:
        logger.warning(f"Binance OI失败: {e}")

    # Bybit备选
    if result['open_interest'] is None:
        try:
            r = requests.get('https://api.bybit.com/v5/market/open-interest',
                             params={'category': 'linear', 'symbol': 'BTCUSDT'},
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and r.json().get('retCode') == 0:
                data = r.json().get('result', {})
                if data:
                    result['open_interest'] = float(data.get('openInterest', 0))
                    result['oi_source'] = 'Bybit'
                    logger.info(f"OI(Bybit): {result['open_interest']:,.0f} BTC")
        except Exception as e:
            logger.warning(f"Bybit OI失败: {e}")

    # Kraken备选（美国交易所，对美IP友好）
    if result['open_interest'] is None:
        try:
            r = requests.get('https://futures.kraken.com/derivatives/api/v3/tickers',
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                tickers = r.json().get('tickers', [])
                for t in tickers:
                    if t.get('symbol') == 'PI_XBTUSD':
                        result['open_interest'] = float(t.get('openInterest', 0))
                        result['oi_source'] = 'Kraken'
                        logger.info(f"OI(Kraken): {result['open_interest']:,.0f} BTC")
                        break
        except Exception as e:
            logger.warning(f"Kraken OI失败: {e}")

    # ===== 多空比 =====
    # Binance
    try:
        r = requests.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
                         params={'symbol': 'BTCUSDT', 'period': '1h', 'limit': 1},
                         headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if data:
                result['long_short_ratio'] = float(data[0]['longShortRatio'])
                result['long_account'] = float(data[0]['longAccount'])
                result['short_account'] = float(data[0]['shortAccount'])
                result['ls_source'] = 'Binance'
                logger.info(f"多空比(Binance): {result['long_short_ratio']:.2f}")
    except Exception as e:
        logger.warning(f"Binance多空比失败: {e}")

    # Bybit备选
    if result['long_short_ratio'] is None:
        try:
            r = requests.get('https://api.bybit.com/v5/market/account-ratio',
                             params={'category': 'linear', 'symbol': 'BTCUSDT', 'period': '5min'},
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and r.json().get('retCode') == 0:
                data = r.json().get('result', {}).get('list', [])
                if data:
                    buy_ratio = float(data[0].get('buyRatio', 0))
                    sell_ratio = float(data[0].get('sellRatio', 1))
                    if sell_ratio > 0:
                        result['long_short_ratio'] = buy_ratio / sell_ratio
                        result['ls_source'] = 'Bybit'
                        logger.info(f"多空比(Bybit): {result['long_short_ratio']:.2f}")
        except Exception as e:
            logger.warning(f"Bybit多空比失败: {e}")

    # Coinglass正式API（带key，美国IP可访问）
    if result['long_short_ratio'] is None and COINGLASS_API_KEY:
        try:
            r = requests.get('https://open-api-v4.coinglass.com/api/futures/global-long-short-account-ratio/history',
                             params={'symbol': 'BTC', 'interval': '1h', 'limit': 1},
                             headers={'coinglass-api-key': COINGLASS_API_KEY, 'Accept': 'application/json'},
                             timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                # Coinglass返回格式: {"data": [{"time":..., "global_account_long_percent":73.24, "global_account_short_percent":26.76}]}
                ls_list = data.get('data', []) if isinstance(data, dict) else data
                if ls_list and isinstance(ls_list, list):
                    latest = ls_list[-1]
                    long_pct = float(latest.get('global_account_long_percent', 0))
                    short_pct = float(latest.get('global_account_short_percent', 0))
                    if short_pct > 0:
                        result['long_short_ratio'] = long_pct / short_pct
                        result['long_account'] = long_pct / 100
                        result['short_account'] = short_pct / 100
                        result['ls_source'] = 'Coinglass'
                        logger.info(f"多空比(Coinglass): {result['long_short_ratio']:.2f} (多{long_pct:.1f}%/空{short_pct:.1f}%)")
        except Exception as e:
            logger.warning(f"Coinglass正式API多空比失败: {e}")

    # OKX备选
    if result['long_short_ratio'] is None:
        try:
            r = requests.get('https://www.okx.com/api/v5/public/long-short-ratio',
                             params={'instId': 'BTC-USDT-SWTP', 'period': '1H'},
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and r.json().get('code') == '0':
                data = r.json().get('data', [])
                if data:
                    result['long_short_ratio'] = float(data[0].get('longShortRatio', 1))
                    result['ls_source'] = 'OKX'
                    logger.info(f"多空比(OKX): {result['long_short_ratio']:.2f}")
        except Exception as e:
            logger.warning(f"OKX多空比失败: {e}")

    # Bitget备选
    if result['long_short_ratio'] is None:
        try:
            r = requests.get('https://api.bitget.com/api/v2/mix/market/account-long-short',
                             params={'symbol': 'BTCUSDT', 'productType': 'USDT-FUTURES'},
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and r.json().get('code') == '0':
                data = r.json().get('data', {})
                if data:
                    long_amt = float(data.get('longAmount', 0))
                    short_amt = float(data.get('shortAmount', 1))
                    if short_amt > 0:
                        result['long_short_ratio'] = long_amt / short_amt
                        result['ls_source'] = 'Bitget'
                        logger.info(f"多空比(Bitget): {result['long_short_ratio']:.2f}")
        except Exception as e:
            logger.warning(f"Bitget多空比失败: {e}")

    return result


# ============ 信号检测 ============

def check_signals(price, mvrv, nupl, etf, fg, funding, macro, weekly, deriv, fed_prob) -> List[str]:
    signals = []

    # MVRV
    if mvrv.get('mvrv') is not None:
        m = mvrv['mvrv']
        if m < THRESHOLDS['mvrv_deep_bottom']:
            signals.append(f"🔴 MVRV={m:.2f} &lt; 1.0 → 历史深度底部区域")
        elif m < THRESHOLDS['mvrv_low']:
            signals.append(f"🟡 MVRV={m:.2f} &lt; 1.5 → 偏低估值区域")
        elif m > THRESHOLDS['mvrv_top']:
            signals.append(f"🔴 MVRV={m:.2f} &gt; 3.5 → 历史顶部区域")

    # NUPL
    if nupl is not None:
        if nupl < THRESHOLDS['nupl_bottom']:
            signals.append(f"🟢 NUPL={nupl*100:.1f}% &lt; 0 → 市场整体亏损，底部区域")
        elif nupl > THRESHOLDS['nupl_top']:
            signals.append(f"🔴 NUPL={nupl*100:.1f}% &gt; 75% → 整体盈利过高，顶部风险")

    # 200周均线偏离
    if weekly.get('deviation_200w') is not None:
        dev = weekly['deviation_200w']
        if dev > THRESHOLDS['deviation_overbought']:
            signals.append(f"🔴 偏离200周均线 {dev:+.1f}% &gt; +30% → 超买区间")
        elif dev < THRESHOLDS['deviation_oversold']:
            signals.append(f"🟢 偏离200周均线 {dev:+.1f}% &lt; -10% → 超卖/强支撑区间")

    # 宏观-TIPS
    if macro.get('tips10y') is not None:
        tips = macro['tips10y']
        if tips > THRESHOLDS['tips_tight']:
            signals.append(f"🔴 10Y实际收益率 {tips:.2f}% &gt; 2% → 强紧缩环境，BTC估值承压")
        elif tips < THRESHOLDS['tips_loose']:
            signals.append(f"🟢 10Y实际收益率 {tips:.2f}% &lt; 1% → 宽松环境，利好风险资产")

    # VIX
    if macro.get('vix') is not None:
        vix = macro['vix']
        if vix > THRESHOLDS['vix_high']:
            signals.append(f"🟡 VIX={vix:.1f} &gt; 30 → 市场高波动/恐慌")
        elif vix < THRESHOLDS['vix_low']:
            signals.append(f"🟡 VIX={vix:.1f} &lt; 15 → 市场极度平静，警惕变盘")

    # ETF
    if etf.get('daily_flow_m') is not None:
        flow = etf['daily_flow_m']
        if flow >= THRESHOLDS['etf_daily_inflow_m']:
            signals.append(f"🟢 ETF单日净流入 ${flow:+.0f}M → 机构大幅加仓")
        elif flow <= THRESHOLDS['etf_daily_outflow_m']:
            signals.append(f"🔴 ETF单日净流出 ${flow:+.0f}M → 机构大幅减仓")
    if etf.get('consecutive_days', 0) >= THRESHOLDS['etf_consecutive']:
        signals.append(f"🟢 ETF连续净流入 {etf['consecutive_days']} 天 → 机构持续看多")

    # 资金费率
    if funding.get('funding_rate') is not None:
        fr = funding['funding_rate']
        if fr > THRESHOLDS['funding_high']:
            signals.append(f"🟡 资金费率 {fr:+.4f}% 偏高 → 多头过热，注意回调")
        elif fr < THRESHOLDS['funding_low']:
            signals.append(f"🟢 资金费率 {fr:+.4f}% 偏低 → 空头拥挤，可能反弹")

    # 多空比
    if deriv.get('long_short_ratio') is not None:
        ls = deriv['long_short_ratio']
        if ls > THRESHOLDS['long_short_high']:
            signals.append(f"🟡 散户多空比 {ls:.2f} &gt; 1.5 → 多头极度拥挤，易插针下跌")
        elif ls < THRESHOLDS['long_short_low']:
            signals.append(f"🟢 散户多空比 {ls:.2f} &lt; 0.7 → 空头极度拥挤，易反弹")

    # 恐慌贪婪
    if fg.get('value') is not None:
        v = fg['value']
        if v < THRESHOLDS['fear_extreme']:
            signals.append(f"🟢 恐慌指数 {v} → 极度恐慌，底部机会")
        elif v > THRESHOLDS['greed_extreme']:
            signals.append(f"🔴 贪婪指数 {v} → 极度贪婪，顶部风险")

    # 美联储概率
    if fed_prob.get('cut_prob') is not None and fed_prob['cut_prob'] > 70:
        signals.append(f"🟢 Polymarket降息概率 {fed_prob['cut_prob']:.0f}% &gt; 70% → 市场计价宽松拐点")
    if fed_prob.get('hike_prob') is not None and fed_prob['hike_prob'] > 70:
        signals.append(f"🔴 Polymarket加息概率 {fed_prob['hike_prob']:.0f}% &gt; 70% → 市场计价紧缩")

    return signals


# ============ 消息格式化 ============

def format_message(price, mvrv, nupl, etf, fg, funding, macro, weekly, deriv, fed_prob, signals) -> str:
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime('%Y-%m-%d %H:%M')
    L = []

    L.append(f"📊 <b>BTC 每日数据</b> | {date_str}")
    L.append("")

    # ===== 一、价格与情绪 =====
    L.append("━━━ <b>一、价格与情绪</b> ━━━")
    if price.get('price') is not None:
        chg = price.get('change_24h', 0)
        emoji = '🟢' if chg >= 0 else '🔴'
        L.append(f"💰 <b>价格</b>：${price['price']:,.2f}  |  24h: {emoji} {chg:+.2f}%")
    else:
        L.append("💰 <b>价格</b>：获取失败")

    if mvrv.get('realized_price'):
        L.append(f"🏷️ <b>已实现价格</b>：${mvrv['realized_price']:,.0f}（链上平均持仓成本）")

    if fg.get('value') is not None:
        v = fg['value']
        emoji = '🟢' if v < 45 else ('🔴' if v > 55 else '⚪')
        L.append(f"🎭 <b>恐慌贪婪</b>：{v} {emoji}（{fg.get('classification','')}）")
    else:
        L.append("🎭 <b>恐慌贪婪</b>：获取失败")
    L.append("")

    # ===== 二、链上估值 =====
    L.append("━━━ <b>二、链上估值</b> ━━━")
    if mvrv.get('mvrv') is not None:
        zone_emoji = {'deep_bottom':'🔴','low':'🟡','neutral':'⚪','high':'🟡','top':'🔴'}.get(mvrv.get('zone'),'⚪')
        L.append(f"📈 <b>MVRV</b>：{mvrv['mvrv']:.2f} {zone_emoji} {mvrv.get('zone_label','')}")
        L.append(f"   阈值：&lt;1.0底部 | 1.0-1.5偏低 | 1.5-2.5中性 | &gt;3.5顶部")
    else:
        L.append("📈 <b>MVRV</b>：获取失败")

    if nupl is not None:
        L.append(f"📊 <b>NUPL</b>：{nupl*100:+.1f}%（净未实现盈亏）")
    if weekly.get('sma200w') is not None:
        dev = weekly.get('deviation_200w', 0)
        dev_emoji = '🟢' if dev < 0 else '🔴'
        L.append(f"📏 <b>200周均线</b>：${weekly['sma200w']:,.0f}  |  偏离：{dev_emoji} {dev:+.1f}%")
    L.append("")

    # ===== 三、宏观流动性 =====
    L.append("━━━ <b>三、宏观流动性</b> ━━━")
    if macro.get('treasury10y') is not None:
        L.append(f"🏛️ <b>10Y美债收益率</b>：{macro['treasury10y']:.2f}%")
    if macro.get('tips10y') is not None:
        tips = macro['tips10y']
        tips_tag = '🔴强紧缩' if tips > 2.0 else ('🟢宽松' if tips < 1.0 else '⚪中性')
        L.append(f"📉 <b>10Y实际收益率(TIPS)</b>：{tips:.2f}%  {tips_tag}")
    if macro.get('dxy') is not None:
        L.append(f"💵 <b>美元指数</b>：{macro['dxy']:.2f}（贸易加权）")
    if macro.get('vix') is not None:
        L.append(f"😱 <b>VIX恐慌指数</b>：{macro['vix']:.1f}")
    if macro.get('fed_funds') is not None:
        L.append(f"🏦 <b>联邦基金利率</b>：{macro['fed_funds']:.2f}%")
    if fed_prob.get('hike_prob') is not None or fed_prob.get('cut_prob') is not None:
        prob_parts = []
        if fed_prob.get('hike_prob') is not None:
            prob_parts.append(f"加息{fed_prob['hike_prob']:.0f}%")
        if fed_prob.get('cut_prob') is not None:
            prob_parts.append(f"降息{fed_prob['cut_prob']:.0f}%")
        if fed_prob.get('hold_prob') is not None:
            prob_parts.append(f"维持{fed_prob['hold_prob']:.0f}%")
        if prob_parts:
            L.append(f"🎯 <b>利率预期(泰勒规则)</b>：{' | '.join(prob_parts)}")
            if fed_prob.get('theoretical_rate') is not None and fed_prob.get('current_rate') is not None:
                diff = fed_prob['theoretical_rate'] - fed_prob['current_rate']
                L.append(f"   当前{fed_prob['current_rate']}% → 理论{fed_prob['theoretical_rate']}%（差值{diff:+.2f}%）")
            if fed_prob.get('cpi_yoy') is not None or fed_prob.get('unrate') is not None:
                detail = []
                if fed_prob.get('cpi_yoy') is not None:
                    detail.append(f"CPI {fed_prob['cpi_yoy']:.1f}%")
                if fed_prob.get('unrate') is not None:
                    detail.append(f"失业{fed_prob['unrate']:.1f}%")
                if fed_prob.get('inflation_exp') is not None:
                    detail.append(f"通胀预期{fed_prob['inflation_exp']:.1f}%")
                if detail:
                    L.append(f"   {' | '.join(detail)}")
    else:
        err = fed_prob.get('error', '')
        L.append(f"🎯 <b>利率预期</b>：获取失败{('（'+err+'）') if err else ''}")
    L.append("")

    # ===== 四、资金流 =====
    L.append("━━━ <b>四、资金流</b> ━━━")
    if etf.get('daily_flow_m') is not None:
        flow = etf['daily_flow_m']
        emoji = '🟢' if flow >= 0 else '🔴'
        L.append(f"💵 <b>ETF单日净流入</b>：{emoji} ${flow:+.1f}M  |  连续流入：{etf.get('consecutive_days',0)}天")
        if etf.get('latest_date'):
            L.append(f"   数据日期：{etf['latest_date']}")
    else:
        L.append("💵 <b>ETF单日净流入</b>：获取失败")
    if etf.get('cumulative_m') is not None:
        cum = etf['cumulative_m']
        L.append(f"📈 <b>ETF累计净流入</b>：${cum/1000:,.1f}B（${cum:,.0f}M）")
    L.append("")

    # ===== 五、合约市场 =====
    L.append("━━━ <b>五、合约市场</b> ━━━")
    if funding.get('funding_rate') is not None:
        fr = funding['funding_rate']
        emoji = '🟢' if fr >= 0 else '🔴'
        L.append(f"📉 <b>资金费率</b>：{emoji} {fr:+.4f}%")
    else:
        L.append("📉 <b>资金费率</b>：获取失败")
    if deriv.get('open_interest') is not None:
        oi = deriv['open_interest']
        oi_usd = oi * (price.get('price') or 0)
        L.append(f"📊 <b>未平仓量(OI)</b>：{oi:,.0f} BTC（约${oi_usd/1e9:.2f}B）")
    if deriv.get('long_short_ratio') is not None:
        ls = deriv['long_short_ratio']
        ls_tag = '🟡多头拥挤' if ls > 1.5 else ('🟢空头拥挤' if ls < 0.7 else '⚪中性')
        L.append(f"👥 <b>散户多空比</b>：{ls:.2f}  {ls_tag}")
    else:
        # 多空比获取失败时，用资金费率综合判断多空情绪
        fr = funding.get('funding_rate')
        if fr is not None:
            if fr > 0.03:
                sentiment = '🟡多头偏强'
                detail = f'资金费率{fr:+.4f}% &gt; 0.03%，多头付费持仓'
            elif fr < -0.03:
                sentiment = '🟢空头偏强'
                detail = f'资金费率{fr:+.4f}% &lt; -0.03%，空头付费持仓'
            else:
                sentiment = '⚪多空均衡'
                detail = f'资金费率{fr:+.4f}%，多空力量均衡'
            L.append(f"👥 <b>多空情绪(综合)</b>：{sentiment}")
            L.append(f"   {detail}")
        else:
            L.append("👥 <b>多空情绪</b>：数据不足")
    L.append("")

    # ===== 信号预警 =====
    if signals:
        L.append(f"⚠️ <b>信号预警</b>（{len(signals)}条）：")
        for s in signals:
            L.append(f"  {s}")
    else:
        L.append("⚠️ <b>信号预警</b>：无触发信号")
    L.append("")

    # ===== 指标释义 =====
    L.append("━━━ <b>📖 指标释义</b> ━━━")
    L.append("• <b>MVRV</b>：市价÷链上持仓成本，衡量估值高低")
    L.append("• <b>已实现价格</b>：链上所有BTC平均持仓成本价")
    L.append("• <b>NUPL</b>：净未实现盈亏=(市值-已实现市值)/市值，&lt;0整体亏损")
    L.append("• <b>200周均线</b>：牛熊分界线，熊市大底常回踩此线")
    L.append("• <b>TIPS实际收益率</b>：扣除通胀后的真实利率，&gt;2%强紧缩")
    L.append("• <b>美元指数</b>：美元强弱，强美元周期BTC普遍承压")
    L.append("• <b>VIX</b>：美股波动率，&gt;30高恐慌，&lt;15极度平静")
    L.append("• <b>利率预期</b>：泰勒规则计算，理论利率=CPI+2+0.5*(CPI-2)+0.5*(4-失业率)")
    L.append("• <b>ETF资金流</b>：现货ETF每日资金进出，代表机构动向")
    L.append("• <b>资金费率</b>：永续合约多空成本，正数多头付费")
    L.append("• <b>OI未平仓量</b>：合约市场总持仓，反映市场参与度")
    L.append("• <b>多空情绪</b>：综合资金费率判断，费率&gt;0.03%多头偏强，&lt;-0.03%空头偏强")
    L.append("")
    L.append("<i>数据来源：bitbo.io/Alternative.me/farside.co.uk/FRED/CoinGecko/Kraken</i>")

    return '\n'.join(L)


# ============ Telegram推送 ============

def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram未配置")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message,
               'parse_mode': 'HTML', 'disable_web_page_preview': True}
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 200:
                logger.info("Telegram推送成功")
                return True
            else:
                logger.warning(f"推送失败({attempt+1}/3): {r.status_code} {r.text[:200]}")
        except Exception as e:
            logger.warning(f"推送异常({attempt+1}/3): {e}")
        if attempt < 2:
            time.sleep(5)
    return False


# ============ 主函数 ============

def main():
    logger.info("=" * 50)
    logger.info("BTC每日数据采集开始（全指标版）")
    logger.info("=" * 50)

    price = fetch_price()
    fg = fetch_fear_greed()
    mvrv = fetch_mvrv()
    nupl = calculate_nupl(mvrv)
    etf = fetch_etf_flows()
    funding = fetch_funding_rate()
    macro = fetch_macro_fred()
    weekly = fetch_btc_weekly()
    deriv = fetch_derivatives()
    fed_prob = fetch_fed_probability(macro)

    signals = check_signals(price, mvrv, nupl, etf, fg, funding, macro, weekly, deriv, fed_prob)
    message = format_message(price, mvrv, nupl, etf, fg, funding, macro, weekly, deriv, fed_prob, signals)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        if not send_telegram(message):
            sys.exit(1)
    else:
        logger.warning("未配置Telegram，仅本地调试")
        print("\n" + message)

    logger.info("采集完成")


if __name__ == '__main__':
    main()
