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

---

## 18. 【已完成】Ollama + Qwen2.5-VL-3B 本地部署与验证（2026-10-27 18:35）

### 安装位置（全部在 E 盘，C 盘零负担）
```
E:\btc-ai\ollama          3,136 MB   Ollama 程序本体（v0.40.11）
E:\btc-ai\models          3,052 MB   模型权重（qwen2.5vl:3b）
E:\btc-ai\tmp                 0 MB   临时目录（OLLAMA_TMPDIR）
E:\btc-ai\downloads       1,585 MB   安装包（可删，省 1.6GB）
E:\btc-ai\logs                      测试结果
E:\btc-ai\test-images               历史基准图（带已知答案）
E:\btc-ai\snapshots\1815            18:15 现场取数截图
E:\btc-ai\vl_coin.py                 生产版读取器
```
**C 盘只被占用了 2.4 MB（`C:\Users\81559\.ollama` 的软链记录），可用空间仍 20.2 GB。**

已设置的持久化环境变量（用户级）：
```
OLLAMA_MODELS = E:\btc-ai\models
OLLAMA_TMPDIR = E:\btc-ai\tmp
```
**注意：新开的 PowerShell 进程需要手动补 PATH：`$env:PATH = "E:\btc-ai\ollama;$env:PATH"`**

### 模型
```
NAME            ID              SIZE    显存占用
qwen2.5vl:3b    fb90415cde1e    3.2 GB  ~2.3 GB
```

### 安装踩坑记录
| 问题 | 原因 | 解决 |
|---|---|---|
| `Invoke-WebRequest` 下载崩溃 | 报 "Insufficient memory"，该命令把整个文件读进内存 | 改用 `curl.exe -L -o` 流式下载 |
| github.com 不通 | 网络限制 | ollama.com 的 307 重定向最终指向 release-assets.githubusercontent.com，**可以连通** |
| 非管理员安装 | 当前 shell 非 admin | Inno Setup 的 `/DIR=` + `/VERYSILENT` 参数**不需要管理员**也能装到自定义路径 |

### 验证结果（3 张历史图 + 1 轮现场新数据）

**历史图测试（2 轮）**
| 读取项 | 准确率 | 结论 |
|---|---|---|
| 顶部现价 | 2/2 | 稳定 |
| 上方累计空头 116.88M | 2/2 | 稳定 |
| 下方累计多头 238.17M | 2/2 | 稳定 |
| tooltip 内价格 | 原提示词 0/2 | **措辞敏感**：会把顶部现价当答案 |
| tooltip 内价格（改述后） | 4/4 | 提示词里明说"图里有两个价格"即可 |

**现场对比测试（18:15 新取一轮，Agent 先读做标准答案）**
| 图 | Agent 读数（标准答案） | 本地模型 v2 | 一致? |
|---|---|---|---|
| base | 现价 79,849 | 79,849 | ✅ |
| upper | tooltip 80,722 / 空头 181.48M | 80,722 / 181.48M | ✅ |
| lower | tooltip 78,577 / 多头 394.90M | 78,577 / 394.90M | ✅ |

**3/3 完全一致，0.65–0.81 秒/张（温启动），冷启动 68.6 秒。**

### 修掉的 3 个问题
1. **幻觉**：上方 tooltip 里没有 `cumulative_long`，模型却报了 394.90M
   → 传入 hover 方向参数，只要求对应字段
2. **JSON 解析失败**：模型输出裸值 `"cumulative_long": 394.90M`（无引号）
   → 正则给裸值补引号再 parse
3. **数字格式不一**：`79.849` vs `79849`
   → `norm_price()` 统一：小于 1000 视为千分位误写，×1000

### 生产版用法
```powershell
$env:OLLAMA_MODELS = "E:\btc-ai\models"
$PY = "C:\Users\81559\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"

& $PY E:\btc-ai\vl_coin.py base  <图>
& $PY E:\btc-ai\vl_coin.py upper <图>    # hover 在现价上方 -> 要 cumulative_short
& $PY E:\btc-ai\vl_coin.py lower <图>    # hover 在现价下方 -> 要 cumulative_long
```
输出：
```json
{"kind":"upper","top_current_price":79849,"tooltip_price":80722,"sec":0.81,"cumulative_short_m":181.48}
```

### 成本对比
| | 改造前（Agent 读图） | 改造后（VLM 读图） |
|---|---|---|
| 每轮图片 token | ~4,500 | **0** |
| 每轮文本 token | 0 | ~60 |
| 图片是否留在上下文 | 永久累积（200 轮后约 34 万 token） | **从不进入** |
| 上下文增长 | 每轮 +4,500 | 每轮 +60 |

**图片 token 降低 98.7%，且彻底不再累积。**

### 对比记录文件
- `E:\btc-ai\logs\comparison-record.json`（完整结构化记录）
- `D:\DP workspace\btc_system\data\vl-comparison-record.json`（项目内副本）

### 下一步（还没做）
1. **写 `local_fetch.py`**：Playwright 连 CDP 37086 自动截图 → 调 `vl_coin.py` → 追加 `market_snapshot.jsonl`
2. **用 VLM 读坐标轴标签解决 hover 不对称**：今天全靠肉眼估 ±1.2%，经常上下距离不等（如 18:15 那次是 +1.09% vs -1.59%）。让脚本读轴标签算精确像素，这是 VLM 带来的额外收益
3. **改定时任务**：30 分钟任务改成「读 JSONL + 判断」，不再调浏览器截图工具
4. `E:\btc-ai\downloads\OllamaSetup.exe` 可以删了（省 1.6 GB）

---

## 19. AgentView（B+C）· 本地模型校验 · 自我复盘 · 已上生产

### 19.1 AgentView 方案 B+C（2026-10-27 20:25 上线，已接到生产）
**目的：把我的「判断」变成机器人能读的字段，不再只是聊天里说话。**

`data/agent_view.json`（我写，机器人读）：
```json
{
  "ts": "2026-10-27T20:25",
  "mode": "live",
  "direction_hint": "short",
  "confidence": 0.45,
  "size_mult": 0.75,
  "veto_new": false,
  "reasons": ["R(3%)=1.08 上下燃料接近平衡", "5条地缘降级信号但均无实质进展", "明日FOMC"],
  "risk_flags": ["fomc_t1", "deescalation_talk", "r_neutral"]
}
```

**接入点**：`run.py` 的 `if plan.ok:` 之后，`core/agent_view.py`。

| 字段 | 效果 |
|---|---|
| `size_mult` | **方案 B**：乘到 `plan.size_contracts`，**只缩不放**（0~1） |
| `veto_new` | **方案 C**：`true` 时跳过本次开仓，**已有仓位的止损/止盈/移动止损不受影响** |
| `mode` | `"shadow"` 只记录不生效；`"live"` 真正生效 |
| 过期 | 超过 **90 分钟**自动视为「无意见」，退化为 size_mult=1、veto=false |

**安全设计**：
- 任何异常都退回「无意见」，绝不阻塞交易主流程
- 影响记录到 `data/agent_view_log.jsonl`，可事后评估它到底有没有用
- 日志里会打印 `AgentView[鲜Xm live] short conf=0.45 size=0.75 veto=False`

**写 AgentView 的方法**（Agent 用）：
```python
from core import agent_view
agent_view.write_view(direction="short", confidence=0.45, size_mult=0.75,
                      veto_new=False, reasons=[...],
                      flags=["fomc_t1"], mode="live")
```

### 19.2 本地模型只读校验（2026-10-27 20:25 起跑，跑满 1 天后评估替换）
- 每轮把三张截图存 `E:\btc-ai\snapshots\HHMM\`
- 跑 `python E:\btc-ai\vl_coin.py base|upper|lower <图>`
- 我的读数与 VLM 读数对比：
  - 一致 → 答「VLM ✓」
  - 不一致 → **必须判断谁错**，写进本文件 19.4 节
- **此阶段不替换我的读数，只做交叉校验**

**已知 VLM 缺陷（需持续观察）**：
| 现象 | 出现频率 | 处理 |
|---|---|---|
| `top_current_price` 多乘 10（798,640） | 偶发 1/6 | 加 `>200000` 异常校验 |
| 裸值 `394.90M` 未加引号导致 JSON 解析失败 | 已修 | `coerce_json()` 正则补引号 |
| 幻觉报出不存在的 cumulative_long | 已修 | 按 hover 方向只要求对应字段 |
| 冷启动首次推理 68 秒 | 每次重启后 | 预热一次即可 |

### 19.3 清算地图口径统一为 3%（2026-10-27 20:25）
- 原因：机器人自算的 `L2[PROXY]` 用 **3% 窗口**，我之前读 **1%**，两者不可比
- 切换后实测：1% 口径 R=2.62 → **3% 口径 R=1.08**，与 proxy 的 0.77 方向一致
- **教训：窗口不同，R 可以差 2.4 倍。以后报告 R 必须注明窗口。**

**x 轴非线性（重要）**：
CoinGlass 清算图的 x 轴标签越靠近现价越密，**用像素估算百分比会有系统误差**。
正确做法：hover 后读 tooltip 里的真实价格，反算实际百分比，再决定是否调整。

### 19.4 自我复盘日志（每 4 小时追加）

| 时间 | 判断 | 实际 | 对/错 | 原因归类 | 改进动作 |
|---|---|---|---|---|---|
| 10-27 08:46 | 建议平多（L3 转强烈看空） | 系统同时自动 reverse_signal 平多，之后价格从 80,274 跌到 79,600 | ✅ 对 | — | — |
| 10-27 全天下跌判断 | 坚持空单 | 空单浮盈最高 +7.16% | ✅ 对 | — | — |
| 10-27 20:22 | 建议「减仓或平仓」空单（因伊朗降级信号） | 用户指出：伊朗表态无实质进展、美国仍在整军，权重不足 | ❌ 错 | **权重给高了：把单方面表态当成实质进展** | jin10 工具加「实质/表态」区分：表态降权 45%，实质加权 +2 |
| 10-27 全时段 | 反复称"峰值 87,861 是 V1 残留" | 实测是 OKX 模拟盘 totalEq 单点毛刺（14:52 价格只动2点、权益跳1,677） | ❌ 错 | **诊断错误：没做价格反推验证就下结论** | perf.py 加滚动中位数去毛刺；以后异常数据先用价格反推理论值核对 |

### 19.5 新闻漏报事故与修复（2026-10-27 20:15）

**事故**：漏掉伊朗最高领袖「落实条件结束战争」、普京「考虑结束冲突解除制裁」两条重大新闻。

**根因（4 条）**：
1. 🔴 我用 `python tools_jin10_latest.py | Select-Object -First 3` **截断输出**，每轮只看到前 2 条噪音（个股公告/辟谣）
2. 🔴 老工具按**时间倒序**排，不按重要度，噪音占据顶部
3. 🟡 金十 API 单次只返回最近 **~15-20 分钟约 21 条**，不存档就无法回查
4. 🔴 **认知偏差：只注意升级信号（打击/导弹/制裁），漏掉降级信号（停火/结束冲突/解除制裁）**

**修复**：
- 新工具 `tools_jin10.py`：
  - 按**重要度**排序（战争=10、美联储=9、中东=7…）
  - 只输出关键词命中的条目，噪音（辟谣/ST/盘初/直播）丢弃
  - `--hours N` 时间窗口
  - 自动存档 `data/news_log.jsonl`，可回查
  - **区分「实质进展」与「单方面表态」**：表态降权 45%，实质进展加权 +2
  - 修了 time 字段格式不稳定（可能是 `'YYYY-MM-DD HH:MM:SS'` 字符串）
- 定时任务明确禁止截断输出，要求地缘新闻**双向扫描**

**当前新闻权重表**（`tools_jin10.py` 的 `WEIGHTS`）：
```
战争/停火/停战 10 | 和平/结束冲突/哈梅内伊/霍尔木兹 9
美联储/鲍威尔/FOMC/利率决议 9 | 伊朗/以色列/导弹/降息/加息/CPI/中期选举 8
中东/打击/制裁/关税/特朗普/比特币/原油/OPEC/油价 7~8
```

### 19.6 自我优化机制（写进 4 小时任务）
每 4 小时必须回顾：
1. 过去 4 小时每 15 分钟的判断，方向对不对？（用当时价格 vs 现在价格对照）
2. 哪次被证伪？原因归类：**数据源错 / 权重错 / 漏信息**
3. 结论写进 19.4 节表格
4. 需要改参数/权重/流程的，**先提方案问用户，不自行改交易逻辑**

### 19.7 已修复的小问题
| 问题 | 修复 |
|---|---|
| `perf.py` 峰值被毛刺污染（87,861 假峰） | 滚动中位数去毛刺（偏离 >800U 剔除），峰值回到 86,425，回撤 -3.82% → -2.23% |
| 权益毛刺无提示 | perf.py 输出「⚠ 已剔除 N 个权益毛刺」 |
| 1%/3% 窗口混用 | 统一 3%（与 proxy 对齐） |
| V1 残留（误判） | **实际不存在**，是我诊断错了；真因是模拟盘 totalEq 毛刺 |

---

## 20. 2026-10-27 夜间大改 · 最终交接（新会话从这里读）

### 20.0 一句话现状
系统已全自动：Agent 只做统筹与判断，`local_fetch.py` 用 CDP + 本地 VLM 取数（图片不进 Agent 上下文），
机器人独立跑策略。当前双持仓（多 85.8 张 / 空 86.6 张），明日 FOMC。

---

### 20.1 架构总览（务必先理解这个）

```
┌─ Chrome-自动化 (独立 profile, 调试端口 9222) ─┐
│  E:\btc-ai\chrome-profile                    │
│  已登录 X / CoinGlass（cookie 持久化）         │
└───────────────┬───────────────────────────────┘
                │ CDP (websocket, suppress_origin=True)
                ▼
      local_fetch.py  ← 每 15 分钟由 Agent 调用
        ├─ 清算地图：截图 → 差分定位 tooltip → 裁剪 → 本地 VLM 读
        ├─ 多空比：DOM 抽表格（3 家交易所）
        ├─ X 专业/散户：滚动 + aria-label 解析互动数
        └─ 写 data/coinglass.json / data/x_posts.json / market_snapshot.jsonl
                │
                ▼
      Agent（我）：读 JSON + 金十新闻 → 判断 → 写 data/agent_view.json
                │                                    │
                │  （只写数据文件，绝不下单）          │
                ▼                                    ▼
      ┌──────────────────────────────────────────────────┐
      │  run.py 机器人（60 秒一轮，独立决策）              │
      │  L1 宏观 → L2 清算 → L3 技术 → L4 事件 → L5 风控  │
      │  读 coinglass.json + agent_view.json               │
      │  自己算出方向 → 下单/平仓/止损/止盈                │
      └──────────────────────────────────────────────────┘
                │
                ▼
      OKX 模拟盘（x-simulated-trading: 1，双向持仓模式）
```

**关键分工（用户反复强调过）：**
- **Agent = 统筹**：取数、判断、复盘、优化策略参数，**不下单**
- **机器人 = 执行**：独立策略引擎，Agent 的判断通过 `agent_view.json` 影响仓位大小，不直接指挥

---

### 20.2 今晚修掉的 9 个问题（按重要性）

#### ① `reverse_signal` 允许逆势信号推翻顺向仓位【逻辑 bug】
- **现象**：21:47 系统方向仍是 `short`，但模型 B 一个标着【逆势】的 LONG 信号 conf=0.70，
  把持有 13 小时的空单平掉了（80,240.7 → 80,133.2，只赚 204 U，最高曾浮盈 1,940 U）
- **根因**：`run.py` 原条件只看信号自身置信度，不检查是否逆势、是否与系统方向一致
- **修复**：
  ```python
  _ok_reverse = (sig.confidence >= cfg["min_confidence"] + 0.17
                 and sig.size_mult >= 0.25
                 and not getattr(sig, "counter_trend", False)   # 逆势不许推翻
                 and (not _sys_dir or sig.side == _sys_dir))    # 须与系统方向一致
  ```

#### ② 移动止损门槛设成 1R，导致 +1% 浮盈完全不保护【设计错误】
- **现象**：空单从 +0.91%（保证金 +9%）一路回吐到 +0.08%，全程无保护
- **根因**：门槛 `pnl_r >= 1.0`，而 1R = 止损距离 = 1.56%，+1% 价格只等于 0.64R
- **修复**：满足任一即启动
  ```python
  trail_start_r = 0.5              # 0.5R（约 0.78% 价格）
  trail_start_margin = 0.08        # 或保证金收益率 8%（约 0.8% 价格）
  counter_trend_trail_r = 0.3      # 逆势单更早
  lock_r = max(0.0, pnl_r - trail_lock_r)   # 止损绝不被推到保本之外
  ```

#### ③ 逆势单只有止盈止损，没有趋势/结构退出【缺失】
- **修复**：`manage()` 新增 `ctx` 参数，逆势单 4 条退出条件
  1. 趋势恶化：L3 与日线同时不利 + 亏损 >0.3R
  2. 利润被抽：峰值浮盈 >0.3%，现浮盈 < 峰值 40%
  3. 突发事件反向：高权重新闻与持仓方向相反
  4. 关键位被抽：多头触及阻力后回落 >100 点 / 空头触及支撑后反弹 >100 点
- **注意**：`manage()` 在打分之前执行，用的是 `cache` 里**上一轮**的方向与关键位

#### ④ maker 挂单贴 1 tick，成交率极低【成本问题】
- **现象**：21:47 开仓那次 maker 未成交，回退市价付 taker 0.05%
- **修复**：改为**关键位挂单**
  ```python
  maker_at_key = True
  maker_offset_usd = 110     # 空单在关键位+110 挂，多单在关键位-110 挂
  maker_offset_step = -40    # 未成交每次朝现价收 40 点：110 -> 70 -> 30
  maker_fallback_market = True   # 最终市价兜底（用户要求「不漏单」优先）
  ```
- **代价**：价格没走到关键位就不成交，会错过这单。这是 maker 的本质权衡。

#### ⑤ 清算地图 VLM 读数错误（今晚 4 次脏数据）【数据质量】
| 现象 | 根因 | 修法 |
|---|---|---|
| 价格读成 `410,310` | 多读一位 | 窗口校验 2~5% |
| 累计值读成 `78359` | 抓了右侧 y 轴刻度 `757.94M` | **差分定位** tooltip 矩形 |
| `5.5934亿` 读成 `559.34亿` | 中文单位数量级 | `norm_liq` 见 `>5000` 自动 /100 |
| 上下窗口不对称（3.26% vs 4.48%） | 两侧独立取第一个可接受位置 | 改贪心 + 早退，目标区 2.6~3.6% |

**差分定位是核心突破**：无 hover 图 vs 有 hover 图做像素差分，逐行统计变化像素数
（tooltip 行有 200+ 个变化，十字线只有 1-2 个）→ 精确框出 tooltip，天然排除坐标轴。

#### ⑥ 权益峰值被 OKX 模拟盘毛刺污染【诊断错误 + 修复】
- **我曾误判为「V1 残留」，实际不是**
- 铁证：`14:50 价79,588 权益86,184` → `14:52 价79,590 权益87,861`（价格只动 2 点，权益跳 1,677）→ `14:53 又回 86,014`
- **真因**：OKX 模拟盘 `totalEq` 偶发单点异常，被 `max()` 当峰值
- **修复**：`perf.py` 滚动中位数去毛刺（偏离 >800U 剔除），峰值 87,861 → 86,425，回撤 -3.82% → -2.23%

#### ⑦ 新闻漏报（伊朗/普京停战信号）【流程事故】
- **根因 4 条**：① `Select-Object -First 3` 截断输出 ② 按时间倒序排，噪音占顶部
  ③ 金十 API 只返回最近 15-20 分钟，不存档无法回查 ④ **只注意升级信号，漏掉降级信号**
- **修复**：`tools_jin10.py` 按重要度排序、关键词命中才输出、去噪音、自动存档、默认 4 小时窗口
- **追加**：区分「实质进展」与「单方面表态」（用户指出表态权重不应高）
  - 实质词（签署/达成协议/停火生效/解除制裁）→ 加权 +2
  - 表态词（考虑/必须/寻求/呼吁）→ **降权 45%**

#### ⑧ 断网无人知晓【监控缺失】
- **现象**：20:35 365VPN 隧道断了，机器人在错误循环里空转直到崩溃，全程无人发现
- **修复**：`tools/health_check.ps1` 每 5 分钟检查 5 项，写 `state/ALERT.txt` + 桌面气泡
  1. 机器人进程存在且唯一
  2. 日志新鲜度 <300 秒
  3. 最近 10 行无 ≥2 条网络错误
  4. OKX 走代理可达
  5. **有持仓时交易所侧必须有止损条件单（裸奔检测）**
- 由 `BTC_Health` 计划任务驱动，同时保活 Chrome 9222

#### ⑨ 取数慢（235 秒）【自己改出来的】
- **根因**：为了「上下对称」，每侧试 8 档 × 每档 2-3 次 VLM = 约 32 次推理
- **修复**：贪心 + 早退，命中 2.6~3.6% 立刻返回，最多 4+4 档
- **实测**：235 秒 → **115 秒**（地图 189 秒 → 79 秒）

---

### 20.3 当前参数（config.json 关键项）

```
strategy_version      V3
leverage              10
margin_pct            0.33        (满仓)
account_risk_target   0.05        (单笔 5% 账户风险)
stop_pct_min          0.0155
touch_tol_usd         100.0       (关键位容差 ±100 点)
trail_start_r         0.5         ← 今晚从 1.0 改
trail_start_margin    0.08        ← 今晚新增
counter_trend_trail_r 0.3         ← 今晚新增
maker_enabled         True
maker_at_key          True        ← 今晚新增（关键位挂单）
maker_offset_usd      110         ← 今晚新增
maker_offset_step     -40         ← 今晚新增
maker_fallback_market True
min_confidence        0.45
max_positions         2
execution_mode        demo
```

---

### 20.4 数据契约（新会话必读）

| 文件 | 写入方 | 读取方 | 说明 |
|---|---|---|---|
| `data/coinglass.json` | `local_fetch.py` | 机器人 L2 | 清算地图 + 多空比；**新鲜期 40 分钟** |
| `data/x_posts.json` | `local_fetch.py` | Agent 判断 | X 专业/散户分层，带互动数 |
| `data/market_snapshot.jsonl` | `local_fetch.py` | Agent 看趋势 | 每轮一行，读最后 8 行即可 |
| `data/agent_view.json` | **Agent** | 机器人 | 判断传达到机器人的唯一通道；**90 分钟过期** |
| `data/map_escalation.json` | `local_fetch.py` | **Agent** | 三读不一致时的升级标记，含 crop 图路径 |
| `data/local_fetch_last.json` | `local_fetch.py` | Agent 排障 | 完整取数结果，含 `why` 字段 |
| `state/ALERT.txt` | `health_check.ps1` | Agent | 故障标记 |

**AgentView 写法：**
```python
from core import agent_view
agent_view.write_view(direction="short", confidence=0.5, size_mult=0.6,
                      veto_new=False, reasons=["..."], flags=["fomc_t1"], mode="live")
```
- `size_mult`：只缩不放，乘到张数上
- `veto_new`：只拦新开仓，不碰持仓管理
- **FOMC 前不要设 veto_new=true（用户明确要求）**

---

### 20.5 未解决 / 待办

| 项目 | 状态 | 说明 |
|---|---|---|
| 人工介入通道未实战验证 | 🟡 | `map_escalation.json` + `tools_write_manual_map.py` 已写好，今晚未真正触发过 |
| R 值偶发偏高 | 🟡 | 23:05 读到 up=828.97M（其他轮 500-600M），校验通过但可疑；建议加「与上一轮对比突变 >50% 则重读」 |
| CoinGlass API key | ❌ | 用户提供的 key 认证有效但**所有端点都返回 Upgrade plan**（套餐无权限） |
| 部分止盈未记入 trades 表 | ❌ | 分批止盈的已实现盈亏没 journal |
| `reconcile` 用止损价伪造成交价 | ❌ | 对账不平 |
| 回测有未来函数 | ❌ | 未修 |
| 信号层止损(0.63%)与执行层(1.56%)不一致 | ❌ | 未修 |
| X 无法用 Playwright 复用登录态 | ✅ 已解决 | 用 CDP 直连 Chrome-自动化，登录态完整继承 |

---

### 20.6 运维手册（新会话照做）

**启动检查（3 条命令）**
```powershell
cd "D:\DP workspace\btc_system"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*run.py*" }
Get-NetTCPConnection -State Listen -LocalPort 9222
if (Test-Path "state\ALERT.txt") { Get-Content "state\ALERT.txt" }
```

**重启机器人**
```powershell
$PY = "C:\Users\81559\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$old = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*run.py*" }
if ($old) { Stop-Process -Id $old.ProcessId -Force; Start-Sleep 2 }
Start-Process -FilePath $PY -ArgumentList "run.py" -WorkingDirectory "D:\DP workspace\btc_system" -WindowStyle Hidden
```

**手动取数一轮**
```powershell
$env:OLLAMA_MODELS="E:\btc-ai\models"
& $PY local_fetch.py
```

**排障优先级**
1. `state/ALERT.txt` 有没有内容
2. `data/local_fetch_last.json` 的 `why`
3. `data/map_escalation.json` 是否存在
4. 日志最后 50 行

---

### 20.7 环境事实

```
项目       D:\DP workspace\btc_system
GitHub     https://github.com/F-Sheng/BTC-Decision-Trading-System
           （本地 D:\DP workspace\BTC决策交易系统，需 git config --local http.proxy）
Python     C:\Users\81559\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
Ollama     E:\btc-ai\ollama，模型 E:\btc-ai\models（qwen2.5vl:3b）
Chrome     E:\btc-ai\chrome-profile，调试端口 9222，桌面「Chrome-自动化.lnk」
代理       127.0.0.1:57777（365VPN）—— **OKX 必须走代理，直连被屏蔽到 169.254.0.2**
金十       API 直连，不受代理影响
硬件       i5-12400F / 16GB / RTX 3050 8GB
```

**踩过的坑（避免重复）：**
- OKX 对大陆 IP 返回黑洞地址 `169.254.0.2`，不是 DNS 故障，是出口 IP 判断
- 命令行工具**不走** WinINET 系统代理，git/pip 需显式 `--proxy` 或 `http.proxy`
- CoinGlass 图表是 **canvas 渲染**，tooltip 不在 DOM 里，只能截图 + VLM
- Ollama 3B 模型**没有常识校验能力**，必须外部加合理性阈值
- PowerShell 5.1 读 `Win32_Process.CommandLine` 常为空，健康检查改用 pwsh 7
