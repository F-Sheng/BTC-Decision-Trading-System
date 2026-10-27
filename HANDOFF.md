# HANDOFF · BTC 自动交易系统 · 交接文档

> **新会话第一句话就说**：
> **「读 D:\DP workspace\btc_system\HANDOFF.md，按里面的状态继续」**
>
> 这份文件是唯一入口。读完就能完全接上，**不需要读旧对话**。
> 最后更新：2026-10-27 17:58 CST

---

## 0. 一句话现状

**OKX 模拟盘跑 V3 策略，持有空单 338.1 张（浮盈 +7.16% 保证金收益率），系统健康，等下 4 小时出图、每天 08:00 复盘。**

---

## 1. 你的角色（最重要，先看这个）

**你是统筹者，不是执行者。**

| 谁做 | 做什么 |
|---|---|
| **机器人 `run.py`**（独立进程，60 秒一轮） | K线、指标、L1-L5 判断、开平仓、止损、移动止损、分批止盈、OKX 强平流 |
| **你（Agent）** | CoinGlass 浏览器取数（无 API 权限）、X 情绪、金十、宏观判断、出图、复盘 |

**铁律：不要把取数/判断推给代码。** 用户明确反对过多次。

**机器人不需要你也能跑。** 你重启会话、关电脑、断网，它照跑（Windows 任务 `BTC_Watchdog` 每 5 分钟检查存活）。

---

## 2. 关键路径

```
项目根     D:\DP workspace\btc_system
报告图     默认 %TEMP%\btc_card_HHMM.png（不落桌面）
           加 --archive 才存 C:\Users\81559\Desktop\BTC决策报告\YYYY-MM-DD\
日志       logs\btc-YYYYMMDD.log
状态       state\bot_state.json + state\journal.db
数据       data\coinglass.json, data\x_posts.json
GitHub     https://github.com/F-Sheng/BTC-Decision-Trading-System
本地仓库   D:\DP workspace\BTC决策交易系统
Python     C:\Users\81559\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
```

---

## 3. 当前持仓（2026-10-27 17:56 快照）

```
方向      做空（short）
张数      338.1 张 = 3.381 BTC
名义      271,294 U
保证金    27,129 U（10 倍杠杆，33% 仓位）
开仓      80,240.7
现价      79,689.3（浮动 +0.69%）
本仓浮盈  +1,942.92 U（保证金收益率 +7.16%）
止损      81,493.8（交易所条件单 state=live）
止盈 TP1  79,168 → 平 40%
止盈 TP2  76,456 → 清空剩余
模型      D（反向信号触发）
开仓时间  10-27 08:46:22
机器人    pid 26268（10-27 12:46 用 maker 代码重启）
```

**权益** 85,860.84 U ｜ **V3 基准** 84,478.20 U（10-27 01:07 起）｜ **V3 至今** +1,382.64 U（+1.64%）

---

## 4. 定时任务（3 个，都活着）

| ID 尾号 | 频率 | 内容 | 出图? |
|---|---|---|---|
| `98855d86` | **每 30 分钟** | CoinGlass 清算地图 + 多空比 + X 推特 + 金十 | ❌ **禁止出图** |
| `d88f63a9` | **每 4 小时** | perf 体检 + 止损单检查 + 账目核对 | ✅ **只有它出图** |
| `21c966b5` | **每天 08:00** | review.py 复盘 | ✅ 出图 |

---

## 5. 用户偏好（严格遵守）

### 汇报格式
- **30 分钟任务：纯文字 2-3 行。** 禁止 render / present / read_image / 保存 PNG
- **图片放回答最末尾**，不要放开头
- 只有「开仓/平仓/止损」「数据源故障」「判断与持仓冲突」才展开

### 持仓区配色（已实现，别改回去）
- 六个 label（保证金·10x / 实际持仓 / 开仓价 / 现价 / 本仓盈亏 / 盈利率）→ **纯白加粗** `(240,245,250)`
- 数值 27,129 / 271,294 / 80,240.7 → **金色** `(232,184,75)`
- 止盈绿 / 止损红，放**同一个方框**（上止盈、下止损）
- 本仓盈亏 U 金额 + 盈利率 % **严格跟随正负显示绿/红**
- 审美参考：Claude 暖中性 + 高对比层级

### 其他
- 用户拒绝「影子账户」话术，直说
- **用户要诚实，不要编。编过一次被抓住**
- CoinGlass **无付费 API**，只能浏览器取数

---

## 6. 当前代码状态（都已上线）

### 6.1 maker 订单（10-27 12:46 上线）
```python
# core/executor.py
_maker_fill()      # post_only 限价单，卖=bid+1tick，买=ask-1tick
_place_entry()     # 优先 maker，24秒不成交回退市价
```
- 开仓：maker（0.05% → 0.02%，省 60%）
- 平仓：反向/止盈用 maker；**止损类（stop/soft_stop/exchange_stop/policy_flatten/trail_stop）强制市价**
- config：`maker_enabled=true, maker_wait_s=24, maker_requote=2, maker_fallback_market=true`

### 6.2 输出路径
`render_card.py` 默认写 `%TEMP%\btc_card_HHMM.png`，加 `--archive` 才存桌面。

### 6.3 关键参数（config.json）
```
strategy_version         V3
leverage                 10
margin_pct               0.33      (满仓，用户选的)
account_risk_target      0.05      (单笔 5% 账户风险)
stop_pct_min             0.0155
stop_pct_max             0.02
touch_tol_usd            100.0     (K线触碰容差)
tp_weights               [0.4,0.35,0.25]
trail_lock_r             0.5
min_confidence           0.45
counter_size_floor       0.33
max_positions            2
run_started_at           1793034476691  (10-27 01:07:56)
run_start_equity         84478.20
```

---

## 7. 五层策略架构（V3）

```
L1 宏观   核心CPI/CPI动能/能源CPI/WTI/纳指VIX/美元/2Y收益率/Coinbase溢价/地缘政治
L2 资金   清算地图 R 值（上3%空头 vs 下3%多头）+ 持仓结构（散户/大户/背离/OI/买卖比/费率）
L3 技术   MA5/10/20/60 + MACD + RSI + BOLL + ATR，日线/4H/1H/15M 四周期
L4 事件   FOMC/CPI/期权到期/中期选举 + 突发新闻情绪
L5 风控   精确 5% 账户风险 + 1.55-2.0% 止损 + 分批止盈 + 移动止损
```

**方向 = L1 + L2 + L3 合成，L4 调节仓位。**

### 关键价位来源（core/targets.py）
5 类候选位打分，共振越多越优先：
```
swing(分形摆动,3) > pivot(枢轴,2) = vp(成交量分布,2) = liq(清算,2) > fib(斐波那契,1)
```
止盈 = 打分前两名。**当前空单 TP1=79,168 就是前低（swing 类）。**

### 分批止盈机制
```
pnl 达到 TP1 → 平 40%，止损立刻移到开仓价（保本）
pnl 达到 TP2 → 清空剩余
若只有 2 档 → 权重归一化成 40% / 60%（第 3 档浪费，已知问题）

移动止损：盈利 ≥1R 后，每多赚 0.5R，止损推到「峰值 − 0.5R」，只进不退
```

### 反向信号机制
```python
# run.py:350
if sig.confidence >= min_confidence + 0.17 and sig.size_mult >= 0.25:
    # 平掉反方向持仓，给新方向让路
```
即 **conf ≥ 0.62** 时切断逆势仓位。10-27 08:46 就是这样平了多单。

---

## 8. 数据源与铁律

| 源 | 取法 | 频率 |
|---|---|---|
| CoinGlass 清算地图 | **浏览器截图 + hover 读 tooltip** | 30 分钟 |
| CoinGlass 多空比 | 浏览器 evaluate 表格 | 30 分钟 |
| X 推特 | 浏览器 evaluate article | 30 分钟 |
| 金十快讯 | `python tools_jin10_latest.py` | 30 分钟 |
| OKX 强平流 | 机器人自己抓 | 60 秒 |

### 清算地图铁律
1. **上下距离必须对称**（±1%~±1.5%），窄于 ±1% 读数不可信
2. **只读 tooltip 的「累计空头/多头清算杠杆」**，绝不读左侧纵轴柱子数值
3. 每次报告说明用的窗口

### X 推特必做重试
X 搜索页经常报「出错了。请尝试重新加载」→ **点击「重试」再 evaluate**：
```js
const els = [...document.querySelectorAll('div[role="button"], span, button')];
const b = els.find(e => (e.innerText||'').trim() === '重试');
if (b) b.click();
```

### chrome-control 排障
工具报 "fetch failed" / "No Chrome extension is attached" → **Chrome 没在运行**。启动 Chrome，扩展「Deepseek Harness APP」自动重连。

---

## 9. 历史事件研究结论（10-27 新建，真实数据）

数据：OKX BTC-USDT-SWAP 1H K线，2023-04 ~ 2026-10，21,900 根
脚本：`event_study.py` ｜ 结果：`data/event_stats.json`

| 事件 | 样本 | T+24h | 上涨比例 | T+72h |
|---|---|---|---|---|
| 基线（随机） | 130 | +0.14% | 52% | +0.15% |
| **FOMC** | 28 | +0.12% | 57% | **−0.37%（中位 −0.84%）** |
| **期权到期** | 42 | **−0.28%** | **38%** | −0.05% |
| **周一** | 186 | +0.26% | 53% | +0.65% |

**三个可用结论：**
1. **FOMC 后 1~3 天偏跌**（T+72h 中位 −0.84%，仅 46% 上涨）→ 利好空头
2. **期权到期后 24h 有 62% 概率下跌**（到期前 T+1h/T+4h 上涨比例 67%，到期后反转）
3. **周一波动放大 1.24x**

**局限（诚实说明）**：所谓"振幅"是收盘涨跌幅绝对值，不是日内高低差；FOMC 时间统一用 02:00（冬令时实际 03:00）；中期选举只有 1 个样本；CPI/NFP 因拿不到可靠历史日期未做。

---

## 10. 已修复的 8 个 bug（别改回去）

| # | Bug | 后果 |
|---|---|---|
| 1 | `pnl_usd` 硬编码 ×10 | 盈亏虚高 1000 倍 |
| 2 | 清算比值不对称比较 | 算出 9.5 倍，真实 1.24 |
| 3 | 启动接管用 `pos` 正负判方向 | 空单被认多单，**裸空 28 万无止损** |
| 4 | `flatten.py` 同款方向 bug | 平仓变加仓 |
| 5 | K 线没碰到阻力也开仓 | 坏信号，已加 `touch_tol_usd=100` |
| 6 | 关键位池缺 BOLL/MACD/RSI | 阻力位不完整 |
| 7 | 移动止损 R 值分母归零 | 移保本后止损失效 |
| 8 | 金十时间戳解析错误 | 引用过期新闻 |

**双向持仓（hedge）模式：所有订单必须带 `posSide`；`pos` 恒为正，方向只能读 `posSide`。**

---

## 11. 常用命令

```powershell
cd "D:\DP workspace\btc_system"
$PY = "C:\Users\81559\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"

& $PY tools_pos_now.py       # 持仓
& $PY tools_check4h.py      # 体检 + 账目核对
& $PY perf.py               # 成绩单
& $PY review.py             # 复盘
& $PY render_card.py        # 出图到 temp
& $PY render_card.py --archive  # 出图到桌面
& $PY event_study.py        # 事件研究

# 进程存活
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*run.py*" }

# 重启机器人
Stop-Process -Id <pid> -Force
Start-Process -FilePath $PY -ArgumentList "run.py" -WorkingDirectory "D:\DP workspace\btc_system" -WindowStyle Hidden
```

---

## 12. 待办 / 已知问题

### 未解决
1. **手续费占浮盈 20%** —— maker 已上线，等下一笔开仓验证
2. **`perf.py` 峰值被污染** —— 显示 87,861（含 V1 数据），回撤虚高 −3.23%，实际 −0.91%
3. **部分止盈的已实现盈亏没记账** —— `trades` 表只在全平后写，TP1 落袋的钱没入账
4. **`reconcile` 用止损价伪造成交价**
5. **回测有前视偏差** —— `resample().last()` + ffill；`DummyLiq` 是假数据
6. **信号层止损与风控层不一致** —— 08:46 那条信号自带 SL=0.63%，执行时被放大到 1.56%
7. **TP 只算出 2 档**，`tp_weights` 配了 3 档，第 3 档浪费

### 建议下一步
- 把历史事件结论写进 L4 打分（FOMC T+3 偏跌、期权到期后偏跌）
- 考虑 CoinGlass 付费 API（约 $29/月），省掉每轮 3 张截图

---

## 13. 宏观背景（2026-10-27 18:00）

- **FOMC：10-28（明天）** —— 研究显示 T+1~T+3 偏跌
- 美国中期选举：2026-11-03
- 核心通胀 2.79%（高于 2% 目标），通胀动能 1.12pt 加速
- WTI ~101，布伦特 ~115.8
- 美元指数 ~102，VIX ~18
- 地缘：俄乌（俄军打击基辅/哈尔科夫），中东（沙特/土耳其/巴基斯坦赴德黑兰调停）
- 新变量：美国财政部明日回购 60 亿美元长期国债（流动性宽松，对空头不利）

### 当前行情快照（17:56）
```
价格 79,689    清算 R=0.37（下多 248M vs 上空 93M）
鲸鱼 37.54% 多（净空 62.46%）    Smart Money 极度看空
24h 清算总额 1.32 亿（−14%，持续降温）
```

---

## 14. 策略演进史（简版）

```
V1（10-26 20:21）100 倍杠杆 + 无止损 + 单向
   → 一笔 −2,617 U（−3%），教训：杠杆是最大变量
V2（10-27 00:01）10 倍杠杆 + 1.2-2.0% 止损钳制 + 双向持仓 + 结构止盈
   → 程序验证通过，但首笔坏信号 −284 U（K线没碰到关键位）
V3（10-27 01:07，当前）K线触碰校验 ±100点 + BOLL进关键位池 + RSI/MACD共振 + 风险精确5%
   → 至今 +1,382 U
```

**总账（10-26 20:21 起）：87,393 → 85,860（−1,532 / −1.75%）**
**V3 期：84,478 → 85,860（+1,382 / +1.64%）**

---

## 15. 接手后第一件事

```powershell
cd "D:\DP workspace\btc_system"
& "C:\Users\81559\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" tools_check4h.py
```

确认：**持仓没变、止损单还在、账目对得上。**

**然后什么都别做，等定时任务来。** 30 分钟一次纯文字，4 小时一次图。

---

## 16. 上下文成本控制（重要）

**每个会话不要跑太久。**

- 一个会话跑 150 轮后，转录文件能到 9 MB，每轮都要重发全部历史（虽有 prompt cache 打折，仍然很贵）
- **建议每 12 小时重启一次会话**：先把新状态更新进这份 HANDOFF，再开新会话
- 更根本的办法：能被规则写死的判断就写进 `run.py`，不要消耗 Agent

**分工原则：机器人做能自动化的，Agent 只做它做不到的。**

---

## 17. 【待实施】本地取数架构（用户 10-27 晚决定的方向）

### 问题诊断
当前架构的缺陷：**Agent 直接截图 → 图片永久留在对话转录里**。
今天截了 229 张图（每张约 1,500 token ≈ 34 万 token），每轮都要重发一遍，
这是 token 消耗的真正主因（占 90%+）。

### 正确架构
```
本地层（零 Agent token，图片永不进 Agent 上下文）
  btc_system/local_fetch.py
   · 每 30 分钟：连 CDP 127.0.0.1:37086 → 截图（4-5 张）
   · 调本地 Qwen2.5-VL-3B 读图（不用 OCR，OCR 对图表读数不准）
   · 追加一行到 data/market_snapshot.jsonl
   · 图片归档到 data/snapshots/，不传 Agent
   · 异常（抓取失败 / 距止损<0.5% / R 突变>50%）写 flag

Agent 层（每轮只读约 540 token 文本）
  · 读 market_snapshot.jsonl 最后 8 行（趋势）
  · 判断方向/仓位
  · 输出 2-3 行
```

### 数据契约 data/market_snapshot.jsonl（已在 18:15 开始记录）
```json
{"ts":"2026-10-27T18:15","px":79530,"r":0.49,
 "up_px":80543,"up_short_m":116.88,"dn_px":79003,"dn_long_m":238.17,
 "whale_long_pct":39.76,"smart":"Extremely Bearish","liq24h_m":135.02,
 "vol24h_b":151.80,"pos":{"side":"short","entry":80240.7,"size":338.1,
 "sl":81493.8,"tp1":79168,"upl_pct":0.89}}
```
每行约 113 token。

### 硬件实测
```
CPU    i5-12400F (6C/12T)
内存   16GB 总 / 6.9GB 可用    <- 最紧
显卡   RTX 3050 8GB (8192MiB, 算力 8.6, CUDA 13.4)   <- 够用
磁盘   E: 149GB 可用            <- 模型放这里
```
**推荐模型：Qwen2.5-VL-3B-Instruct Q4_K_M（约 2.3GB 显存）**
- 备选：InternVL3-2B（1.5GB，更快）
- 不要用 Qwen2.5-VL-7B（5.5GB，内存不够易 OOM）

部署：
```powershell
$env:OLLAMA_MODELS="E:\ollama-models"
ollama pull qwen2.5vl:3b     # 约 3.2GB
```

### 为什么不用 OCR
工具提示浮在 K 线图上、半透明、有重叠 -> 传统 OCR（RapidOCR/PaddleOCR）读数不准。
必须用 VLM 做语义读图。用户明确指出过这一点。

### 为什么判断不能交给脚本
脚本能把阈值写死（R>1 就怎样），但写不出这种判断：
「下方 248M 多头是空头燃料，但上方空头只有 93M，意味着反弹时没有空头爆仓推动力」。
**感知可以外包给本地模型，判断必须 Agent 做。**

### 实施顺序
1. 装 Ollama + Qwen2.5-VL-3B
2. 用 data/snapshots/ 里已存的真实截图测识别准确率（不花钱）
3. 准了 -> 写 local_fetch.py
4. 把 30 分钟定时任务改成「读 JSONL + 判断」，不再调浏览器截图工具
5. 每 12 小时滚动一次会话

### 预期效果
| | 现在 | 改造后 |
|---|---|---|
| 每轮输入 | ~150,000 token | ~6,000 token |
| 48 轮/天 | ~720 万 | ~29 万 |
| 节省 | | **96%** |

### 关键认知（用户纠正的）
- 错：降频到 4 小时来省钱
- 对：**贵在上下文长度，不在唤醒频率。短上下文 + 高频判断才对。**
- 错：让脚本替代判断
- 对：**本地模型只做眼睛（感知），Agent 做大脑（判断）**

### 相关文件
- `data/market_snapshot.jsonl`  已开始记录（每轮追加一行）
- `tools_snapshot_append.py`    追加快照的脚本
- `data/snapshots/`             （待建）截图归档目录
