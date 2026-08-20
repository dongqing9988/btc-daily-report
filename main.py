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
              'latest_date': None, 'recent_flows': []}

    def parse_table(html):
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

    # 用session保持cookies，先访问首页
    session = requests.Session()
    session.headers.update(HEADERS)

    for attempt in range(3):
        try:
            # 先访问首页获取cookies
            session.get('https://farside.co.uk/', timeout=TIMEOUT)
            time.sleep(1)

            # 访问完整数据页（历史数据多，可算累计）
            r = session.get('https://farside.co.uk/bitcoin-etf-flow-all-data/', timeout=TIMEOUT)
            if r.status_code != 200:
                logger.warning(f"farside完整页状态码: {r.status_code} (尝试{attempt+1})")
                # 降级用/btc/页
                r = session.get('https://farside.co.uk/btc/', timeout=TIMEOUT)

            if r.status_code == 200:
                daily_data = parse_table(r.text)
                if daily_data:
                    # 最新一天
                    latest_date, latest_flow = daily_data[-1]
                    result['daily_flow_m'] = latest_flow
                    result['latest_date'] = latest_date
                    result['recent_flows'] = daily_data[-10:]

                    # 累计净流入（全部历史累加）
                    result['cumulative_m'] = sum(f for _, f in daily_data)

                    # 连续流入天数
                    consecutive = 0
                    for _, flow in reversed(daily_data[-10:]):
                        if flow > 0:
                            consecutive += 1
                        else:
                            break
                    result['consecutive_days'] = consecutive

                    logger.info(f"ETF: {latest_date} ${latest_flow:+.1f}M, 连续{consecutive}天, 累计${result['cumulative_m']:,.0f}M")
                    return result
                else:
                    logger.warning(f"未解析到ETF数据 (尝试{attempt+1})")
        except Exception as e:
            logger.warning(f"ETF获取失败 (尝试{attempt+1}/3): {e}")
        if attempt < 2:
            time.sleep(3)

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
    return result


# ============ 7. FRED宏观数据 ============

def fetch_macro_fred() -> Dict[str, Any]:
    result = {'tips10y': None, 'treasury10y': None, 'vix': None, 'dxy': None, 'fed_funds': None}
    if not FRED_API_KEY:
        logger.warning("FRED_API_KEY未配置，跳过宏观数据")
        return result

    series_map = {
        'tips10y': 'DFII10',       # 10年期TIPS实际收益率
        'treasury10y': 'DGS10',    # 10年期美债名义收益率
        'vix': 'VIXCLS',            # VIX
        'dxy': 'DTWEXBGS',          # 贸易加权美元指数
        'fed_funds': 'FEDFUNDS',    # 联邦基金利率
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

    logger.info(f"宏观: TIPS={result['tips10y']}%, 10Y={result['treasury10y']}%, VIX={result['vix']}, DXY={result['dxy']}, FFR={result['fed_funds']}%")
    return result


# ============ 8. Polymarket美联储利率概率 ============

def fetch_fed_probability() -> Dict[str, Any]:
    result = {'cut_prob': None, 'hike_prob': None, 'market_question': None}
    try:
        # 搜索美联储相关市场
        r = requests.get('https://gamma-api.polymarket.com/markets',
                         params={'search': 'fed rate', 'closed': 'false', 'limit': 15,
                                 'order': 'volume24hr', 'ascending': 'false'},
                         headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            logger.warning(f"Polymarket搜索状态码: {r.status_code}")
            return result

        markets = r.json()
        best_market = None
        for m in markets:
            q = m.get('question', '').lower()
            if any(kw in q for kw in ['fed', 'fomc', 'rate', 'interest', 'cut', 'hike', 'powell']):
                best_market = m
                break

        if not best_market:
            logger.warning("Polymarket未找到美联储相关市场")
            return result

        result['market_question'] = best_market.get('question')
        outcomes = best_market.get('outcomes', '[]')
        prices = best_market.get('outcomePrices', '[]')
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if isinstance(prices, str):
            prices = json.loads(prices)

        for o, p in zip(outcomes, prices):
            prob = float(p) * 100
            o_lower = o.lower()
            if any(kw in o_lower for kw in ['cut', '降', '降息']):
                result['cut_prob'] = prob
            elif any(kw in o_lower for kw in ['hike', '加', '加息', 'raise']):
                result['hike_prob'] = prob

        logger.info(f"Polymarket: {result['market_question'][:50]}... 降息={result['cut_prob']}%, 加息={result['hike_prob']}%")
    except Exception as e:
        logger.warning(f"Polymarket失败: {e}")
    return result


# ============ 9. BTC周线 + 200周均线 + 偏离率 ============

def fetch_btc_weekly() -> Dict[str, Any]:
    result = {'sma200w': None, 'current_price': None, 'deviation_200w': None}
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
                logger.info(f"200周SMA: ${result['sma200w']:,.0f}, 当前: ${closes[-1]:,.0f}, 偏离: {result['deviation_200w']:+.1f}%")
    except Exception as e:
        logger.warning(f"BTC周线失败: {e}")
    return result


# ============ 10. 未平仓合约量OI + 多空比 ============

def fetch_derivatives() -> Dict[str, Any]:
    result = {'open_interest': None, 'long_short_ratio': None, 'long_account': None, 'short_account': None}
    # OI
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/openInterest',
                         params={'symbol': 'BTCUSDT'}, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            result['open_interest'] = float(r.json()['openInterest'])
            logger.info(f"OI: {result['open_interest']:,.0f} BTC")
    except Exception as e:
        logger.warning(f"OI失败: {e}")
    # 多空比
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
                logger.info(f"多空比: {result['long_short_ratio']:.2f}")
    except Exception as e:
        logger.warning(f"多空比失败: {e}")
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
        line = f"📈 <b>MVRV</b>：{mvrv['mvrv']:.2f} {zone_emoji} {mvrv.get('zone_label','')}"
        if mvrv.get('realized_price'):
            line += f"  |  已实现价格：${mvrv['realized_price']:,.0f}"
        L.append(line)
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
    if fed_prob.get('cut_prob') is not None or fed_prob.get('hike_prob') is not None:
        prob_parts = []
        if fed_prob.get('cut_prob') is not None:
            prob_parts.append(f"降息{fed_prob['cut_prob']:.0f}%")
        if fed_prob.get('hike_prob') is not None:
            prob_parts.append(f"加息{fed_prob['hike_prob']:.0f}%")
        if prob_parts:
            L.append(f"🎯 <b>Polymarket利率预期</b>：{' | '.join(prob_parts)}")
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
    L.append("• <b>ETF资金流</b>：现货ETF每日资金进出，代表机构动向")
    L.append("• <b>资金费率</b>：永续合约多空成本，正数多头付费")
    L.append("• <b>OI未平仓量</b>：合约市场总持仓，反映市场参与度")
    L.append("• <b>多空比</b>：散户多空账户比，&gt;1.5多头拥挤易插针")
    L.append("")
    L.append("<i>数据来源：Binance/bitbo.io/Alternative.me/farside.co.uk/FRED/Polymarket</i>")

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
    fed_prob = fetch_fed_probability()

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
