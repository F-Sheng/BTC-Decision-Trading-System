---
name: btc-trading-agent
description: |
  用 AI Agent 统筹、Python 代码执行的比特币合约交易系统。Agent 负责用浏览器读 CoinGlass 清算地图、推特专业号与散户情绪、金十快讯并做宏观判断；代码负责算仓位、双向下单、挂止损、移动止损、记账、出图。
  当用户要求「跑 BTC 交易策略」「看清算地图做决策」「自动盯盘开仓」「复盘交易成绩」时使用本技能。
  默认连接 OKX 模拟盘，不会动真钱。
---

# BTC 交易 Agent 工作流

## 角色分工（最重要的一条）

**你是统筹者，不是让代码包办一切。**

| 必须你亲自做 | 必须交给代码 |
|---|---|
| 用浏览器打开 CoinGlass 读清算地图 | 算仓位、止损、止盈 |
| 读推特专业号与散户情绪 | 下单、挂条件单 |
| 读金十快讯并判断宏观方向 | 对账、记账、风控 |
| 判断"这条新闻对 BTC 意味着什么" | 每分钟扫信号 |

**绝对不要**试图用代码去抓 CoinGlass —— 它的 API 返回加密数据，没付费密钥只能拿到 `{"code":"0","success":true}` 而 `data` 字段被抹掉。早期版本这样做，结果只能编一个估算模型冒充真数据，非常危险。

---

## 每小时：数据刷新

### 1. CoinGlass 清算地图

```
mcp__chrome__navigate → https://www.coinglass.com/pro/futures/LiquidationMap
mcp__chrome__screenshot → 读出当前价
mcp__chrome__hover → 在图上取四个点：+3% / -3% / +1.5% / -1.5%
```

**每个点读 tooltip 里的「累计空头清算杠杆」或「累计多头清算杠杆」。**

⚠️ 三条铁律：
1. **必须上下对称**。拿"上方 1.3% 的空头"除以"下方 10% 的多头"毫无意义——早期就是这么算出 9.5 倍的错误结论，实际只有 1.24 倍。
2. **不要把左侧纵轴的单根柱子数值当成累计值**。累计曲线看 tooltip。
3. 写入 `scripts/../data/coinglass.json` 的 `near` / `far` 两档。

### 2. CoinGlass 多空比

```
mcp__chrome__get_text raw → https://www.coinglass.com/LongShortRatio
```

读 Binance / OKX / Bybit 的：散户、大户账户、大户持仓、Smart Money、情绪投票。
另外从行情表格里读 BTC 行的 **Whale bias**（形如 `1.6738 62.60% 37.40%`）。

### 3. 推特情绪（分层）

```
专业号：https://x.com/search?q=%28from%3AColeMacro%20OR%20from%3AKillaXBT%20OR%20from%3Awoonomic%20OR%20from%3ARaoulGMI%20OR%20from%3ANickTimiraos%29%20BTC&f=live
散户号：https://x.com/search?q=%24BTC%20lang%3Aen%20min_faves%3A1500&f=live
```

```
mcp__chrome__evaluate:
  document.querySelectorAll('article') → innerText
```

**每条必须带 `tier`（pro / retail）和中文翻译 `text_cn`**，写入 `data/x_posts.json`。

- `pro`：专业交易员、宏观研究员、链上分析师（白名单在 `core/x_sentiment.py`）
- `retail`：散户、喊单号、营销号 → **按反向指标处理**

### 4. 宏观新闻（这一步只有你能做）

打开 `https://www.jin10.com/` 或金十快讯，挑 3-5 条与 BTC 定价相关的：

- 美联储 / 利率 / 通胀数据
- 原油 / OPEC
- 中东局势 / 战争 / 停火
- 特朗普 / 关税 / 大选
- 监管 / ETF

**判断它们对短期方向的影响，写进汇报。** 不要指望代码的关键词匹配能做到这件事。

### 5. 出图并汇报

```bash
python scripts/render_card.py
```

汇报必须包含：六层五档结论、决策路径、关键价位、持仓（双向两条腿）、最近信号。

---

## 每天：复盘

```bash
python scripts/review.py
```

重点看四件事：

1. **信号漏斗**：生成多少条、采纳几条、未执行的原因
2. **成交**：胜率、平均 R、单笔明细
3. **数据健康**：CoinGlass 是否过期、真实强平流笔数、推特样本数
4. **参数体检**：有没有"全部未执行""零信号"这类异常

**如果发现"信号 25 条只采纳 1 条"，说明门控过严，要给具体调整建议（改哪个参数、改成多少），不要泛泛而谈。**

---

## 参数标定节奏

| 频率 | 做什么 |
|---|---|
| 每 4 小时 | 出决策图，报当前状态 |
| 每天 08:00 | `review.py` 复盘 |
| **攒够 10 笔平仓后** | 用 `backtest.py` 重新标定 `min_confidence`、`NEAR_TOL`、各层权重 |
| 每周 | 核对 L1 九项、L2 燃料/持仓、情绪合成的权重 |

**不做标定 = 永远在拟合噪声。**

---

## 五条红线

1. **不要用编造的模型冒充真数据**。清算估算模型（`core/liquidation.py`）只是兜底；CoinGlass 过期时应判中性或改用 `core/liq_live.py`（OKX 真实强平流）。
2. **不要在数据过期时静默降级**。必须写进汇报。
3. **不要单边做空**。双向模式下，做空的同时可以开多单吃反弹，两条腿各自止损止盈。
4. **不要为了"有信号"而放宽阈值**。`NEAR_TOL` 从 0.3% 调到 0.5% 就是因为不触发放宽的——典型过拟合。
5. **不要动用户手动持有的仓位**，除非明确授权。

---

## 关键参数速查

| 参数 | 默认 | 含义 |
|---|---|---|
| `leverage` | 10 | 杠杆 |
| `margin_pct` | 0.33 | 单笔保证金占账户上限 |
| `account_risk_target` | 0.05 | 单笔风险预算（保证金由止损反推） |
| `stop_pct_min/max` | 0.012 / 0.020 | 止损距离范围 |
| `tp_weights` | [0.4, 0.35, 0.25] | 三档止盈比例 |
| `trail_lock_r` | 0.5 | 移动止损锁定比例 |
| `min_confidence` | 0.45 | 开仓置信度门槛 |
| `counter_size_floor` | 0.33 | 逆势仓位下限 |
| `max_positions` | 2 | 最大同时持仓（双向模式下多空各算一个）|

---

## 故障排查

| 症状 | 检查 |
|---|---|
| 长时间没信号 | `python diag.py`；日志里 `[trace] A漏斗` 会写明卡在哪一步 |
| 有信号不开仓 | 看日志"未执行原因"：置信度不足 / 已持仓 / 总保证金超限 |
| 订单被拒 | 双向模式必须带 `posSide`；确认 `posMode` 为 `long_short_mode` |
| 止损单不见 | `python tools/tools_fix_stops.py` 按本地状态逐边重挂 |
| 机器人不跑 | `tools/restart_bot.ps1 -StatusOnly`；日志超 3 分钟不更新视为卡死 |
| 平仓平不掉 | 双向模式下要按 `posSide` 分别平，见 `tools/tools_flat_all.py` |
