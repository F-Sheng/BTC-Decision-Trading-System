import io
import os

p = r"D:\DP workspace\btc_system\HANDOFF.md"

sec = r"""

---

### 21.10 补充更新（2026-10-28 03:10，写在第 21 节之后，覆盖第 21.2 节中强平流那一行）

**这一节是最新的，如果和前面冲突，以本节为准。**

#### ① 强平流数据源已从 OKX 换成 LiquidVision

用户质疑「你刚不是说 LiquidVision 的强平流更齐全吗？为什么还接入欧易的强平流」后实测：

```
【OKX 单家】0.97 秒 / 100 条 / 时间跨度 159 分钟 / 最新一笔 3867 秒前（64 分钟！）
【LV BTCUSDT】0.83 秒 / 9 条 / 最新 156 秒前（2.6 分钟）
              交易所 binance 5 / htx 2 / bitget 1 / gate 1
```

**关键发现：OKX `/public/liquidation-orders` 的数据滞后 64 分钟。**

**后果：** V3 期间所有「60 分钟 0 条 = 市场平静」的判断都是错的——是数据没更新，不是没爆仓。

**改动（`tools_liq_flow.py`）：**
```python
# 旧：OKX 单家，滞后 64 分钟
# 新：LV /api/v1/liquidations?symbol=BTCUSDT&minutes=60&limit=500
# 单位换算：OKX sz=张(1张=0.01BTC) vs LV qty=币 -> 张 = qty / 0.01
# 方向映射：LV side=long(多头被爆) -> side="sell"；side=short -> side="buy"
# 阈值重标：BURST_COUNT_5M=6 / BURST_VOL_5M=200 / FRESH_SEC=30
```

**验证：** 最新强平 174 秒前（原 3867 秒），60 分钟 9 条 / 空头爆仓 90 张 / 多头 0 张。

#### ② 现在的清算数据全部来自 LiquidVision

| 数据 | 来源 | 说明 |
|---|---|---|
| **强平流** | LV `/api/v1/liquidations?symbol=BTCUSDT` | 7 家交易所，2 分钟新鲜 |
| **清算地图** | LV `/api/v1/liqmap/BTCUSDT` | 400 价位 + cum_long/cum_short |
| 清算汇总/级联 | LV `/liquidations/summary`、`/cascades` | |

**OKX 现在只负责：K线、OI、资金费率、多空比、账户与下单。**

**注意（用户问到的）：OKX 有 K 线 API（`/api/v5/market/candles`，9 个字段 ts/o/h/l/c/vol/volCcy/volCcyQuote/confirm），但 K 线里没有清算字段。OKX 的 Rubik 数据接口也没有爆仓统计（`liquidation-details` / `liquidation-summary` 都 404）。所以「清算」这部分 OKX 给不了，必须靠 LiquidVision。**

#### ③ 数据源新鲜度通用检查（新增）

V3 期间自检只检查了 R 值新鲜度，没检查其他数据源的时效性，导致「强平流滞后 64 分钟」没被发现。现在检查：

```
强平流(LV)        实时（每次 L0 现取）
清算地图           age_s < 600 秒（自检项）
X 推文            < 90 分钟
X 摘要            < 90 分钟
金十 news_log      < 90 分钟
Odaily log         < 90 分钟
```

**超过阈值标 ⚠，必须在汇报里说明。**

#### ④ 今晚一共换掉三个假数据源

| # | 假数据源 | 症状 | 换成 |
|---|---|---|---|
| 1 | CoinGlass 截图 + Qwen2.5-VL | 60-70% 成功率，多次把价格读成累计值 | LV liqmap API |
| 2 | OKX 带 `x-simulated-trading: 1` 头 | 虚高 1746 倍，出现真实市场不存在的 80,120 价位 | OKX 真实市场接口（不带头） |
| 3 | OKX `liquidation-orders` | **滞后 64 分钟**，造成「市场平静」误判 | LV 7 家交易所 |

**共同教训：数据源不仅要看「有没有返回」，还要看「量级是否合理」和「时效是否新鲜」。三者缺一不可。**

#### ⑤ 定时任务归属说明

DSH 定时任务 `deliveryMode: "host"`，**投递到当前活跃会话**。如果新会话收不到 2 分钟心跳提醒，原因是任务仍绑定在旧会话。

**解决办法（二选一）：**
- **方案 A（推荐）**：把旧会话留作「监控会话」不关，新会话做其他工作。机器人是独立进程，两边都能查状态。
- **方案 B**：在旧会话删除 3 个 schedule，在新会话里重新创建（心跳 2 分钟 / 4 小时体检 / 每日 08:00 复盘）。

**删除后要立刻在新会话重建，否则无人监控。** 机器人本身不受影响（独立进程 + 交易所侧止损单）。
"""

with io.open(p, "r", encoding="utf-8") as f:
    cur = f.read()

if "### 21.10 补充更新" in cur:
    print("已存在，跳过")
else:
    with io.open(p, "a", encoding="utf-8") as f:
        f.write(sec)
    print("已追加 21.10 补充更新")
    print("HANDOFF 现在 %.1f KB / %d 行"
          % (os.path.getsize(p) / 1024, sum(1 for _ in open(p, encoding="utf-8"))))
