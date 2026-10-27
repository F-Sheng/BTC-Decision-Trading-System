import io
import os

p = r"D:\DP workspace\btc_system\HANDOFF.md"

sec = r"""

---

## 21. V4 大版本交接（2026-10-28 02:53 起，新会话从这里读）

### 21.0 一分钟现状

```
版本         V4（V3 于 02:53 结束，已归档到 config.v3_archive）
V4 起点权益  82,943.68 USDT
V4 起始时间  2026-10-28 02:53:43
持仓        空仓（多空都已止损平掉）
机器人      run.py 正常运行，cycle 从 1 重新计数
调度        每 2 分钟心跳 + L0/L1/L2 三级分层
数据源      除 X 外全部 API；X 用 CDP 读「正在关注」时间线
```

**V3 战绩（已归档）：** 起始 84,478.20 → 结束 82,943.68 = **−1,534.52 U（−1.82%）**
V3 期共 4 笔平仓：−283.78 / +204.31 / +0.00 / −1,208.14

---

### 21.1 V4 相对 V3 的 12 项重大变化

#### 数据层（5 项）

| # | 变化 | 旧 | 新 |
|---|---|---|---|
| 1 | **清算地图** | CoinGlass 截图 + Qwen2.5-VL 读数（60-70% 成功率，多次幻觉） | **LiquidVision `/api/v1/liqmap`**：400 价格档位 + cum_long/cum_short（美元），age 0-40 秒，JSON |
| 2 | **强平流** | OKX 接口**带 simulated 头 → 返回 demo 垃圾数据**（虚高 1746 倍，出现 80,120 这种不存在价位） | 直连真实市场（不带 simulated 头），量级 500 张/百条 |
| 3 | **新闻宏观** | 自己写关键词权重 | 金十官方 `important` 字段（爆标记）+ 实质/表态分级 |
| 4 | **新闻币圈** | 无 | **Odaily REST API** `/api/v1/newsflash?isImportant=true`（38 万条库，官方重要标记） |
| 5 | **X 推特** | 硬编码 5 个账号，5-8 条，chrome-control 缓存滞后 12-19 小时 | **「正在关注」时间线**（滚轮+PageDown 滚动，5→13 条，2-5 分钟新）+ 25 账号清单备用 + 本地 qwen2.5:3b 摘要情绪打分 |

#### 调度层（2 项）

| # | 变化 | 说明 |
|---|---|---|
| 6 | **2 分钟心跳 + 三级分层** | L0(2分钟,3秒)=强平流+金十 / L1(5分钟,8秒)=liqmap+Odaily+判定 / L2(60分钟,2分钟)=X+宏观。L0 命中强平潮或新★★ 会**提前触发 L1** |
| 7 | **新闻触发去重** | 同一★★ 不重复触发 L1（用 `heartbeat_state.json` 的 seen_news，保留 60 条） |

#### 交易层（5 项）

| # | 变化 | 旧问题 | 新逻辑 |
|---|---|---|---|
| 8 | **移动止损** | `lock_r = pnl_r - 0.5`，0.5R 时锁 0R = 保本零缓冲，被 0.15% 波动扫掉，错失后续 0.75% 行情 | `buffer = max(0.4R, pnl_r*40%)`，`lock_r = pnl_r - buffer`（0.5R→锁0.1R，1R→锁0.6R） |
| 9 | **逆势单退出** | 只有止盈止损 | 新增 4 条：趋势恶化 / 利润被抽 / 突发事件反向 / 关键位被抽 |
| 10 | **reverse_signal** | 只看信号自身置信度，导致「逆势」信号能推翻顺向仓位（损失 1,940 U 浮盈） | 增加 `not sig.counter_trend` 且 `sig.side == sc.direction` |
| 11 | **止盈位置** | 多单 TP 80,822.6 ≈ 阻力位 80,857，价格没突破就回头打不到 | 多单 TP ≤ 最近压力 − `tp_safety_usd`(100)；空单 TP ≥ 最近支撑 + 100 |
| 12 | **加仓（新增）** | 无 | 双向：多单测试支撑确认→加多；空单测试阻力拒绝→加空。最多 2 次/每次 40%/隔 0.5%/事件黑窗 1h |

#### 另外修复的 3 个 bug

| bug | 后果 | 修复 |
|---|---|---|
| `coinglass_file.summary()` 里 `%.1f` 格式化 `None` | 清掉脏数据后 r_ratio=None，**机器人每轮 cycle 崩溃** | 全部 None 安全处理，缺失显示「不可用」 |
| `FOMC_2026` 表填的是美东日期 | FOMC 决议是美东 14:00 = **北京次日 02:00**，日期错一天导致 10-28 全天被判「事件静默期」，**禁止开仓 25 小时** | 全部 +1 天改北京日期；blackout 从「整天」改「公布前后 30 分钟」；再从 veto 降级为缩仓提示（用户明确要求不要用事件禁止开仓） |
| 状态被内存旧值覆盖 | 机器人在跑时改 `state/*.json`，下一轮被内存值冲掉（改 TP、改 start_equity 都中招过） | **改 state 必须先停机器人**。已踩坑 3 次 |

---

### 21.2 数据源全景（新会话必读）

| 数据 | 来源 | 认证 | 频率 | 可靠性 |
|---|---|---|---|---|
| **清算地图（±3% 累计 + R）** | `https://liquidvision.app/api/v1/liqmap/BTCUSDT?exchange=all&range=1d&model=v3` | `Authorization: Bearer Iv_wxwcAjfgNLFNp_RD_FfDRkyENKb9Zgy8hynrDWr3Zy4` | 5 分钟 | ✅ API，age 0-40s |
| 清算汇总（1h/4h/12h/24h） | `/api/v1/liquidations/summary` | 同上 | 5 分钟 | ✅ |
| 清算级联 | `/api/v1/liquidations/cascades` | 同上 | 按需 | ✅ |
| 持仓状态（new_longs 等） | `/api/v1/oi/regime` | 同上 | 60 分钟 | ✅ |
| 资金流 | `/api/v1/flows` | 同上 | 按需 | ✅ |
| **订单簿热力图** | `/api/v1/heatmap/{exchange}/{symbol}` | **需 x402 微支付 0.005 USDC/次** | — | 未接入 |
| 异动 | `/api/v1/sparks` | **需 x402** | — | 未接入 |
| **K线/OI/资金费率/多空比** | OKX API | API key（config 里） | 机器人 60 秒 | ✅ |
| **强平流** | `https://www.okx.com/api/v5/public/liquidation-orders?instType=SWAP&uly=BTC-USDT&state=filled&limit=100` | **无需认证，但不能带 `x-simulated-trading` 头** | L0 每 2 分钟 | ✅ |
| 新闻（宏观） | 金十 `flash-api.jin10.com/get_flash_list` | 无需 | L0 每 2 分钟 | ✅ 含 `important` 爆标记 |
| 新闻（币圈） | `https://api.odaily.news/api/v1/newsflash?isImportant=true` | 无需 | 5 分钟 | ✅ 38 万条库 |
| **X 推特** | CDP 读 `x.com/home` 的「正在关注」tab | 浏览器登录态 | 60 分钟 | ⚠️ 唯一非 API；滚动需真实滚轮+PageDown |
| 宏观（市值/恐惧贪婪/涨跌榜） | `D:/DP workspace/odaily_skill` 的 `run.py` | 无需 | 60 分钟 | ✅ |

**LiquidVision 关键参数：**
- 认证只能用 `Authorization: Bearer <key>`，`X-API-Key` 会返回 invalid
- `liqmap` 返回 `bins[400]` + `cum_long[400]` + `cum_short[400]`（美元）+ `current_price` + `age_s`
- **liqmap 的绝对值与 CoinGlass 差 4 倍**（模型不同），只在同一源内做趋势对比
- 但两家的 24h 清算总量高度一致（$137M vs $123M，差 10%）

---

### 21.3 调度架构（2 分钟心跳）

```
每 2 分钟  python tools_heartbeat.py
  │
  ├─ L0（每次，约 3 秒）
  │    · tools_liq_flow.py  （强平流，真实市场）
  │    · tools_jin10.py     （金十新闻）
  │    输出 burst / news_high / top_news / fresh_news
  │
  ├─ L1（每 5 分钟，或被 L0 的 burst/news_high 提前触发，约 8 秒）
  │    · tools_lv_map.py    （LV liqmap → 写 data/coinglass.json）
  │    · tools_odaily.py    （币圈新闻）
  │    · tools_should_fetch.py（判定引擎 9 条规则）
  │
  └─ L2（每 60 分钟，约 2 分钟）
       · tools_x_following.py（「正在关注」时间线）
       · tools_x_digest.py   （本地 qwen2.5:3b 摘要 + 情绪分）
       · tools_macro_odaily.py（Odaily Skill 宏观）
```

**节流状态存 `data/heartbeat_state.json`**：`last_L1` / `last_L2` / `seen_news[]`

**汇报规则**（用户明确要求）：
- 只有 L0 跑且无异常 → **不汇报**
- L1 无新事件 → 不汇报
- L1 有 ★★/强平潮/价格突变 → 2-3 行纯文字
- L2 跑了 → 2-3 行（含 X 情绪合计 + 最重要 2 条）
- 开平仓/止损/自检 FAIL/数据源故障 → 必须展开

**「静默」的正确含义（用户多次强调）**：
```
静默 = 没有大事时，不向用户发消息
≠ 机器人停止交易
≠ Agent 平掉仓位
```
**机器人照常运行、照常开仓。Agent 没有下单权限，只能写 agent_view.json 调仓位系数。**

---

### 21.4 数据自检（12 项，用户强制要求）

```powershell
python tools_lv_map.py --check
```

| # | 检查项 | 阈值 | 为什么要有 |
|---|---|---|---|
| 1 | OKX 现价 | 60k~200k | 防接口串数据 |
| 2 | **OKX 强平流量级** | 单笔最大 <500 张、合计 <5000 张 | **防重演模拟盘接口事故（曾虚高 1746 倍）** |
| 3 | LV liqmap 可用 | ok=True | |
| 4 | LV liqmap 新鲜度 | age <600 秒 | |
| 5 | LV 窗口命中 | 上/下窗口都在 2.7%~3.3% | 旧版「对称」检查已过时（那是截图时代的） |
| 6 | LV R 合理 | 0.2~8.0 | |
| 7 | **coinglass R 新鲜度** | R 非 None 且 age<900 秒 | **防我引用 2 小时前的旧 R（实际犯过）** |
| 8 | LV 清算汇总 | 0 < 24h <5e9 | |
| 9 | 金十 news_log | 有内容 | |
| 10 | Odaily log | 有内容 | |
| 11 | 机器人日志 | <300 秒 | |
| 12 | 止损单在位 | 有持仓时有条件单 | 防裸奔 |

**任何 FAIL 必须在汇报里明说，并停止用该数据源判断。**

---

### 21.5 当前参数（config.json 关键项）

```
strategy_version      V4
leverage              10
margin_pct            0.33
account_risk_target   0.05
stop_pct_min          0.0155
touch_tol_usd         100.0
tp_safety_usd         100.0     ← V4 新增：止盈必须留在关键位内侧 100 点
trail_start_r         0.5
trail_start_margin    0.08
trail_buffer_min_r    0.4       ← V4 新增：移动止损缓冲下限
trail_buffer_ratio    0.4       ← V4 新增：缓冲 = 盈利的 40%
counter_trend_trail_r 0.3
maker_enabled         True
maker_at_key          True
maker_offset_usd      110
maker_offset_step     -40
maker_fallback_market True
add_enabled           True      ← V4 新增
add_max_times         2
add_size_mult         0.4
add_min_gap_pct       0.5
add_reject_pct        0.35
add_risk_cap          1.6
add_blackout_h        1.0
min_confidence        0.45
max_positions         2
execution_mode        demo
run_start_equity      82943.68  ← V4 起点
run_started_at        1793128423000  ← 2026-10-28 02:53:43
```

---

### 21.6 V3 期间的核心教训（务必读）

**教训 1：假数据比没数据更危险**
`OkxClient(simulated=True)` 带 `x-simulated-trading: 1` 头调 `/public/liquidation-orders`，
返回的 demo 数据虚高 1746 倍（单笔 10,000 张 vs 真实 91.9 张），还出现 80,120 这个
真实市场不存在的价位。**用户一句「从来没有 80,120 这个数据」才让我查出根因。**
→ 所有外部数据必须有**量级合理性检查**，不能只看格式对不对。

**教训 2：L1 宏观是慢变量，不该主导短期方向**
V3 期间 L1 一直是 −5（通胀/美元/能源压制），我据此坚持空头，但市场被
①地缘降级 ②WTI 跌破 100 ③空头挤压 推着上涨，空单止损 −1,208 U。
→ L1 定「仓位上限与持有周期」，L2 资金流 + L4 事件定「短期方向」。

**教训 3：止损缓冲不能为零**
移动止损推到保本价（零缓冲）会被 0.15% 的日常波动扫掉。
多单 01:23 被扫，之后价格涨到 81,254，错失约 1,100 U。
→ 缓冲必须留 `max(0.4R, pnl_r*40%)`。

**教训 4：改 state 必须先停机器人**
踩坑 3 次（改止盈、改 start_equity 各一次，另一次被覆盖）：机器人在跑时改
`state/*.json`，下一轮它用内存旧值覆盖回去。
→ **停进程 → 改文件 → 重启 → 验证**。

**教训 5：日期要区分时区**
FOMC 决议美东 14:00 = 北京次日 02:00。表里填美东日期导致「事件静默期」判定
错位一天，机器人被禁止开仓 25 小时。

**教训 6：`X or 默认值` 会吞掉 0**
`(st.get("age_s") or 999)` 在 age_s=0（最新鲜）时返回 999，把最新数据判成过期。
→ 用 `x if x is not None else default`。

---

### 21.7 未解决 / 待办

| 项目 | 状态 | 说明 |
|---|---|---|
| X 时间线滚动条数 | 🟡 | 滚轮+PageDown 有效（5→13 条），但 CDP 偶尔超时；X 虚拟列表难拿到 30+ 条 |
| x.com/home 偶发 "Something went wrong" | 🟡 | 已加 3 次重试，不确定是否 100% 能过 |
| 分批止盈未 journal | ❌ | 部分止盈的已实现盈亏没记进 trades 表 |
| reconcile 用止损价伪造成交价 | ❌ | 对账时不平 |
| 回测有未来函数 | ❌ | 未修 |
| 信号层止损 0.63% vs 执行层 1.56% | ❌ | 两者不一致 |
| CoinGlass 与 LV liqmap 绝对值差 4 倍 | ⚠️ | 模型不同，只在单源内比较 |
| x402 微支付（热力图/异动） | ❌ | 0.005 USDC/次，需配置 crypto 钱包 |
| FOMC 2026 日期表 | ✅ 已修正 | 全部 +1 天改北京日期 |

---

### 21.8 运维手册

**启动检查**
```powershell
cd "D:\DP workspace\btc_system"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*run.py*" }
Get-NetTCPConnection -State Listen -LocalPort 9222
if (Test-Path "state\ALERT.txt") { Get-Content "state\ALERT.txt" }
```

**改 state 的正确顺序（重要）**
```powershell
# 1. 停
$old = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*run.py*" }
if ($old) { Stop-Process -Id $old.ProcessId -Force; Start-Sleep 3 }
# 2. 改（跑脚本）
# 3. 重启
Start-Process -FilePath $PY -ArgumentList "run.py" -WorkingDirectory "D:\DP workspace\btc_system" -WindowStyle Hidden
# 4. 验证 state 文件确实是新值
```

**常用脚本**
| 脚本 | 用途 |
|---|---|
| `tools_heartbeat.py` | 心跳调度（唯一入口） |
| `tools_heartbeat.py --status` | 看 L1/L2 上次时间 |
| `tools_lv_map.py` | 取清算地图写 coinglass.json |
| `tools_lv_map.py --check` | 12 项数据自检 |
| `tools_liq_flow.py` | OKX 真实强平流 |
| `tools_jin10.py` | 金十新闻（默认 4 小时窗口） |
| `tools_odaily.py` | Odaily REST 新闻 |
| `tools_x_following.py` | X「正在关注」时间线 |
| `tools_x_digest.py` | 本地模型摘要 X 推文 |
| `tools_macro_odaily.py` | Odaily Skill 宏观 |
| `tools_should_fetch.py` | 9 条规则判定引擎 |
| `tools_pos_now.py` | 查持仓 |
| `perf.py` | 收益统计（含权益去毛刺） |
| `render_card.py --archive` | 出图（只在 4 小时/每日任务用） |

**排障优先级**
1. `state/ALERT.txt`
2. `data/heartbeat_state.json`（L1/L2 时间）
3. `tools_lv_map.py --check` 输出
4. `data/local_fetch_last.json` 的 `why`
5. 日志最后 50 行

---

### 21.9 环境事实

```
项目       D:\DP workspace\btc_system
OdailySkill D:\DP workspace\odaily_skill（克隆自 github.com/ODAILY/odaily_plugin-）
GitHub     https://github.com/F-Sheng/BTC-Decision-Trading-System
本地仓库    D:\DP workspace\BTC决策交易系统
           （需 git config --local http.proxy http://127.0.0.1:57777）
Python     C:\Users\81559\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
Ollama     E:\btc-ai\ollama\ollama.exe
模型       E:\btc-ai\models
           · qwen2.5vl:3b  3.2GB（视觉，已降级为备用）
           · qwen2.5:3b    1.9GB（文本，X 摘要用）
Chrome     E:\btc-ai\chrome-profile，调试端口 9222，桌面「Chrome-自动化.lnk」
代理       127.0.0.1:57777（365VPN）
           **OKX 必须走代理，直连被屏蔽到 169.254.0.2**
金十/Odaily/LiquidVision  可直连
硬件       i5-12400F / 16GB / RTX 3050 8GB
```

**踩坑清单**
- OKX 对大陆 IP 返回黑洞地址 `169.254.0.2`，不是 DNS 故障
- 命令行工具**不走** WinINET 系统代理，git/pip 需显式 `--proxy`
- **OKX `/public/liquidation-orders` 绝不能带 `x-simulated-trading: 1`**（返回 demo 垃圾数据）
- LiquidVision 认证只能用 `Authorization: Bearer`，`X-API-Key` 无效
- CoinGlass 图表是 **canvas**，tooltip 不在 DOM，只能截图；**现已改用 LV API，不再需要**
- Ollama 3B 模型**没有常识校验能力**，必须外部加量级阈值
- PowerShell 5.1 读 `Win32_Process.CommandLine` 常为空，健康检查用 pwsh 7
- X 的虚拟列表**不响应** `window.scrollBy` / 容器 `scrollTop`，必须用 `Input.dispatchMouseEvent` mouseWheel + `Input.dispatchKeyEvent` PageDown
- `(x or default)` 会吞掉 0 和空字符串，检查数值时用 `x is not None`
"""

with io.open(p, "r", encoding="utf-8") as f:
    cur = f.read()

if "## 21. V4 大版本交接" in cur:
    print("已存在，跳过")
else:
    with io.open(p, "a", encoding="utf-8") as f:
        f.write(sec)
    print("已追加第 21 节（V4 交接）")
    print("HANDOFF 现在 %.1f KB / %d 行"
          % (os.path.getsize(p) / 1024, sum(1 for _ in open(p, encoding="utf-8"))))
