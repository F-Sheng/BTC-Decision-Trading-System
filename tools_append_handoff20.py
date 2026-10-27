import io
import os

p = r"D:\DP workspace\btc_system\HANDOFF.md"

sec = r"""
---

## 20. 2026-10-27 夜间大改 · 最终交接（新会话从这里读）

### 20.0 一句话现状
系统已全自动：Agent 只做统筹与判断，`local_fetch.py` 用 CDP + 本地 VLM 取数（图片不进 Agent 上下文），
机器人独立跑策略。当前双持仓（多 85.8 张 / 空 86.6 张），明日 FOMC。

---

### 20.1 架构总览（务必先理解这个）

```
┌─ Chrome-自动化 (独立 profile, 调试端口 9222) ─┐
│  E:\\btc-ai\\chrome-profile                    │
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
"""
# 修正转义
sec = sec.replace("\\\\", "\\")

with io.open(p, "r", encoding="utf-8") as f:
    cur = f.read()

if "## 20. 2026-10-27 夜间大改" in cur:
    print("已存在，跳过")
else:
    with io.open(p, "a", encoding="utf-8") as f:
        f.write(sec)
    print("已追加第 20 节")
    print("HANDOFF 大小: %.1f KB, %d 行" % (os.path.getsize(p) / 1024,
                                            sum(1 for _ in open(p, encoding="utf-8"))))
