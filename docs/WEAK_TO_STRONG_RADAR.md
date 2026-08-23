# 弱转强雷达（Weak-to-Strong Radar）

> 文档状态：Phase 1 + Phase 2 已全部完成；2026-08-22 完成一轮状态机纠错（外部
> 代码评审发现并修复了多处真实bug：结构确认判定过弱、BLOCK 死态、跨日状态
> 未重置等，见第 11 节）；2026-08-23 完成第二轮重构——把"结构事实"和"交易
> 决策"彻底拆成两层（原来结构层错误地借用了 BUYABLE/WAIT 这些本该属于展示
> 层的名字），并补上数据合理性校验/市场板块负反馈字段/H1-L1原始快照，见
> 第 11.1 节。Phase 3 待办跟踪中。
> 路由：`/weak-to-strong-radar`　·　API 前缀：`/api/weak-to-strong-radar`
> 最后更新：2026-08-23

---

## 1. 核心原则

**「弱转强成立」≠「值得买入」。**

`BUYABLE` 是**交易决策层**的产出，由三层判断链共同决定，不是任何单一分数加权出来的：

1. **Hard Blocker（硬性拦截，命中即 BLOCK，与结构进度无关）**：数据过期、大盘闸门
   RED、板块分类不在允许列表、非板块核心龙头（`non_leader`）、监管风险 HIGH/EXTREME、
   候选观察期已过。
2. **Soft Cap（软上限，不拦截但压低展示态上限）**：板块仍处 `NEW_START` 早期 → 最高
   只展示到 `READY`；龙头未决（`undetermined`）→ 最高只展示到 `CONFIRMING`；涨停空间
   不足 `w2s_space_min_room_pct` → 降级为 `WAIT`。
3. **Setup Progression（结构事实层，只看价格行为，不受 1/2 影响）**：`WATCH → READY
   → REPAIRING → PULLBACK → CONFIRMED`（H1/L1 两段式回踩确认，见第4节状态机）。

只有结构事实层走到 `CONFIRMED`，且同时没有被 1/2 任何一层压低或拦截，展示态才会是
`BUYABLE`——这三层各自独立重算，Hard Blocker 短暂命中不会清空结构事实层已经走到的
进度，闸门一恢复展示立刻反映真实进度（详见第4节、第11.1节）。Chips（日内获利盘/
筹码）这一组需要分钟级数据，本仓库目前没有该数据源，Checklist UI 上诚实显示灰色
**"Phase 2"** 标签，绝不伪造 ✓ / ✗。Stress R/R（压力情景风险回报比）已实现，但明确
是一种保守估算，不是完整期望收益模型，也不构成任何止损/止盈建议（精确定义见第4节）。

**关于验证记录的重要区分**：本文档和页面上出现的"N条单测全绿"只证明代码按照
设计的逻辑正确运行（Engineering Validation），**完全不代表这套弱转强定义本身
能不能在实盘上赚钱**（Strategy Validation）。后者需要历史回测（Phase 3 待办第4项），
在那之前不应该把"代码没bug"误读成"策略有效"。

与已有的 `/signals`（弱转强信号页）关系：`/signals` 是"发现弱转强股票"的轻量列表，本雷达
是完整走一遍"盘前候选 → 板块闸门 → 龙头闸门 → 竞价判断 → 盘中确认 → 状态机"决策链路的
独立页面，两者数据管道完全分离，互不依赖，互不改动。

---

## 2. 架构总览

```
盘前（daily_update.py 收盘后 15:30 / 09:27 两次调度）
  ├─ 第5步 刷新板块统计 → 板块每日快照 upsert_sector_daily_snapshot()
  └─ 候选池发现 w2s_candidate_service.discover_candidates()
       ├─ Prompt1/Prompt2 → eastmoney_fetcher.fetch_strong_pool_codes()
       ├─ 本地二次校验排名/均线类条件（不信任东财 Prompt 解析结果；成交额条件
       │   信任东财自身数值过滤，不在本地复核范围，见第8节）
       └─ upsert weak_to_strong_candidates（命中续期 / 连续miss超窗口失活）

盘中快速刷新（独立 API，09:26 定时 + 手动按钮，目标 <5-10秒）
  w2s_refresh_service.run_refresh()
    ├─ 跨日重置：signal_trade_date 不是今天 → 清空日内结构字段（保留生命周期字段）
    ├─ w2s_market_gate_service.get_market_gate()  → Market Trend/Risk Appetite/四色（全局算一次）
    ├─ 按 Theme(=Stock.primary_sector_id) 分组
    ├─ w2s_sector_gate_service.score_sector()   → Sector Strength/Momentum/7分类
    ├─ w2s_leader_gate_service.score_leaders_for_theme() → Core Leader Score/排名
    ├─ eastmoney_fetcher.fetch_stock_quotes_batch() → 批量实时报价（算真实VWAP/真实开盘Gap，
    │   均有合理性校验：VWAP须落在[当日最低价,最高价]区间、Gap不能超过当日涨跌停幅度，
    │   校验不通过时置 None 退回近似值，不让异常数据参与结构判断，见第11.1节）
    ├─ w2s_risk_service.compute_stops/compute_stress_rr() → 三层止损 + 压力情景R/R
    ├─ w2s_state_machine.compute_next_state()   → 结构事实层推进（不受闸门/软上限影响）
    │   + Hard Blocker/Soft Cap 两层覆盖推导出展示态
    └─ 展示态真变化 → 写一条 weak_to_strong_events（追加写，不覆盖，含H1/L1原始快照）
```

后端服务文件均以 `w2s_` 前缀命名（`backend/app/services/w2s_*.py`），核心打分/状态机函数
是**纯函数**（不开 DB session，只吃标量输入），DB 相关的查询/写入逻辑单独放在薄封装里，
方便单测（`backend/tests/test_w2s_*.py`，92条全绿）。Market Gate 不新起数据管道——三部分
数据都基于已有的、daily_update 每天同步的数据：指数趋势复用
`index_trend_service.get_market_trend()`（读库），涨跌家数/涨跌停比复用 `MarketBreadthDaily`
（大盘趋势页同一份数据源），T-1冻结群体次日反馈复用 `market_effect_service`（赚钱/亏钱
效应引擎）。

---

## 3. 数据库设计

### `weak_to_strong_candidates`（当前态，一股一行）

| 字段 | 说明 |
|---|---|
| `stock_id/stock_code/stock_name` | 股票标识 |
| `first_seen_date/last_seen_date/consecutive_miss_days/candidate_source/is_active` | 候选生命周期（跨日保留） |
| `setup_type` | 弱转强分型占位字段，恒为 `GENERIC`（架构预留，见第10节） |
| `sector_id/sector_name/sector_category/sector_strength_score/sector_momentum_score/sector_divergence_health` | Sector Gate 结果（`sector_momentum_score` 无历史基准时为 `None`，不是假的50分；`sector_divergence_health` 2026-08-23新增，仅 phase=4 分歧阶段有值，是 `sector_category` 二分类判断背后的原始健康度分数，此前算完即丢弃，现在独立暴露） |
| `leader_type/leader_rank/leader_score` | Leader Gate 结果 |
| `current_state` | **交易决策层**（展示值）：`WATCH\|READY\|REPAIRING\|CONFIRMING\|BUYABLE\|WAIT\|BLOCK`。由 `structural_state` 叠加 Hard Blocker/Soft Cap 推导，`BUYABLE`/`WAIT`/`BLOCK` 只会在这一层出现 |
| `structural_state` | **结构事实层**：`WATCH\|READY\|REPAIRING\|PULLBACK\|CONFIRMED\|FAILED`，只看价格行为，不受任何闸门/软上限影响，闸门临时不通过时底层仍持续推进（2026-08-23 二次重构：此前误借用了 REPAIRING/CONFIRMING/BUYABLE/WAIT 这些属于展示层的名字，见第11.1节） |
| `recovery_high/pullback_low/pullback_started` | H1/L1 回踩结构追踪字段（2026-08-22 由弱定义"任意反弹即确认"改为两段式，见第4节状态机） |
| `signal_trade_date` | 当前结构态属于哪个交易日，跨日刷新时用于判断是否要重置日内字段（2026-08-22新增） |
| `setup_substate/refresh_sample_count` | 状态机辅助字段 |
| `price/prev_close/ma5/vwap/day_open/day_high/day_low/day_amount/turnover_rate` | 实时行情快照（`vwap` 2026-08-22新增，真实VWAP，取代此前用MA5顶替） |
| `auction_gap/auction_sector_gap/is_auction_exceeded` | 竞价相关（`auction_gap` 2026-08-22 改为真实开盘Gap，`auction_sector_gap`/`is_auction_exceeded` 仍是预留字段未写入） |
| `limit_price/limit_room` | 涨停价/剩余空间 |
| `technical_stop/standard_stop/stress_stop/stress_rr` | 三层止损位 + 压力情景风险回报比 |
| `regulatory_risk_level/signal_enabled/data_freshness_seconds` | 风险与数据新鲜度 |
| `trigger_reasons/block_reasons` | 本次刷新的触发/拦截原因（中文，分号分隔） |
| `last_refreshed_at/refresh_duration_ms/formula_version` | 刷新元信息 |

### `weak_to_strong_events`（追加写事件日志）

只在**展示态**真实改变时插入一条，字段含 `old_state/new_state/trigger_reasons/
block_reasons` + 当次刷新时的板块/龙头/价格快照 + `structural_state/recovery_high/
pullback_low`（2026-08-23新增，保留事件发生时刻的结构层原始快照——不用等专门的
回测框架，这是调 `w2s_pullback_min_pct` 回踩噪音阈值需要的最小可用数据源，现在就
能开始积累真实样本）。Phase 3 专属字段（`leadership_impact`/`profit_volume_ratio`/
`t1_supply_risk` 等）已建列，Phase 2 恒为 `NULL`。

### 顺手修复：`sector_daily_snapshots`

新增 `amount` 列 + `(sector_id, date)` 唯一约束。**该表此前从未被生产流程写入**（全仓库唯一
一处实例化在 `scripts/seed_mock_data.py` 造假数据脚本里），`daily_update.py` 现在每天调用
`upsert_sector_daily_snapshot()` 把它从死表变活表——这是 Sector Momentum Score 能算出"今天
比昨天变化"的前提。上线首日没有"昨天"基准，动量分返回 `None`，前端/数据均如实体现
"数据积累中"，不伪造历史对比。

---

## 4. 核心公式

### Market Trend Score（0-100）
```
核心指数加权平均（排除科创50/北证50——盘子小、噪声大，跟 Sector Gate 排除动态噪声板块
同一考量），各指数分值直接复用 index_trend_service 已有的趋势强度分（位置40+排列20+斜率20+动能20）：
TrendScore = 0.40·上证指数score + 0.35·深证成指score + 0.25·创业板指score
三个核心指数任一缺失 → None（不能悄悄拿两个当三个用）
```

### Risk Appetite Score（0-100，2026-08-22 二次修订）
```
UpDownScore(0-35)     = clamp(up_count/(up_count+down_count) * 35, 0, 35)
LimitScore(0-25)      = clamp(limit_up_count/(limit_up_count+limit_down_count) * 25, 0, 25)，缺失给12.5
MarketEffectScore(0-40) = clamp(20 + (profit_strength-loss_strength)/2 * weight, 0, 40)，缺失给20
  weight = 0.5（当日市场效应 breadth_source=tracked_pool，广度退化为近似）否则 1.0
RiskAppetite = UpDownScore + LimitScore + MarketEffectScore
涨跌家数缺失 → None（硬性输入，不能编造中性值掩盖数据缺失）
```
`profit_strength`/`loss_strength` 从 2026-08-23 起额外**独立**暴露在 `get_market_gate()`
返回值和 `/market-gate` 响应里（`market_effect_profit_strength`/`market_effect_loss_strength`/
`market_negative_feedback`，后者是 `loss_strength` 的 LOW/MEDIUM/HIGH/UNKNOWN 显式分级），
不再只是被揉进 `MarketEffectScore` 后就丢弃的中间值——risk_score 低到底是"普涨面不够"
还是"T-1冻结群体今天集体大面"，这是两种完全不同的市场环境，需要能分开看。

**改动原因**：原公式里两融余额5日变化率跟"今天9:35能不能做弱转强"这种日内决策相关性
不够强（两融更接近中短期资金背景），换成 `market_effect_service` 的 T-1冻结群体（昨日
涨停/首板/连板/炸板/跌停）次日真实反馈后，Risk Appetite 才是真正在回答"市场今天有没有
承接弱转强的情绪环境"。`market_effect` 部分取"最近一个已收盘、有完整数据的交易日"而不是
"今天"——冻结群体反馈需要 T 日的 `StockDailySnapshot` 才能算出"T-1群体在T日的真实表现"，
盘中今天的快照还没写入，用最近收盘日的结果代表"最近一次已知的市场承接环境"。

### Market Gate 四色分类
```
若 TrendScore>=55 且 RiskScore>=55 → GREEN
否则取 worst=min(TrendScore, RiskScore)：
  worst>=35 → YELLOW；worst>=20 → ORANGE；否则 → RED
任一分数缺失 → ORANGE（保守，不敢判GREEN也不夸大到RED）
```
默认只有 `RED` 触发状态机 BLOCK（`w2s_market_gate_blocked` 可配置），YELLOW/ORANGE 仅展示
警示不拦截——原始规格的"降低仓位/更严格执行"语义留给使用者自行判断，本雷达不做仓位建议。

### Sector Strength Score（0-100）
```
RankScore = 0.35·rank(rank_5d) + 0.25·rank(rank_10d) + 0.15·rank(rank_20d)
          + 0.10·rank(rank_lu) + 0.15·rank(rank_board)      # rank(r) = (6-r)*20，r=None→0
ActivityScore = clamp(board_height*12 + limit_up_count*6 + strong_stock_count*4, 0, 100)
Strength = clamp(0.6·RankScore + 0.4·ActivityScore, 0, 100)
```

### Sector Momentum Score（0-100，50=持平；无历史基准返回 None）
```
amount_chg_pct = (今日amount - 昨日amount) / max(昨日amount,1) * 100
momentum_raw = 今日涨幅*2.0 + Δlimit_up_count*8 + Δboard_height*10
             + Δemotion_score*0.5 + Δstrong_stock_count*4 + amount_chg_pct*0.3
Momentum = clamp(50 + momentum_raw, 0, 100)
```
2026-08-22 修订：无历史基准（`prev is None`）时返回 `None` 而不是固定 50——固定 50
看起来像一个真实分数，容易被误读成"算出了持平"，实际只是没数据。

### Sector 7 分类
`phase∈{0,1}→NEW_START`、`2→EXPANDING`、`3→MAIN_UPTREND`、`5→DECLINING`、`6→DEAD`；
`phase=4`（分歧阶段）按板块高度/风险分/情绪分变化算健康度（`compute_divergence_health`，
2026-08-23 起独立暴露为 `sector_divergence_health` 字段，不再是算完即丢弃的中间值），
≥阈值(默认50)→`HEALTHY_DIVERGENCE`，否则→`HIGH_LEVEL_WARNING`。

`NEW_START` 允许候选**进入观察/结构追踪**（不会被 Sector Gate 硬性 BLOCK），但**不允许
放行到 BUYABLE**——展示态会被状态机的软上限限制到最多 `READY`（见第4节状态机）。这两个
"允许"含义不同，前者是"可以出现在候选池、Sector Gate 不拦截"，后者是"可以被判定为可
交易信号"，本文档统一用"Sector Gate 允许列表"指前者、"软上限"指后者，避免混用同一个
"放行"字眼指代不同层级。

### Core Leader Score（0-100）
```
RS_in_sector(0-40)   同Theme内 leader_score 排名，前4名给 40/28/18/10 分
RS_vs_market(0-15)   leader_score 在全体强势池中的百分位 * 15
BoardHistory(0-20)   clamp(board_count_60d*4 + limit_up_days_20d*1.5, 0, 20)
CapitalCapacity(0-10) clamp(昨日换手率/3, 0, 10)
DivergenceResilience(0-10)  昨日跌幅>-3%→10分，>-6%→5分，否则0分
SectorLeadershipBonus(0-5)  StockSectorRelation.is_leader → +5   【当前恒为0，见下方说明】
```
> **已知限制**：`StockSectorRelation.is_leader` 在全部生产代码路径里**从未被置为 `True`**
> （唯一写 `True` 的地方是 `scripts/seed_mock_data.py` 造假数据脚本）。这意味着
> `SectorLeadershipBonus` 目前不是"理论上可能循环定义"的风险，而是**确定性地恒为0**，
> Core Leader Score 实际打分区间是 0-95 而非文档写的 0-100。原因：本仓库目前没有生产
> 流程会真正判定并回写"某只股票是某板块的历史/公认龙头"这个标签，做一个可信的龙头
> 识别写入器是新增范围（可能与未来 Setup Type 分型工作重叠），本次不做，如实记录在此，
> 详见第8节已知局限第5条。

同 Theme 排序后，第一二名分差 < 阈值(默认8分) → 两者都标 `undetermined`（"龙头未决"）；
分差达标 → 第一名 `core`、第二名 `backup`；排名≥3 → `non_leader`（硬性 BLOCK）。
**`undetermined` 不再硬性 BLOCK**（2026-08-22 修订，见第11节）——早期龙头竞争阶段如果
直接判死会错过市场自己筛选真龙头的过程，改成软上限：结构可以正常推进展示，但最高只
显示到 `CONFIRMING`，不会显示还没真正确立龙头就给的 `BUYABLE`。

### Space Gate（涨停空间降级判断）
```
limit_room（已算） < 阈值(默认2%) → 空间不充分。limit_room 缺失同样判不充分（不能拿
缺失数据当宽松放行的理由）。只在 structural_state 已经是 BUYABLE 时把展示态降级为
WAIT，底层 structural_state 保留 BUYABLE 不清空——空间会随价格逐分钟变化，候选本身
没有失效，空间一旦重新充足展示立刻恢复，不用重新走一遍回踩结构。
```

### 三层止损 + 压力情景风险回报比（Stress R/R）

字段名沿用 `technical_stop`/`standard_stop`/`stress_stop`（DB 列名/API 不改），但"标准
止损"这个名字容易被理解成行业公认的标准做法，实际只是"技术止损位基础上留个缓冲"，
下面用更精确的概念名描述三者分别在回答什么问题：

```
technical_stop（结构失效位）= pullback_low（L1，回踩阶段的滚动最低价），缺失时退回 MA5
  —— 回答"价格跌破哪里，说明这次回踩修复的结构判断本身就错了"

standard_stop（缓冲失效位）= technical_stop * (1 - 2%)
  —— 在结构失效位基础上留2%缓冲，避免正常回踩噪音（未到 w2s_pullback_min_pct 阈值的
     小幅波动）刚好触及技术止损就被震出

stress_stop（T+1压力情景止损）= price * (1 - 跌停幅度%)
  —— 不是"预测明天会跌停"，而是"如果买入后遇到最坏情况——次日直接跌停开盘"，模拟
     这种 T+1 机制下无法当日止损规避的极端情形会亏多少

Stress R/R = 今日剩余涨停空间% / 跌停幅度%
```
跌停幅度%使用现有的 `get_limit_pct(code, is_st)`（按证券规则动态算出：主板/创业板
科创板/北交所/ST 幅度各不相同，代码里从来不是写死的常量——这一点在早期文档版本里
曾经用具体百分比数字举例，容易被误读成硬编码常量，本版本起统一只写函数名不写数字，
避免这种误解反复出现），是同板块类型的固定值，不是概率加权预期——这个指标只回答
"如果最坏情况发生，今天剩的上涨空间值不值得担这个风险"，**不是完整期望收益模型，
不构成止损/止盈建议**。

### 状态机（2026-08-23 二次重构：结构事实层/交易决策层彻底分离）

**为什么二次重构**：2026-08-22 那版虽然已经把"结构进度"和"闸门覆盖"分成了
`structural_state`/`current_state` 两个字段，但 `structural_state` 内部仍然复用
`REPAIRING`/`CONFIRMING`/`BUYABLE`/`WAIT` 这套本该只属于**展示层**的词汇——这些名字
天然带着"是否可以交易"的含义，一旦复用就很容易在后续开发中把交易决策逻辑（比如空间
判断）重新写回结构层内部，重犯"两层职责糅在一起"的老问题。二次重构给结构事实层换了
一套独立词表，物理上杜绝这类回退。

```
结构事实层（structural_state：WATCH/READY/REPAIRING/PULLBACK/CONFIRMED/FAILED，
只看价格行为，不受任何闸门/软上限影响，持续推进）：
  WATCH/READY/FAILED → [现价 > repair_anchor=max(昨收,VWAP或MA5)]  → REPAIRING
                                                                     （recovery_high=现价，开始建H1）
                        （FAILED 是合法的重新进入点，等价于 WATCH，不是死态）
  REPAIRING          → [现价 ≤ repair_anchor]                     → FAILED（结构失效，清空H1/L1追踪）
                      → [现价创新高]                               → REPAIRING（recovery_high 上移）
                      → [现价回落超过 pullback_min_pct(默认1.5%)]   → PULLBACK（冻结recovery_high=H1，开始记pullback_low=L1）
  PULLBACK           → [现价 ≤ repair_anchor]                     → FAILED（跌穿起点，回踩确认失败）
                      → [现价 > 冻结的 recovery_high]               → CONFIRMED（二次突破H1，结构确认完成）
                      → [否则]                                     → PULLBACK（继续记录pullback_low）
  CONFIRMED          → [现价 ≤ repair_anchor]                     → FAILED（信号失效，清空追踪）
                      → [否则]                                     → CONFIRMED

交易决策层（current_state：WATCH/READY/REPAIRING/CONFIRMING/BUYABLE/WAIT/BLOCK，由
derive_display_state() 一处函数把结构事实层 + Hard Blocker + Soft Cap 映射成展示值——
`BUYABLE`/`WAIT`/`BLOCK` 只会在这一层出现，结构事实层永远不会产出这三个值）：
  structural_state==FAILED                → WAIT
  structural_state∈{WATCH,READY}          → 原样展示（WATCH/READY）
  Hard Blocker 命中（数据过期/大盘RED/板块分类不允许/龙头non_leader/监管风险过高/观察期已过）
                                            → BLOCK，但 structural_state 照常在底层推进，
                                              闸门一恢复展示立刻反映真实进度，不用重新走结构
  Soft Cap·NEW_START（板块刚起步）         → READY（即使结构已到 REPAIRING/PULLBACK/CONFIRMED）
  structural_state==REPAIRING              → REPAIRING
  structural_state==PULLBACK               → CONFIRMING
  structural_state==CONFIRMED 且龙头未决    → CONFIRMING（不隐藏"已确认"这个事实，只是不放行到BUYABLE）
  structural_state==CONFIRMED 且空间不足    → WAIT（底层 structural_state 仍是 CONFIRMED，不清空）
  structural_state==CONFIRMED 且以上都通过  → BUYABLE
```

`WARNING`/`EXIT`（持仓监控态）**不实现**——本仓库没有持仓/成交跟踪能力，硬做即编数据。
`CONFIRMING`/回踩识别依赖同一交易日内多次 `/refresh` 采样（不是连续的分钟级监控），
`refresh_sample_count` 如实暴露"基于N次采样"。真实 VWAP 通过实时报价的成交额/成交量
算出（`amount/(volume*100)`，量为0或落在[当日最低价,最高价]区间外时退回 MA5，见
第11.1节合理性校验）。

---

## 5. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/market-gate` | 大盘闸门当前状态（趋势分/风险偏好分/四色/各指数分/市场效应置信度/市场负反馈分级） |
| GET | `/candidates` | 候选列表（`active_only` 默认true），含交易决策层（`current_state`）与结构事实层（`structural_state`） |
| GET | `/candidates/{code}` | 详情 + 8组Checklist（MARKET/SECTOR/LEADER/DIVERGENCE/SETUP/SPACE/CHIPS/RISK；仅 CHIPS 仍是"Phase 2"占位，其余7组均为真实pass/fail；SETUP 组会显示"结构事实已到X，被闸门/软上限临时覆盖展示"这类透明说明） |
| POST | `/refresh` | 需登录；启动后台刷新线程，独立锁 `/tmp/tradeflux_w2s_radar.lock` |
| GET | `/refresh/status` | 轮询刷新任务状态 |
| GET | `/events` | 状态变化事件日志（`stock_code` 可选过滤） |
| GET/PUT | `/config` | 读写 Prompt 与阈值配置（复用 `AppConfig` 表，不新建配置表） |

## 6. 调度与锁

独立锁文件 `/tmp/tradeflux_w2s_radar.lock`，跟 `daily_update`/`sync_boards` 共用的
`/tmp/tradeflux_daily_update.lock` **完全分开**——雷达刷新只碰自己的两张新表，不该被全量
更新阻塞，也不该反过来阻塞全量更新。新增 `mon-fri 09:26` 定时任务（`misfire_grace_time=300`，
竞价数据过时几分钟就没意义），跟 `daily_update_preopen`（09:27）错开1分钟。失败不重试——
非关键路径，用户随时可在页面手动点刷新补一次。

**已知运营缺口**：目前只有 09:26 这一次自动刷新，全天其余时段的状态推进依赖用户手动点
"立即刷新"。这意味着如果没人在交易时段主动刷新，H1/L1 回踩结构的采样密度会很稀疏
（不是连续监控）。是否需要增加盘中多次自动刷新（比如每5-15分钟一次）不在本次范围内，
留待后续评估触发频率与东财接口压力的取舍。

## 7. 默认参数（存于 `AppConfig`，`GET/PUT /config` 可改，改动即时生效无需重启）

| key | 默认值 | 含义 |
|---|---|---|
| `w2s_min_yesterday_amount` | 3亿元 | Prompt 文本里的昨日成交额阈值说明用（本地不再二次校验，见第8节） |
| `w2s_leader_gap_threshold` | 8分 | Core Leader Score 分差阈值 |
| `w2s_observation_window_days` | 7天 | 候选连续miss多少天后移出 |
| `w2s_divergence_health_threshold` | 50 | phase=4 细分阈值 |
| `w2s_auction_gap_min` | 3% | 竞价Gap超预期阈值 |
| `w2s_space_min_room_pct` | 2% | 涨停空间不足阈值，低于此值 BUYABLE 降级 WAIT |
| `w2s_pullback_min_pct` | 1.5% | 有效回踩最小幅度，低于此值视为噪音，不冻结H1（2026-08-22新增） |
| `w2s_sector_gate_allowed` | NEW_START,EXPANDING,MAIN_UPTREND,HEALTHY_DIVERGENCE | 允许进入状态机的板块分类（NEW_START仍在软上限限制内） |
| `w2s_regulatory_risk_cap` | HIGH,EXTREME | 达到此级别即BLOCK |
| `w2s_market_gate_blocked` | RED | 大盘闸门达到此颜色即BLOCK |

## 8. 已知局限（如实记录，不在产品里假装不存在）

1. **候选池"昨日成交额"条件不在本地复核范围**：完全信任东财 Prompt 自身的数值过滤。
   曾经短暂改用批量实时报价的 amount 本地校验，又主动撤回（2026-08-22）——那个值在
   盘前/盘中调用时代表"当前累计成交额"，跟"昨日全天成交额"是两个不同的变量，不是
   近似而是语义错误，尤其 09:27 那次运行会产生系统性偏差、误伤本该入选的候选。宁可
   完全信任东财自己的数值过滤（成交额这种无歧义标量比较出错概率本来就低），也不用
   语义不对的替代变量冒充校验结果。真要本地核验，需要先把逐日成交额补进
   `StockDailySnapshot`（意味着要扩展 K 线重建管线，那里有个未彻底解决的历史 bug，
   见第9节），是独立的、更大的改动。
2. **回踩结构识别基于离散采样，不是连续监控**：H1/L1 两段式判断依赖每次 `/refresh`
   拿到的价格快照，不是分钟级连续数据。采样越稀疏，越可能错过真正的高点/低点（比如
   两次刷新之间价格冲高又回落，中间的极值不会被记录）。`refresh_sample_count` 如实
   暴露这一点。真要提升精度需要建立候选专属的日内快照引擎（Phase 3 方向，需要新的
   数据管道，本次不做）。
3. **竞价Gap 用真实开盘价计算，但"竞价"和"开盘"不完全是一回事**：`auction_gap` 现在
   是 `(今开-昨收)/昨收`（2026-08-22 由"当前涨跌幅近似"改为这个），今开一旦开盘就
   固定不再漂移，但严格意义上"集合竞价的最终价"和"连续竞价的第一笔成交价"在实践中
   通常是同一个数字，此处不做更细的区分。
4. **Market Effect 数据在广度退化为 tracked_pool 时置信度降级但不隔绝**：`Risk
   Appetite Score` 里的市场效应分量在 `breadth_source=tracked_pool` 时权重减半
   （见第4节公式），但仍然参与计算，不是完全剔除——半信半疑好过完全不用。
5. **`SectorLeadershipBonus`（Core Leader Score 里的板块龙头加成）目前恒为0**：
   `StockSectorRelation.is_leader` 在全部生产代码路径里从未被置为 `True`，只有
   `scripts/seed_mock_data.py` 造假数据脚本会写它。做一个可信的"历史/公认龙头"识别
   写入器需要新的判定逻辑（比如板块内长期涨幅/连板历史排名前N自动打标，或人工标注
   接口），是独立的新增范围，本次不做，见第4节 Core Leader Score 公式旁的详细说明。
6. **没有"有没有主线"（Mainline Clarity）判断**：当前每个候选独立走 Sector Gate，
   不回答"今天全市场有没有清晰的主线板块，还是多个板块在抢筹"这种更宏观的问题。
   这需要一个跨候选、跨板块的市场层面新分类器（比如 `CLEAR/COMPETING/ROTATION/NONE`），
   是不同于现有 Market Gate（回答"今天市场敢不敢接"）的新分析维度，不是对现有公式的
   小修补，暂不做，跟踪于第12节 Phase 3 待办。
7. **多主题归属候选（一只股票同时属于多个热门题材）没有专门建模**：`sector_id`
   直接取 `Stock.primary_sector_id`（单一归属），如果一只股票在 CPO/数据中心/AI算力
   等多个题材里都能算"龙头候选"，目前只会按它的主板块打分，不会展示"在题材A排第1、
   在题材B排第3"这种多维视角。评审建议了 `Candidate × Theme` 关系模型，但复杂度较高
   且当前主板块归属已覆盖大多数场景，暂不做，跟踪于第12节。
8. **东财 Prompt 解析结果没有专门的降级监控**：候选池发现依赖东财对 Prompt 关键词的
   解析结果（`fetch_strong_pool_codes`），如果东财侧解析逻辑变化导致召回数量异常下降
   （比如从平时的20-50只骤降到个位数），目前没有自动检测/告警，只能靠人工观察候选池
   页面数量异常。跟踪于第12节。

## 9. 遗留问题（非本次引入，跟进记录）

`daily_update.py` 的 K 线重建管线里有一个**尚未彻底解决**的 board_count 偶发滞后 bug
（曾在 001267 汇绿生态案例中复现：连板数展示滞后于实际情况），根因排查在另一次会话中
中途搁置。本次弱转强雷达的候选发现/龙头打分复用了同一批 `Stock` 字段
（`board_count_60d`/`limit_up_days_20d`），**理论上会受到相同 bug 影响**，但概率与既有
强势股池/涨跌停池一致，不是雷达新引入的问题，未来排查该 bug 时一并验证雷达是否修复。

---

## 10. 弱转强分型（Setup Type）——架构预留，未接入生产逻辑

外部代码评审指出：当前"一套状态机处理所有股票"其实混合了至少三种性质不同的弱转强
（趋势龙头回踩修复 / 情绪龙头断板反抽 / 深水反核），三者的强弱定义、龙头评分权重、
市场环境要求本质不同，长期应该拆成 `TREND`/`EMOTION`/`ANTI_NUCLEAR` 三套 Policy，
共享同一套 Orchestrator/Common Gates（Market/Sector/Space/Regulatory/生命周期/事件日志）。

**这次只做了最小的架构预留，没有重写生产状态机**：`weak_to_strong_candidates` 新增
`setup_type` 字段，恒为 `GENERIC`，不接任何分型专属逻辑。这是刻意的顺序决策——当前
状态机刚修完几个真实存在的语义 bug（见第11节），在语义还不完全可信的基础上扩展三套
Policy 只会把 bug 一起复制三份。计划顺序：

1. （已完成）修正现有状态机的正确性问题
2. （当前所在位置）架构预留 `setup_type` 字段 + 抽象接口占位，不改变生产行为
3. （待规划）三套 Policy 用 **Shadow Mode** 并行计算记录，不参与正式 BUYABLE 判断，
   积累实际 candidate-session 样本
4. （待规划）Golden Case（比如典型的深水反核案例）+ 回测验证后，再逐步切换生产决策

## 11. 2026-08-22 状态机纠错记录（外部评审驱动）

这一轮修改由外部代码评审发起，逐条核实代码后确认是真实 bug（不是设计取舍），已修复：

| 问题 | 根因 | 修复 |
|---|---|---|
| CONFIRMING 判定过弱 | `price > pullback_low` 而 pullback_low 只是滚动最低价，任意小反弹就触发 | 改为 H1/L1 两段式：先冻结修复高点(H1)，记录回踩低点(L1)，突破冻结的H1才算确认 |
| BLOCK 是死态 | 所有状态转移分支都不匹配 `current_state=="BLOCK"`，闸门恢复后也无法跳出 | 展示态/结构态解耦，闸门每次独立重算，不再依赖历史是否 BLOCK |
| 跨日状态未重置 | 候选续期只更新 `last_seen_date`，没碰 `current_state`/`pullback_low` | 新增 `signal_trade_date`，跨日时清空全部日内字段，保留生命周期字段 |
| 龙头未决直接 BLOCK | 早期龙头竞争阶段被直接判死，错过市场自己筛选真龙头的过程 | 改为软上限，最高展示到 CONFIRMING，不隐藏结构进度 |
| NEW_START 与成熟板块同级放行 | 新题材第一天爆发跟"已证明过自己强、现在分歧"的核心弱转强混在一起 | 软上限，NEW_START 最高展示到 READY |
| VWAP 用 MA5 顶替 | 未意识到实时报价已有 amount/volume 可以直接算出真实VWAP | 改用 `amount/(volume*100)` 算真实VWAP，缺失时才退回MA5 |
| 竞价Gap 用当前涨跌幅近似 | 未意识到实时报价已有今开/昨收字段 | 改用 `(今开-昨收)/昨收`，全天固定不漂移 |
| 昨日成交额用今日实时报价核验 | 语义错误：今日累计成交额≠昨日全天成交额，09:27运行时尤其失真 | 撤回本地核验，改为完全信任东财Prompt自身过滤 |
| Sector Momentum 固定50分 | 看起来像真实分数，容易被误读成"算出了持平" | 无历史基准时返回 `None`，展示层显示"数据积累中" |
| Risk Appetite 用两融余额 | 两融是中短期资金背景，跟"今天能不能做弱转强"相关性不够 | 换成 T-1冻结群体（市场效应引擎）次日真实反馈 |

`formula_version` 从 `w2s_radar_v0.3.0` 升至 `w2s_radar_v0.4.0`。这一轮没有做的：三类
弱转强拆分（架构预留但不接生产逻辑，见第10节）、完整监管风险0-100分（主动撤回，跟
用户原始规格冲突）、Phase 3 全部四项（需要新的分钟级数据管道，属于独立的更大决策）。

### 11.1 2026-08-23 第二轮修订（外部评审第三轮驱动）

第三轮评审的核心质疑：`structural_state` 虽然已经跟 `current_state` 分开存了，但内部
复用 `REPAIRING`/`CONFIRMING`/`BUYABLE`/`WAIT` 这套展示层词汇本身就是隐患——`BUYABLE`/
`WAIT` 天然带着"是否可以交易"的含义，不应该是结构事实层会产出的值。这次全部核实后
采纳，另有几项数据健壮性/可解释性改进一并落地：

| 问题 | 处理 |
|---|---|
| 结构事实层复用展示层词汇，语义上仍是"半糅合" | `structural_state` 换成独立词表 `WATCH/READY/REPAIRING/PULLBACK/CONFIRMED/FAILED`；新增 `derive_display_state()` 一个函数收拢所有"结构事实→展示态"的映射，是全仓库唯一允许产出 `BUYABLE` 的地方（见第4节状态机） |
| `FAILED`（原 `WAIT`）此前是否算死态未明确 | 明确 `FAILED` 是合法重新进入点，等价于 `WATCH`，价格重新收复 `repair_anchor` 即可再次进入 `REPAIRING` |
| VWAP/开盘Gap 没有合理性校验，接口字段异常时可能产出物理上不可能的值 | VWAP 必须落在 `[当日最低价,当日最高价]` 区间；Gap 不能超过 `get_limit_pct` 算出的涨跌停幅度（留10%容差）；任一校验不通过则置 `None`，退化为不依赖该值的近似判断，不让异常数据参与结构判断 |
| Market/Sector 负反馈信号被揉进复合分数后即丢弃，界面看不出"低分到底因为什么" | Market 层新增 `market_effect_profit_strength/loss_strength/market_negative_feedback`（LOW/MEDIUM/HIGH/UNKNOWN）三个独立字段；Sector 层新增 `sector_divergence_health`（phase=4分歧阶段的原始健康度分数），均在 `/market-gate`、候选详情 Checklist 里可见 |
| H1/L1 回踩阈值调参没有历史样本可看 | `weak_to_strong_events` 新增 `structural_state/recovery_high/pullback_low` 三列，每次展示态变化都保留当时的结构层原始快照，不用等回测框架就能开始积累调参数据 |
| "五道硬性闸门"的产品描述不准确 | 改用三层判断链描述：Hard Blocker（硬性拦截）/ Soft Cap（软上限）/ Setup Progression（结构事实推进），见第1节 |
| `NEW_START` 板块"放行"含义模糊（Sector Gate允许列表 vs 可以BUYABLE） | 文档统一区分"Sector Gate 允许列表"（可以进入候选池追踪）和"软上限"（是否放行到BUYABLE），见第4节 Sector 7分类 |
| Stress R/R 文档仍写"分母是主板9.9%常数" | 删除具体百分比数字，统一只写"使用现有 `get_limit_pct(code,is_st)`"，避免继续被误读成硬编码（这是同一处误解在第一轮评审里已经被指出过一次，说明上次修复不彻底，这次改成不写具体数字从根源上避免复发） |
| "标准止损"命名容易被理解成行业标准做法 | 不改字段名（DB/API 兼容），文档改用"结构失效位/缓冲失效位/T+1压力情景止损"三个更精确的概念名分别描述，见第4节 |
| Core Leader Score 的 `SectorLeadershipBonus` 可能循环定义 | 核实后确认**不是循环**（没有任何反馈路径），但比"理论上循环"更严重——`is_leader` 从未被生产代码置为 `True`，该分量**恒为0**，已如实记录在第8节已知局限第5条 |

这一轮没有做的（评审同时提出但明确判断为独立范围，不在本次实施）：Mainline Clarity
市场主线分类器、Emotion Leader/Trend Anchor 分离展示、`Candidate × Theme` 多主题模型、
Prompt解析降级监控——均已记录在第8节已知局限第6-8条 + 第12节 Phase 3 待办，等待
后续单独评估；候选专属日内快照引擎的**自动化定时抓取频率**本身（跟第6节"已知运营
缺口"是同一个决策点）也刻意没有动，因为这会改变对东财接口的稳定态请求压力，需要
单独跟用户确认，不能在一次批量修复里顺带静默加上。

---

## 12. Phase 2 / Phase 3 待办跟踪

> 供后续会话直接续接，每项标注了依赖前提。

### Phase 2 —— 已全部完成
- [x] Market Gate（大盘趋势+风险偏好四色分级）
- [x] Space Gate 降级判断 + 三层止损 + Stress R/R
- [x] 候选池成交额条件本地二次校验（后又主动撤回，改为信任东财Prompt，见第8节第1条）
- [x] ~~完整监管风险 0-100 分~~ 不做，主动撤回（跟用户原始规格冲突）
- [x] 2026-08-22 状态机纠错批次（见第11节）
- [x] 2026-08-23 结构事实层/交易决策层二次分离 + 数据合理性校验 + 市场板块负反馈
      字段 + H1/L1原始快照（见第11.1节）

### Phase 3（依赖分钟级数据，本仓库当前无该数据源，需先起数据链路）
- [ ] 候选专属日内快照引擎（外部评审建议提前到 Phase 3 最优先——是回踩结构识别精度、
      真正意义上的分钟级监控、未来回测重放的共同基础设施；评审进一步指出可以现在就
      低成本先做：复用 `/refresh` 里已经拉到的 `fetch_stock_quotes_batch` 报价数据写一张
      快照表即可，不需要新的外部请求——**唯一需要额外确认的是自动定时抓取的频率/cron**，
      这会改变对东财接口的稳定态请求压力，需单独跟用户确认后再加，不能静默引入）
- [ ] Golden Case 测试场景（评审指出不需要等完整回测框架——可以先用合成的多步价格
      序列 + 已知真实案例如"神奇制药"，针对 `compute_structural_transition`/
      `derive_display_state` 写语义级测试，独立于底下的历史回测框架）
- [ ] 回测框架（历史数据批量验证策略有效性，Engineering/Semantic-Golden-Case/Strategy
      三层验证体系的最后一层，见第1节"关于验证记录的重要区分"）
- [ ] T+1 供给风险
- [ ] Leadership Impact V0.1（板块带动性）
- [ ] 日内获利盘估算（明确标注"估算"而非真实筹码）
- [ ] Mainline Clarity 市场主线分类器（今天全市场有没有清晰主线，见第8节第6条）
- [ ] Emotion Leader / Trend Anchor 角色分离展示（见第8节，跟 Setup Type 三类拆分
      工作有重叠，建议放在那之后一起做）
- [ ] `Candidate × Theme` 多主题归属模型（见第8节第7条）
- [ ] 东财 Prompt 解析降级监控（候选召回数量异常骤降时告警，见第8节第8条）

### Setup Type 三类拆分（独立跟踪，见第10节，不计入 Phase 3 顺序）
- [x] 架构预留：`setup_type` 字段 + 顺序规划
- [ ] TREND/EMOTION/ANTI_NUCLEAR 三套 Policy（Shadow Mode，不参与生产决策）
- [ ] Golden Case 验证后逐步切换生产

### 持续优化（不分 Phase，随时可做的小改进）
- [ ] 前端候选表格补充列排序（参考 `TurnoverOverview.tsx` 的 `SortTh` 模式）
- [ ] `/refresh` 耗时超过10秒目标时的告警/降级策略
- [ ] Checklist 详情文案根据具体数值给出更细粒度的解释（目前偏摘要）
- [ ] 评估是否需要增加盘中多次自动刷新（见第6节"已知运营缺口"）
