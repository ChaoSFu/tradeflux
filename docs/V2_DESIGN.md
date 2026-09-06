# TradeFlux V2 设计

> V1 是**按功能模块**组织的页面集合；V2 是**按一条交易决策链**组织的观测层。
> 两者共用同一个后端、同一个数据库、同一套 daily_update 和同一个 auth store。
> **V2 只是新的展示与决策层，不是第二个系统。**

---

## 1. 产品目标

每天打开后，用最少的信息回答三件事：

1. 今天有没有交易许可
2. 主线在哪里
3. 该盯哪几只核心股

## 2. 决策链

```
市场 Market
  ↓
主线 Mainline
  ↓
核心股 Core Stock
  ↓
监管 Regulation
  ↓
SIGNAL READY
  ↓
人工执行 Human Execution
```

```
SignalReady(stock)
  = MarketPASS ∧ SectorPASS ∧ CorePASS ∧ ¬RegulatoryBlock
```

**SIGNAL READY 不是什么：**

- ≠ 自动下单 —— 系统不下单
- ≠ 预测上涨 —— 四项同时成立不代表会涨
- ≠ 盈利保证

它只表示：指数、板块、个股结构、监管四项没有互相矛盾，**可以进入人工买点确认**。

## 3. V1 / V2 边界

| | V1 | V2 |
|---|---|---|
| 路由 | `/`、`/market-trend`、`/stocks` … | `/v2/*` |
| 代码 | `src/pages`、`src/components` | `src/v2/**` |
| 后端 | 同一个 | 同一个 |
| 数据库 | 同一个 | 同一个 |
| daily_update | 同一条 | 同一条 |
| auth | 同一个 store | 同一个 store |

**V1 一行没改**，唯一的例外是给 `Header.tsx` 的 `DataUpdateMenu` 加了 `export`
关键字——纯可见性变更，行为完全不变，目的是让 V2 设置页复用同一个更新组件而不是
复制一套。

V1 的「弱转强雷达 / 破局雷达 / 涨停板块雷达 / 市场效应 / 强势股概览 / 活跃股池 /
成交额概览 / 趋势板块 / 情绪板块」**不作为 V2 一级导航**——它们是 V2 的数据来源或
子能力，不是新的一级产品概念。V1 页面全部保留可用。

## 4. 设计原则

### Facts First
优先展示可核对的市场事实：收盘、MA5/10/20/60、均线排列、斜率、涨跌幅、区间涨幅、
成交额、换手率、量比、连板数、涨停次数、排名、监管状态。

### Unknown ≠ False
`None` 不是 0，缺失不是「弱」，判不出来不是「不通过」。缺数据时显示
「—」「数据不足」「无法判断」，**绝不用 0 / False / 持平 / 空头顶替**。

Gate 的 `WAIT` 和 `BLOCK` 严格区分：

- `WAIT` = 条件没满足，**或者我们判不出来**
- `BLOCK` = 条件明确不满足，有硬证据

### 标准指标优先
`leader_score` / `risk_score` / `emotion_score` / `emotional_temperature` /
`continuity_score` / `profit_strength` / `loss_strength` 这些自造指标为兼容 V1
保留在数据里，但在 V2：不作为核心排序指标、不作为任何 gate 的条件、不占视觉中心。

### 没有黑箱综合分
决策链只输出 `PASS / WAIT / BLOCK` + 具体原因。一个 83 分说不清它是「三项满足、
一项差一点」还是「四项都刚过线」，而这两种情况的交易含义完全不同。

### 标签必须可还原
「多头」「主线」「走弱」「二波确认」都必须能对回明确字段和规则。

## 5. 各道 Gate

### Market Gate — `v2/domain/marketGate.ts`

数据来源：`GET /market-trend/indices`（后端已算好，前端不重算均线）。
核心指数：上证 000001、深成指 399001、创业板 399006、科创50 000688。

```
BLOCK   过半核心指数 state ∈ {bearish, weak}
PASS    过半 ∈ {bullish, strong} 且没有任何一个 ∈ {bearish, weak}
WAIT    其余，含"拿不到数据"
```

PASS 里那句「且没有指数明显走坏」是刻意的：主板强而创业板崩的时候，高标龙头往往
正在受伤，这种分化不该给通行证。

### Sector Gate — `v2/domain/sectorGate.ts`

**现在只能做一半。** 板块没有均线：`SectorIndexDaily` 只有当天一根，历史 K 线回填
被数据源（push2his）限流拦着。所以：

```
板块趋势 alignment = unknown
Sector Gate 最多给到 WAIT，给不出 PASS
```

**绝不用 5/10/20 日涨幅冒充均线多头**——涨幅为正只说明这段涨了，说不出结构；
一只从高位跌下来的板块 20 日涨幅仍可能是正的。

现在能给的是**领先证据**（跟「趋势」在界面上严格分开摆）：

```
rank_5d / rank_10d / rank_20d  进前 5 各算一条
rank_lu（涨停数排名）           进前 5 算一条
rank_strong（强势股数排名）     进前 5 算一条
board_height ≥ 4               算一条
```

排序是**字典序**：证据条数 → 最高连板 → 成交额。任何一行的位次都能用一句话解释，
不生成主线分。

### Core Stock — `v2/domain/coreStock.ts`

三种不同的 Leadership，分开成三个 Tab，不混成一个榜：

| | 看的是 | 数据来源 |
|---|---|---|
| 活跃龙头 | 连板龙头的结构演化 | `GET /leader-cycle`（price_v1_1 状态机） |
| 涨停核心 | 当下高度 + 可交易性 | `GET /stocks/limit-moves` |
| 趋势龙头 | 成交额容量 | `GET /turnover/overview` |

生命周期状态直接用后端 replay 的结果，**前端一条判定规则都不复制**。中文映射：

```
STREAKING 连板中   BROKEN 刚断板     REPAIRING 修复中
CROSS_SUCCESS 二波确认              CROSS_WEAKENING 确认后走弱
CROSS_FAILED 修复失败               FADED 周期结束
UNKNOWN 数据不足                    NO_CYCLE 无有效周期
```

分组：重点跟踪（连板中/修复中/二波确认）· 转折观察（刚断板）· 降级（走弱/失败/结束）
· 数据待核（数据不足/无有效周期）。

**「重点跟踪」≠ 可以买**——生命周期回答「它在哪」，回答不了「能不能买」。

涨停核心里 **Leadership 与 Tradability 分开**：一字涨停可以同时是当天最强的和
买不进去的，把这两件事合成一个「强度」等于让人对着成交不了的信号做决策。

### Regulatory Gate

个股：直接复用 `GET /watchlist/regulatory`（监管中 / 即将解除 / 最近解除 /
逼近严重异动阈值）。

市场级监管态度：**暂无结构化数据，明确留空**。不能用个股监管数量推断管理层态度
——监管个股多可能是行情热，也可能是交易所口径变了，这两件事对下一步的含义相反。

## 6. 数据缺口（不伪装成已完成）

| 缺什么 | 影响 | 为什么现在没有 |
|---|---|---|
| 板块均线 | 主线的趋势 gate 给不出 PASS | 板块指数历史被 push2his 限流拦着 |
| 个股趋势（成交额榜） | 趋势龙头的「趋势」列是「数据不足」 | 成交额接口不带均线字段 |
| 市场级监管态度 | 监管页只有个股部分 | 需要政策/表态类结构化数据源 |
| 历史时点成分股 | 生命周期研究结论只在「今天的幸存者」宇宙内成立 | 快照按今天的池子回填 |

## 7. 生命周期研究现状（不进入 V2 硬规则）

`price_v1_1` 已有前瞻评估（同日同池超额、可执行超额、按周期整段重抽的 95% 区间），
但**多数正向事件的区间仍跨 0**。所以 V2 里：

- 不显示「BROKEN→REPAIRING 胜率 58% · 强烈买入」这类结论
- 不基于现有小样本动态调生命周期规则

生命周期当前承担的是 **attention management / structure description /
risk exclusion**，不是已验证的自动选股 edge。

## 8. 尚未实现

- Phase 3 板块均线（`GET /sectors/trend-analysis`）
- Phase 4 个股趋势补充（成交额榜的 MA）
- Phase 5 `tradeSignal` 在所有页面统一接线（domain 已就位，Trading Console 已用，
  Core Stocks 尚未逐股显示四道 gate）
- 市场级监管环境模型
