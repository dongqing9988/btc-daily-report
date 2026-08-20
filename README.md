# BTC 每日数据自动推送

每天自动采集 BTC 关键数据，通过 Telegram Bot 推送到手机，完全云端自动化（GitHub Actions），无需本地电脑开机。

## 数据项

| 数据项 | 数据源 | 说明 |
|--------|--------|------|
| BTC价格 + 24h涨跌幅 | Binance / bitbo.io | 实时价格 |
| MVRV估值 + 底部判断 | bitbo.io | MVRV值 + 历史阈值区间判断 |
| ETF资金流 + 连续流入天数 | farside.co.uk | 全市场BTC ETF单日净流入/流出 |
| 恐慌贪婪指数 | Alternative.me | 0-100情绪指数 |
| 永续合约资金费率 | Binance / OKX | 多空力量对比 |
| 关键信号预警 | 脚本内置阈值 | 触发时高亮提醒 |

## 信号阈值

- MVRV < 1.0 → 深度底部
- MVRV < 1.5 → 偏低估值
- MVRV > 3.5 → 顶部区域
- ETF连续净流入 ≥ 3天 → 机构持续看多
- ETF单日净流入 > $200M → 机构大幅加仓
- 资金费率 > 0.05% → 多头过热
- 资金费率 < -0.05% → 空头过热
- 恐慌指数 < 25 → 极度恐慌
- 贪婪指数 > 75 → 极度贪婪

## 推送效果

```
📊 BTC 每日数据 | 2026-08-20 08:00

💰 价格：$69,318.42  |  24h: 🟢 +7.79%
📈 MVRV：1.32  🟡 偏低
   阈值参考：<1.0底部 | 1.0-1.5偏低 | 1.5-2.5中性 | >3.5顶部
💵 ETF资金流：🟢 $+164.2M  |  连续流入：9天
🎭 恐慌贪婪：62 🟡（Greed）
📉 资金费率：+0.012%

⚠️ 信号预警（2条）：
  🟡 MVRV=1.32 < 1.5 → 偏低估值区域
  🟢 ETF连续净流入 9 天 → 机构持续看多
```

## 部署步骤

### 1. Fork 或使用本仓库

### 2. 创建 Telegram Bot
- 在 Telegram 中搜索 `@BotFather`
- 发送 `/newbot`，按提示设置名称和用户名
- 保存返回的 Bot Token（格式：`123456:ABC-DEF...`）

### 3. 获取 Chat ID
- 在 Telegram 中给你刚创建的 Bot 发送任意一条消息
- 浏览器访问 `https://api.telegram.org/bot<你的TOKEN>/getUpdates`
- 找到 `"chat":{"id": 123456789}`，这个数字就是 Chat ID

### 4. 配置 GitHub Secrets
- 进入仓库 Settings → Secrets and variables → Actions
- 点击 "New repository secret"
- 添加以下两个 Secret：
  - `TELEGRAM_BOT_TOKEN`：你的 Bot Token
  - `TELEGRAM_CHAT_ID`：你的 Chat ID

### 5. 手动触发测试
- 进入仓库 Actions → "BTC Daily Report" → "Run workflow"
- 查看运行日志，确认 Telegram 收到消息

### 6. 完成
- 每天北京时间 08:00 自动推送，无需任何操作

## 定时调整

编辑 `.github/workflows/daily_report.yml` 中的 `cron` 表达式：
- `0 0 * * *` = UTC 00:00 = 北京时间 08:00
- `0 1 * * *` = UTC 01:00 = 北京时间 09:00
- `30 0 * * *` = UTC 00:30 = 北京时间 08:30

## 本地调试

```bash
pip install -r requirements.txt
python main.py
```

未配置 Telegram 环境变量时，脚本仅打印消息不推送。

## 注意事项

- GitHub Actions 每月免费额度 2000 分钟，本任务每天运行约 1 分钟，完全免费
- 部分数据源（Binance）在国内网络可能无法访问，GitHub Actions 服务器在国外可正常访问
- 如某数据源暂时不可用，脚本会跳过该项继续推送其余数据
