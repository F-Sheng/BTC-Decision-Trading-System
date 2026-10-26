# BTC 决策交易系统

> **AI Agent 统筹 + 代码执行**的比特币合约自动交易系统
> 五层决策 · 双向持仓 · 结构止盈 · 风险预算 · 全程可审计

**当前版本：V2**

```
┌─────────────────────────────────────────────────────────┐
│  L1 宏观    CPI/通胀动能/能源/油价/纳指VIX/美元/2Y/溢价/地缘  │
│                        ↓ 定大方向                        │
│  L2 资金    清算地图(Agent取) + 真实强平流(代码取) + 持仓结构  │
│                        ↓ 定短期路径                      │
│  L3 技术    日线/4h/1h/15m 均线·MACD·RSI·BOLL·ATR·分形    │
│                        ↓ 定进场时机                      │
│  L4 事件    FOMC/CPI/期权到期 · 新闻情绪 · 突发检测        │
│                        ↓ 定节奏                          │
│  L5 风控    止损位置/强平缓冲/保证金上限/风险预算/日亏熔断  │
│                        ↓ 一票否决                        │
└─────────────────────────────────────────────────────────┘
```

> ⚠️ **仅供研究学习，不构成投资建议。** 默认连接 OKX **模拟盘**，不动真钱。

---

## 为什么是「Agent + 代码」两层

| 必须 Agent 做（浏览器 + 语义判断） | 必须代码做（确定性计算） |
|---|---|
| 打开 CoinGlass 读清算地图 | 算仓位、止损、止盈 |
| 读推特专业号与散户情绪 | 下单、挂条件单、对账 |
| 读金十快讯，判断"美联储鹰派暂停意味着什么" | 每分钟扫信号、记账 |

**早期踩过的坑**：试图让代码直接抓 CoinGlass。它的 API 返回加密数据（`{"code":"0","success":true}` 但 `data` 被抹掉），于是只能**编一个估算模型冒充真数据**——输出的数字和真数据长得一模一样，但全是假设。后来改成「Agent 取数、代码计算」，问题才消失。

---

## 核心特性

### 双向持仓（V2 新增）
OKX `long_short_mode`，**多空两条腿可以同时存在**，各自止损、各自止盈。

```
空单在 80,691 等止盈 79,422
价格反弹 → 另开多单 80,690，止盈 82,048
两条腿互不干扰，各赚各的
```

### 支撑阻力用标准算法
| 方法 | 说明 |
|---|---|
| **枢轴点** | PP=(H+L+C)/3，R1=2PP−L，S1=2PP−H …（TradingView 默认口径） |
| **成交量分布** | 价格分桶累计成交量，POC / VAH / VAL（70% 区间） |
| **分形摆动** | Bill Williams 3/3 分形，多周期确认 |

**布林带不当压力位**（它是波动率指标）；整数关口只留最近的。

### 止盈从结构算，不是固定 R 倍数
候选池：前低/前高 → 枢轴点 → 成交量 VAL/POC → 真实爆仓热区 → 斐波那契扩展。
同一价格区域**方法越多越优先（共振）**，限制在 0.5R~4R。

### 仓位由风险预算反推
```
保证金% = 目标风险 ÷ (杠杆 × 止损%)
```
目标风险 5%、杠杆 10x、止损 2% → 保证金 25%，实际风险正好 5%。

### 移动止损两级
- **档位跳变**：TP1 后移保本，TP2 后移到 TP1
- **连续跟随**：盈利超 1R 后，每多赚 0.5R 就把止损推进到「峰值 − 0.5R」

> 依赖 `Position.risk0`（开仓时原始风险距离）——止损移到保本后 `|止损−开仓| = 0`，R 值分母会丢。

### 推特情绪分层
- **专业号**（白名单）→ 小权重正向
- **散户号** → **反向指标**：集体看多视为利空

因为推特喊单的激励结构是"看多比看空更容易涨粉"。

---

## 快速开始

```bash
git clone <repo>
cd BTC决策交易系统
pip install -r requirements.txt

cp config.example.json config.json
cp secrets.example.json secrets.json   # 填 OKX 模拟盘 Key、DeepSeek、FRED
```

**先用模拟盘跑通**：
```bash
python self_test.py    # 自检
python diag.py         # 看各层评分
python run.py          # 主循环
```

**Windows 常驻（不弹窗）**：
```powershell
schtasks /create /tn "BTC_Watchdog" `
  /tr "wscript.exe //B `"$PWD\tools\run_if_missing.vbs`"" `
  /sc minute /mo 5 /f
```

---

## 配合 Agent 使用

本仓库包含一个 **Skill**：`skill/btc-trading-agent/`

把它复制到你的 Agent skill 目录，Agent 就知道：
- 每小时该用浏览器抓哪些数据、怎么读清算地图（含三条防错铁律）
- 每天该复盘哪些指标
- **五条绝对不能做的事**

详见 [skill/btc-trading-agent/SKILL.md](skill/btc-trading-agent/SKILL.md)

---

## 目录结构

```
BTC决策交易系统/
├── run.py                 主循环（60 秒一轮，15 分钟 K 线收盘出信号）
├── perf.py                运行成绩单
├── review.py              每日复盘
├── render_card.py         决策台出图（PNG）
├── diag.py                分层评分诊断
├── backtest.py            历史回测
├── flatten.py             一键平仓
├── core/                  18 个模块
│   ├── okx.py             OKX v5 REST（HMAC 签名）
│   ├── market.py          K 线 + 指标
│   ├── macro.py           L1 宏观九项
│   ├── levels.py          技术位（枢轴/成交量分布/分形）
│   ├── liq_live.py        真实强平流（OKX 公开接口）
│   ├── coinglass_file.py  读 Agent 写入的 CoinGlass 快照
│   ├── positioning.py     散户/大户持仓、费率、OI
│   ├── sentiment.py       情绪合成
│   ├── x_sentiment.py     推特分层打分
│   ├── scoring.py         五层汇总
│   ├── strategy.py        四个入场模型
│   ├── targets.py         结构止盈
│   ├── risk.py            风控 + 仓位
│   ├── executor.py        下单/止损/对账/移动止损
│   └── journal.py         状态 + SQLite 账本
├── tools/                 运维脚本
├── skill/                 ⭐ Agent 技能包
├── docs/                  架构文档
├── data/                  Agent 写入的数据快照
└── config.example.json
```

---

## 常用命令

| 命令 | 作用 |
|---|---|
| `python run.py` | 主循环 |
| `python perf.py` | 运行成绩单（周期/盈亏/胜率/平均R） |
| `python review.py` | 每日复盘（信号漏斗/数据健康/参数体检） |
| `python render_card.py` | 出决策台图片 |
| `python diag.py` | 各层评分诊断 |
| `python backtest.py` | 历史回测 |
| `python flatten.py` | 平掉所有持仓 |
| `python tools/tools_go_hedge.py` | 清仓并切换双向持仓 |

---

## 诚实的局限

1. **回测没有证明有 edge**。47 天数据、四个入场模型均 R ≈ 0，扣手续费后为负。这套系统是"按规则不犯错"，不是"保证赚钱"。
2. **大部分阈值是经验值**。宏观分档、置信度加减、情绪合成权重都是先给合理初值再调，未经统计标定。
3. **CoinGlass 依赖 Agent 刷新**。Agent 不刷新就退化到真实强平流或中性，**不会用编造的模型冒充数据**。
4. **单笔 5% 风险 = 连亏 3 笔 −15%**。
5. **不处理资金费率成本**，持仓过夜自行计算。

---

## License

MIT
