#!/usr/bin/env python3
"""
BTC 每日数据自动采集 + Telegram 推送
数据项：价格、MVRV(底部判断)、ETF资金流、恐慌贪婪指数、资金费率、信号预警
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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Telegram 配置（从环境变量/GitHub Secrets 读取）
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# 请求超时
TIMEOUT = 20
# 请求头
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# 信号阈值
SIGNAL_THRESHOLDS = {
    'mvrv_bottom': 1.0,       # MVRV < 1.0 深度底部
    'mvrv_low': 1.5,          # MVRV < 1.5 偏低
    'mvrv_top': 3.5,          # MVRV > 3.5 顶部
    'etf_consecutive_inflow': 3,  # ETF连续流入>=3天
    'etf_daily_inflow_m': 200,    # ETF单日净流入>200M
    'etf_daily_outflow_m': -200,  # ETF单日净流出<-200M
    'funding_rate_high': 0.05,    # 资金费率>0.05%（多头过热）
    'funding_rate_low': -0.05,    # 资金费率<-0.05%（空头过热）
    'fear_extreme': 25,           # 恐慌指数<25 极度恐慌
    'greed_extreme': 75,          # 贪婪指数>75 极度贪婪
}


# ============ 数据采集函数 ============

def fetch_price() -> Dict[str, Any]:
    """获取BTC当前价格和24h涨跌幅，Binance主，bitbo备选"""
    result = {'price': None, 'change_24h': None, 'source': None}

    # 方案1: Binance
    try:
        r = requests.get(
            'https://api.binance.com/api/v3/ticker/24hr',
            params={'symbol': 'BTCUSDT'},
            headers=HEADERS, timeout=TIMEOUT
        )
        if r.status_code == 200:
            d = r.json()
            result['price'] = float(d['lastPrice'])
            result['change_24h'] = float(d['priceChangePercent'])
            result['source'] = 'Binance'
            logger.info(f"价格(Binance): ${result['price']:,.2f} ({result['change_24h']:+.2f}%)")
            return result
    except Exception as e:
        logger.warning(f"Binance价格获取失败: {e}")

    # 方案2: bitbo.io price-history
    try:
        r = requests.get(
            'https://api.bitbo.io/price-history',
            params={'interval': '5_min', 'limit': 288},  # 24h数据
            headers=HEADERS, timeout=TIMEOUT
        )
        if r.status_code == 200:
            d = r.json()
            data = d.get('5_min', [])
            if len(data) >= 2:
                latest = data[-1]
                first = data[0]
                result['price'] = float(latest['p'])
                result['change_24h'] = (float(latest['p']) / float(first['p']) - 1) * 100
                result['source'] = 'bitbo.io'
                logger.info(f"价格(bitbo): ${result['price']:,.2f} ({result['change_24h']:+.2f}%)")
                return result
    except Exception as e:
        logger.warning(f"bitbo价格获取失败: {e}")

    logger.error("所有价格数据源均失败")
    return result


def fetch_fear_greed() -> Dict[str, Any]:
    """获取恐慌贪婪指数"""
    result = {'value': None, 'classification': None}
    try:
        r = requests.get(
            'https://api.alternative.me/fng/',
            params={'limit': 1},
            headers=HEADERS, timeout=TIMEOUT
        )
        if r.status_code == 200:
            d = r.json()
            item = d['data'][0]
            result['value'] = int(item['value'])
            result['classification'] = item['value_classification']
            logger.info(f"恐慌贪婪: {result['value']} ({result['classification']})")
            return result
    except Exception as e:
        logger.warning(f"恐慌贪婪指数获取失败: {e}")
    return result


def fetch_mvrv() -> Dict[str, Any]:
    """从bitbo.io获取MVRV值，并判断底部区间"""
    result = {
        'mvrv': None,
        'realized_price': None,
        'market_cap': None,
        'zone': None,
        'zone_label': None
    }
    try:
        r = requests.get('https://bitbo.io/', headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            logger.warning(f"bitbo.io状态码: {r.status_code}")
            return result

        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text(separator='\n')
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        # 提取MVRV
        for i, line in enumerate(lines):
            if line == 'MVRV' and i + 1 < len(lines):
                try:
                    result['mvrv'] = float(lines[i + 1])
                except ValueError:
                    pass
            if line == 'Realized Price' and i + 3 < len(lines):
                # 结构: Realized Price / $ / 52,700.7
                for j in range(i + 1, min(i + 5, len(lines))):
                    val = lines[j].replace('$', '').replace(',', '').strip()
                    try:
                        result['realized_price'] = float(val)
                        break
                    except ValueError:
                        continue

        # 判断区间
        if result['mvrv'] is not None:
            m = result['mvrv']
            if m < 1.0:
                result['zone'] = 'deep_bottom'
                result['zone_label'] = '深度底部'
            elif m < 1.5:
                result['zone'] = 'low'
                result['zone_label'] = '偏低'
            elif m < 2.5:
                result['zone'] = 'neutral'
                result['zone_label'] = '中性'
            elif m < 3.5:
                result['zone'] = 'high'
                result['zone_label'] = '偏高'
            else:
                result['zone'] = 'top'
                result['zone_label'] = '顶部'

        if result['mvrv'] is not None:
            rp_str = f"${result['realized_price']:,.0f}" if result['realized_price'] else "N/A"
            logger.info(f"MVRV: {result['mvrv']} ({result['zone_label']}), Realized Price: {rp_str}")
        else:
            logger.info("MVRV获取失败")
    except Exception as e:
        logger.warning(f"MVRV获取失败: {e}")
    return result


def fetch_etf_flows() -> Dict[str, Any]:
    """从farside.co.uk获取BTC ETF资金流，计算单日净流入和连续流入天数"""
    result = {
        'daily_flow_m': None,    # 单日净流入（百万美元）
        'consecutive_days': 0,   # 连续流入天数
        'recent_flows': [],      # 最近N天数据
        'latest_date': None
    }

    def parse_num(s: str) -> Optional[float]:
        """解析数字，处理括号表示负数、逗号、破折号"""
        s = s.strip().replace(',', '')
        if s in ('', '-', '—'):
            return 0.0
        if s.startswith('(') and s.endswith(')'):
            s = '-' + s[1:-1]
        try:
            return float(s)
        except ValueError:
            return None

    # 重试2次
    for attempt in range(3):
        try:
            r = requests.get('https://farside.co.uk/btc/', headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                logger.warning(f"farside状态码: {r.status_code} (尝试{attempt+1}/3)")
                if attempt < 2:
                    time.sleep(3)
                    continue
                return result

            soup = BeautifulSoup(r.text, 'lxml')
            tables = soup.find_all('table')
            if not tables:
                logger.warning("未找到表格")
                return result

            # 第一个表格是ETF资金流
            table = tables[0]
            rows = table.find_all('tr')

            daily_data = []  # [(date_str, total_million)]
            for row in rows:
                cells = [c.get_text(strip=True) for c in row.find_all(['th', 'td'])]
                if len(cells) < 14:
                    continue
                # 第一列是日期，最后一列是Total
                date_str = cells[0]
                total_str = cells[-1]
                # 检查是否是日期行（如 "03 Aug 2026"）
                if not any(month in date_str for month in ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']):
                    continue
                total_m = parse_num(total_str)
                if total_m is not None:
                    daily_data.append((date_str, total_m))

            if not daily_data:
                logger.warning("未解析到ETF数据")
                if attempt < 2:
                    time.sleep(3)
                    continue
                return result

            # 取最近10天
            recent = daily_data[-10:] if len(daily_data) >= 10 else daily_data

            latest_date, latest_flow = recent[-1]
            result['daily_flow_m'] = latest_flow
            result['latest_date'] = latest_date
            result['recent_flows'] = recent

            # 计算连续流入天数（从最新一天往前数）
            consecutive = 0
            for _, flow in reversed(recent):
                if flow > 0:
                    consecutive += 1
                else:
                    break
            result['consecutive_days'] = consecutive

            logger.info(f"ETF: {latest_date} 净流入 ${latest_flow:+.1f}M, 连续流入 {consecutive} 天")
            return result
        except Exception as e:
            logger.warning(f"ETF资金流获取失败 (尝试{attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(3)

    return result


def fetch_funding_rate() -> Dict[str, Any]:
    """获取永续合约资金费率，Binance主，OKX备选"""
    result = {'funding_rate': None, 'source': None}

    # 方案1: Binance
    try:
        r = requests.get(
            'https://fapi.binance.com/fapi/v1/premiumIndex',
            params={'symbol': 'BTCUSDT'},
            headers=HEADERS, timeout=TIMEOUT
        )
        if r.status_code == 200:
            d = r.json()
            result['funding_rate'] = float(d['lastFundingRate']) * 100  # 转百分比
            result['source'] = 'Binance'
            logger.info(f"资金费率(Binance): {result['funding_rate']:+.4f}%")
            return result
    except Exception as e:
        logger.warning(f"Binance资金费率获取失败: {e}")

    # 方案2: OKX
    try:
        r = requests.get(
            'https://www.okx.com/api/v5/public/funding-rate',
            params={'instId': 'BTC-USDT-SWAP'},
            headers=HEADERS, timeout=TIMEOUT
        )
        if r.status_code == 200:
            d = r.json()
            if d.get('code') == '0' and d.get('data'):
                result['funding_rate'] = float(d['data'][0]['fundingRate']) * 100
                result['source'] = 'OKX'
                logger.info(f"资金费率(OKX): {result['funding_rate']:+.4f}%")
                return result
    except Exception as e:
        logger.warning(f"OKX资金费率获取失败: {e}")

    logger.error("资金费率所有数据源均失败")
    return result


# ============ 信号检测 ============

def check_signals(price_data, mvrv_data, etf_data, fg_data, funding_data) -> List[str]:
    """检测关键信号，返回预警列表"""
    signals = []

    # MVRV信号
    if mvrv_data.get('mvrv') is not None:
        m = mvrv_data['mvrv']
        if m < SIGNAL_THRESHOLDS['mvrv_bottom']:
            signals.append(f"🔴 MVRV={m:.2f} &lt; 1.0 → 历史深度底部区域")
        elif m < SIGNAL_THRESHOLDS['mvrv_low']:
            signals.append(f"🟡 MVRV={m:.2f} &lt; 1.5 → 偏低估值区域")
        elif m > SIGNAL_THRESHOLDS['mvrv_top']:
            signals.append(f"🔴 MVRV={m:.2f} &gt; 3.5 → 历史顶部区域")

    # ETF信号
    if etf_data.get('daily_flow_m') is not None:
        flow = etf_data['daily_flow_m']
        if flow >= SIGNAL_THRESHOLDS['etf_daily_inflow_m']:
            signals.append(f"🟢 ETF单日净流入 ${flow:+.0f}M → 机构大幅加仓")
        elif flow <= SIGNAL_THRESHOLDS['etf_daily_outflow_m']:
            signals.append(f"🔴 ETF单日净流出 ${flow:+.0f}M → 机构大幅减仓")

    if etf_data.get('consecutive_days', 0) >= SIGNAL_THRESHOLDS['etf_consecutive_inflow']:
        signals.append(f"🟢 ETF连续净流入 {etf_data['consecutive_days']} 天 → 机构持续看多")

    # 资金费率信号
    if funding_data.get('funding_rate') is not None:
        fr = funding_data['funding_rate']
        if fr > SIGNAL_THRESHOLDS['funding_rate_high']:
            signals.append(f"🟡 资金费率 {fr:+.4f}% 偏高 → 多头过热，注意回调风险")
        elif fr < SIGNAL_THRESHOLDS['funding_rate_low']:
            signals.append(f"🟢 资金费率 {fr:+.4f}% 偏低 → 空头过热，可能反弹")

    # 恐慌贪婪信号
    if fg_data.get('value') is not None:
        v = fg_data['value']
        if v < SIGNAL_THRESHOLDS['fear_extreme']:
            signals.append(f"🟢 恐慌指数 {v} → 极度恐慌，可能是底部机会")
        elif v > SIGNAL_THRESHOLDS['greed_extreme']:
            signals.append(f"🔴 贪婪指数 {v} → 极度贪婪，注意顶部风险")

    return signals


# ============ 消息格式化 ============

def format_message(price_data, mvrv_data, etf_data, fg_data, funding_data, signals) -> str:
    """格式化Telegram消息"""
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime('%Y-%m-%d %H:%M')

    lines = []
    lines.append(f"📊 <b>BTC 每日数据</b> | {date_str}")
    lines.append("")

    # 价格
    if price_data.get('price') is not None:
        change = price_data.get('change_24h', 0)
        emoji = '🟢' if change >= 0 else '🔴'
        lines.append(f"💰 <b>价格</b>：${price_data['price']:,.2f}  |  24h: {emoji} {change:+.2f}%")
    else:
        lines.append("💰 <b>价格</b>：获取失败")

    # MVRV
    if mvrv_data.get('mvrv') is not None:
        zone_emoji = {
            'deep_bottom': '🔴', 'low': '🟡', 'neutral': '⚪',
            'high': '🟡', 'top': '🔴'
        }.get(mvrv_data.get('zone'), '⚪')
        mvrv_line = f"📈 <b>MVRV</b>：{mvrv_data['mvrv']:.2f}  {zone_emoji} {mvrv_data.get('zone_label', '')}"
        if mvrv_data.get('realized_price'):
            mvrv_line += f"  |  已实现价格：${mvrv_data['realized_price']:,.0f}"
        lines.append(mvrv_line)
        lines.append(f"   阈值参考：&lt;1.0底部 | 1.0-1.5偏低 | 1.5-2.5中性 | &gt;3.5顶部")
    else:
        lines.append("📈 <b>MVRV</b>：获取失败")

    # ETF
    if etf_data.get('daily_flow_m') is not None:
        flow = etf_data['daily_flow_m']
        emoji = '🟢' if flow >= 0 else '🔴'
        lines.append(f"💵 <b>ETF资金流</b>：{emoji} ${flow:+.1f}M  |  连续流入：{etf_data.get('consecutive_days', 0)}天")
    else:
        lines.append("💵 <b>ETF资金流</b>：获取失败")

    # 恐慌贪婪
    if fg_data.get('value') is not None:
        v = fg_data['value']
        if v < 25:
            emoji = '🟢'
        elif v < 45:
            emoji = '🟡'
        elif v < 55:
            emoji = '⚪'
        elif v < 75:
            emoji = '🟡'
        else:
            emoji = '🔴'
        lines.append(f"🎭 <b>恐慌贪婪</b>：{v} {emoji}（{fg_data.get('classification', '')}）")
    else:
        lines.append("🎭 <b>恐慌贪婪</b>：获取失败")

    # 资金费率
    if funding_data.get('funding_rate') is not None:
        fr = funding_data['funding_rate']
        emoji = '🟢' if fr >= 0 else '🔴'
        lines.append(f"📉 <b>资金费率</b>：{emoji} {fr:+.4f}%")
    else:
        lines.append("📉 <b>资金费率</b>：获取失败")

    # 信号预警
    if signals:
        lines.append("")
        lines.append(f"⚠️ <b>信号预警</b>（{len(signals)}条）：")
        for sig in signals:
            lines.append(f"  {sig}")

    lines.append("")
    lines.append("——————————")
    lines.append("📖 <b>指标释义</b>")
    lines.append("• <b>MVRV</b>：市价÷链上持仓成本，衡量市场整体估值高低")
    lines.append("• <b>已实现价格</b>：链上所有BTC的平均持仓成本价")
    lines.append("• <b>ETF资金流</b>：BTC现货ETF每日资金进出，代表机构动向")
    lines.append("• <b>恐慌贪婪指数</b>：市场情绪，0极度恐慌～100极度贪婪")
    lines.append("• <b>资金费率</b>：永续合约多空成本，正数多头付费，负数空头付费")
    lines.append("")
    lines.append("<i>数据来源：Binance/bitbo.io/Alternative.me/farside.co.uk</i>")

    return '\n'.join(lines)


# ============ Telegram推送 ============

def send_telegram(message: str) -> bool:
    """发送Telegram消息，支持HTML格式"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram token或chat_id未配置")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True
    }

    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=TIMEOUT)
            if r.status_code == 200:
                logger.info("Telegram推送成功")
                return True
            else:
                logger.warning(f"Telegram推送失败 (尝试{attempt+1}/3): {r.status_code} {r.text[:200]}")
        except Exception as e:
            logger.warning(f"Telegram推送异常 (尝试{attempt+1}/3): {e}")
        if attempt < 2:
            time.sleep(5)

    logger.error("Telegram推送最终失败")
    return False


# ============ 主函数 ============

def main():
    logger.info("=" * 50)
    logger.info("BTC每日数据采集开始")
    logger.info("=" * 50)

    # 采集所有数据
    price_data = fetch_price()
    fg_data = fetch_fear_greed()
    mvrv_data = fetch_mvrv()
    etf_data = fetch_etf_flows()
    funding_data = fetch_funding_rate()

    # 检测信号
    signals = check_signals(price_data, mvrv_data, etf_data, fg_data, funding_data)

    # 格式化消息
    message = format_message(price_data, mvrv_data, etf_data, fg_data, funding_data, signals)

    # 推送
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        success = send_telegram(message)
        if not success:
            sys.exit(1)
    else:
        logger.warning("未配置Telegram，跳过推送（仅本地调试模式）")
        print("\n" + message)

    logger.info("采集完成")


if __name__ == '__main__':
    main()
