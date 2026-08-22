# 弱转强雷达（Weak-to-Strong Radar）

> 文档状态：Phase 1 + Phase 2 Market Gate 已上线，Phase 2 其余项/Phase 3 待办跟踪中
> 路由：`/weak-to-strong-radar`　·　API 前缀：`/api/weak-to-strong-radar`
> 最后更新：2026-08-22

---

## 1. 核心原则

**「弱转强成立」≠「值得买入」。**

候选股票必须**同时**通过大盘闸门（Market Gate）、板块闸门（Sector Gate）、龙头闸门
（Leader Gate）、回踩结构确认四道硬性关卡，才会被状态机推进到 `BUYABLE`——不是把各项
指标线性加权后直接吐出买入信号。Space Gate（涨停空间降级判断）、Chips（筹码/获利盘）、
Risk（Stress R/R、三层止损）这三组仍未实现，Checklist UI 上诚实显示灰色 **"Phase 2"**
标签，绝不伪造 ✓ / ✗。

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
       ├─ 本地二次校验排名/均线类条件（不信任东财 Prompt 解析结果）
       └─ upsert weak_to_strong_candidates（命中续期 / 连续miss超窗口失活）

盘中快速刷新（独立 API，09:26 定时 + 手动按钮，目标 <5-10秒）
  w2s_refresh_service.run_refresh()
    ├─ w2s_market_gate_service.get_market_gate()  → Market Trend/Risk Appetite/四色（全局算一次）
    ├─ 按 Theme(=Stock.primary_sector_id) 分组
    ├─ w2s_sector_gate_service.score_sector()   → Sector Strength/Momentum/7分类
    ├─ w2s_leader_gate_service.score_leaders_for_theme() → Core Leader Score/排名
    ├─ eastmoney_fetcher.fetch_stock_quotes_batch() → 批量实时报价
    ├─ w2s_state_machine.compute_next_state()   → 7态状态机（含 Market Gate 拦截判断）
    └─ 状态真变化 → 写一条 weak_to_strong_events（追加写，不覆盖）
```

后端服务文件均以 `w2s_` 前缀命名（`backend/app/services/w2s_*.py`），核心打分/状态机函数
是**纯函数**（不开 DB session，只吃标量输入），DB 相关的查询/写入逻辑单独放在薄封装里，
方便单测（`backend/tests/test_w2s_*.py`，43条全绿）。Market Gate 不新起数据管道——两个
分数都基于已有的、daily_update 每天同步的数据：指数趋势复用
`index_trend_service.get_market_trend()`（读库），风险偏好复用 `MarketBreadthDaily`
（两融/涨跌统计，大盘趋势页同一份数据源）。

---

## 3. 数据库设计

### `weak_to_strong_candidates`（当前态，一股一行）

| 字段 | 说明 |
|---|---|
| `stock_id/stock_code/stock_name` | 股票标识 |
| `first_seen_date/last_seen_date/consecutive_miss_days/candidate_source/is_active` | 候选生命周期 |
| `sector_id/sector_name/sector_category/sector_strength_score/sector_momentum_score` | Sector Gate 结果 |
| `leader_type/leader_rank/leader_score` | Leader Gate 结果 |
| `current_state/setup_substate/pullback_low/refresh_sample_count` | 状态机状态 |
| `price/prev_close/ma5/day_open/day_high/day_low/day_amount/turnover_rate` | 实时行情快照 |
| `auction_gap/auction_sector_gap/is_auction_exceeded` | 竞价相关（`auction_sector_gap`/`is_auction_exceeded` Phase 1 未写入，预留字段） |
| `limit_price/limit_room` | 涨停价/剩余空间 |
| `regulatory_risk_level/signal_enabled/data_freshness_seconds` | 风险与数据新鲜度 |
| `trigger_reasons/block_reasons` | 本次刷新的触发/拦截原因（中文，分号分隔） |
| `last_refreshed_at/refresh_duration_ms/formula_version` | 刷新元信息 |

### `weak_to_strong_events`（追加写事件日志）

只在 `current_state` 真实改变时插入一条，字段含 `old_state/new_state/trigger_reasons/
block_reasons` + 当次刷新时的板块/龙头/价格快照。Phase 2/3 专属字段（`market_trend_score`/
`leadership_impact`/`vwap`/`stress_rr` 等）已建列，Phase 1 恒为 `NULL`。

### 顺手修复：`sector_daily_snapshots`

新增 `amount` 列 + `(sector_id, date)` 唯一约束。**该表此前从未被生产流程写入**（全仓库唯一
一处实例化在 `scripts/seed_mock_data.py` 造假数据脚本里），`daily_update.py` 现在每天调用
`upsert_sector_daily_snapshot()` 把它从死表变活表——这是 Sector Momentum Score 能算出"今天
比昨天变化"的前提。上线首日没有"昨天"基准，动量分固定给 50（中性），前端/数据均如实体现
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

### Risk Appetite Score（0-100）
```
UpDownScore(0-40)    = clamp(up_count/(up_count+down_count) * 40, 0, 40)
LimitScore(0-30)     = clamp(limit_up_count/(limit_up_count+limit_down_count) * 30, 0, 30)，缺失给15
MarginScore(0-30)    = clamp(15 + 两融余额5日变化率*3, 0, 30)，缺失给15
RiskAppetite = UpDownScore + LimitScore + MarginScore
涨跌家数缺失 → None（硬性输入，不能编造中性值掩盖数据缺失）
```

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

### Sector Momentum Score（0-100，50=持平；首日/无历史基准固定50）
```
amount_chg_pct = (今日amount - 昨日amount) / max(昨日amount,1) * 100
momentum_raw = 今日涨幅*2.0 + Δlimit_up_count*8 + Δboard_height*10
             + Δemotion_score*0.5 + Δstrong_stock_count*4 + amount_chg_pct*0.3
Momentum = clamp(50 + momentum_raw, 0, 100)
```

### Sector 7 分类
`phase∈{0,1}→NEW_START`、`2→EXPANDING`、`3→MAIN_UPTREND`、`5→DECLINING`、`6→DEAD`；
`phase=4`（分歧阶段）按板块高度/风险分/情绪分变化算健康度，≥阈值(默认50)→
`HEALTHY_DIVERGENCE`，否则→`HIGH_LEVEL_WARNING`。默认允许 WATCH+ 的分类：
`NEW_START/EXPANDING/MAIN_UPTREND/HEALTHY_DIVERGENCE`。

### Core Leader Score（0-100）
```
RS_in_sector(0-40)   同Theme内 leader_score 排名，前4名给 40/28/18/10 分
RS_vs_market(0-15)   leader_score 在全体强势池中的百分位 * 15
BoardHistory(0-20)   clamp(board_count_60d*4 + limit_up_days_20d*1.5, 0, 20)
CapitalCapacity(0-10) clamp(昨日换手率/3, 0, 10)
DivergenceResilience(0-10)  昨日跌幅>-3%→10分，>-6%→5分，否则0分
SectorLeadershipBonus(0-5)  StockSectorRelation.is_leader → +5
```
同 Theme 排序后，第一二名分差 < 阈值(默认8分) → 两者都标 `undetermined`（"龙头未决"，
不强行指定）；分差达标 → 第一名 `core`、第二名 `backup`；排名≥3 → `non_leader`（直接 BLOCK）。

### 7 态状态机

```
WATCH → [竞价Gap ≥ 阈值(默认3%)，仅09:25后判定一次] → READY
WATCH/READY → [现价 > max(昨收,MA5)] → REPAIRING
REPAIRING → [现价 ≤ 修复关键位] → WAIT
          → [现价 > 回踩低点pullback_low] → CONFIRMING
CONFIRMING → [现价 ≤ 回踩低点] → WAIT
           → [否则] → BUYABLE
BUYABLE → [现价 ≤ 修复关键位] → WAIT

任意态 → BLOCK（优先级最高，逐项检查）：
  数据过期(signal_enabled=False) → 大盘闸门达到封顶(默认仅RED) → 板块分类不在允许列表
  → 龙头non_leader/undetermined → 监管风险达到封顶(默认HIGH,EXTREME) → 候选观察期已过
```
Market Gate 是全局的——同一次刷新里所有候选共用同一个市场状态，不是逐股算，每次
`/refresh` 只调用一次 `get_market_gate()`。

`WARNING`/`EXIT`（持仓监控态）**Phase 1 不实现**——本仓库没有持仓/成交跟踪能力，硬做即编数据。
`CONFIRMING` 的判断依赖同一交易日内多次 `/refresh` 采样，`refresh_sample_count` 如实暴露
"基于N次采样"，不假装连续监控。真实 VWAP 缺失，用 `max(昨收, MA5)` 近似，前端/文档均标注。

---

## 5. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/market-gate` | 大盘闸门当前状态（趋势分/风险偏好分/四色/各指数分） |
| GET | `/candidates` | 候选列表（`active_only` 默认true） |
| GET | `/candidates/{code}` | 详情 + 8组Checklist（MARKET/SECTOR/LEADER/DIVERGENCE/SETUP/SPACE/CHIPS/RISK；MARKET组现为真实pass/fail） |
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

## 7. 默认参数（存于 `AppConfig`，`GET/PUT /config` 可改，改动即时生效无需重启）

| key | 默认值 | 含义 |
|---|---|---|
| `w2s_min_yesterday_amount` | 3亿元 | 候选最低昨日成交额（当前信任东财Prompt数值过滤，见第8节） |
| `w2s_leader_gap_threshold` | 8分 | Core Leader Score 分差阈值 |
| `w2s_observation_window_days` | 7天 | 候选连续miss多少天后移出 |
| `w2s_divergence_health_threshold` | 50 | phase=4 细分阈值 |
| `w2s_auction_gap_min` | 3% | 竞价Gap超预期阈值 |
| `w2s_sector_gate_allowed` | NEW_START,EXPANDING,MAIN_UPTREND,HEALTHY_DIVERGENCE | 允许进入状态机的板块分类 |
| `w2s_regulatory_risk_cap` | HIGH,EXTREME | 达到此级别即BLOCK |
| `w2s_market_gate_blocked` | RED | 大盘闸门达到此颜色即BLOCK |

## 8. 已知局限（如实记录，不在产品里假装不存在）

1. **候选池"昨日成交额"条件未做本地二次校验**，直接信任东财 Prompt 自身的数值过滤——
   `StockDailySnapshot` 没有持久化逐日成交额，补上需要改动 K线重建管线
   （`screening_service.compute_window_stats`/`eastmoney_fetcher.KLineBar`），
   该管线里还有一个未彻底解决的历史 bug（见第9节），评估后判定这次改动的风险大于收益，
   Phase 1 明确跳过，写在 `w2s_candidate_service.py` 的函数注释里。
2. **竞价Gap无独立数据源**：本仓库没有单独捕获9:25集合竞价专属价格，`auction_gap` 用
   "当前报价相对昨收的涨跌幅"近似，正常情况下09:26定时任务运行时两者基本重合，但用户若
   在盘中较晚时段首次手动刷新，这个值反映的是当时的实时涨跌幅而非严格的竞价价。
3. **Sector Momentum 首日无基准**：固定给50分，需要运行数天才能开始反映真实动量变化。

## 9. 遗留问题（非本次引入，跟进记录）

`daily_update.py` 的 K 线重建管线里有一个**尚未彻底解决**的 board_count 偶发滞后 bug
（曾在 001267 汇绿生态案例中复现：连板数展示滞后于实际情况），根因排查在另一次会话中
中途搁置。本次弱转强雷达的候选发现/龙头打分复用了同一批 `Stock` 字段
（`board_count_60d`/`limit_up_days_20d`），**理论上会受到相同 bug 影响**，但概率与既有
强势股池/涨跌停池一致，不是雷达新引入的问题，未来排查该 bug 时一并验证雷达是否修复。

---

## 10. Phase 2 / Phase 3 待办跟踪

> 供后续会话直接续接，每项标注了依赖前提。

### Phase 2
- [x] **Market Gate**（2026-08-22 上线）：Market Trend Score（核心指数趋势加权）+
      Risk Appetite Score（涨跌家数/涨跌停比/两融余额变化）→ GREEN/YELLOW/ORANGE/RED，
      默认仅 RED 触发状态机 BLOCK（`w2s_market_gate_blocked` 可配置）。
      新文件 `w2s_market_gate_service.py`，新增 `GET /market-gate`，
      `formula_version` 升至 `w2s_radar_v0.2.0`。
- [ ] **Space Gate 降级判断**：`limit_room` 数值已算并展示，缺"空间不足时降级 WAIT/BLOCK"
      的判断逻辑。
- [ ] **完整监管风险 0-100 分**：当前只有 LOW/MEDIUM/HIGH/EXTREME 四档粗分类。
- [ ] **Stress R/R + 三层止损**：依赖 Space Gate 降级逻辑先落地。
- [ ] 候选池成交额条件本地二次校验（依赖 K线重建管线扩展，见第8节局限1）。

### Phase 3（依赖分钟级数据，本仓库当前无该数据源，需先起数据链路）
- [ ] 日内获利盘估算（明确标注"估算"而非真实筹码）
- [ ] T+1 供给风险
- [ ] Leadership Impact V0.1（板块带动性）
- [ ] 回测框架

### 持续优化（不分 Phase，随时可做的小改进）
- [ ] 前端候选表格补充列排序（参考 `TurnoverOverview.tsx` 的 `SortTh` 模式）
- [ ] `/refresh` 耗时超过10秒目标时的告警/降级策略
- [ ] Checklist 详情文案根据具体数值给出更细粒度的解释（目前偏摘要）
