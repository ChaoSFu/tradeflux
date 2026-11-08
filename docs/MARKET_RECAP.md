# 每日复盘（Daily Market Recap）

> 2026-11-08 上线 Phase 1。页面 `/market-recap`（侧边栏：主页 → **每日复盘** → 大盘趋势，要登录）。
> 代码：`backend/app/services/market_recap_service.py`（市场事实）、`market_recap_store.py`（人工数据、锁定、验证）、
> `backend/app/routers/market_recap.py`、`backend/app/models/market_recap.py`、`frontend/src/pages/MarketRecap.tsx`。
> 原 `/review`（旧版日复盘）保留。

**定位**：市场事实 → 结构判断 → 核心识别 → 关联研究 → 条件预案 → 次日验证 → 认知改进。
系统给事实，判断和预案是人的；不打分、不预测、不出买卖信号。

## 一、三类数据分开放

| 类 | 存哪 | 能不能改 |
|---|---|---|
| 市场事实 | **不存**。每次按日期从原有各表现算（`GET /market-recap/context`） | — |
| 人工研究 | `market_recaps`（判断、情景、反思）、`recap_watch_stocks`（观察池）、`leader_relations`（关联知识）、`leader_response_daily`（每日响应）、`next_day_plans`（预案草稿） | 草稿随时改 |
| 锁定预案 | `recap_locked_versions` | **只插不改**。改了草稿再锁是新的一版 |

全部按登录用户名（owner）隔离，**接口全部后端鉴权**，不只靠前端 RequireLogin。

## 二、市场事实：按指定交易日取

复盘 D 这天，每个模块只读 D（或 D 及以前的序列），并带 `{status, trade_date, source, note}`：
`ok` 完整、`partial` 部分缺失、`stale` 不是这一天的、`missing` 没有、`error` 这一块查挂了（其余照常，
Postgres 事务会回滚，不连累后面的模块）。

| 模块 | 来源 |
|---|---|
| 指数（上证、深成、创业板、科创50） | `index_daily_snapshots` ≤ D：MA5/10/20/60、均线排列、MA20/60 方向（对比 5 日前）、5/20 日涨幅 |
| 涨跌广度 | `market_breadth_daily` + 涨停 / 炸板明细（封板率 = 涨停 ÷（涨停 + 炸板），明细 0 行 = 没抓到，不是 0） |
| 成交 | 两市成交额、较前日、较近 60 日均额。官方值次日才公布，当天用盘中最后快照并标明 |
| 短线接力 | `market_effect_service.compute_cohort_outcome`（昨日涨停 / 首板 / 连板 / 炸板今日表现，带有效样本数）+ `limit_moves_analysis_service.compute_advance_ladder`（晋级率） |
| 板块 | `sector_mainline_service.get_sector_mainline_state(as_of=D)`；状态基准日 ≠ D 时标 stale |
| 核心候选 | 连板 / N天M板（不连续的那种，跟连板分开）/ 生命周期（直接调 `/leader-cycle` 那个接口函数）/ 成交额前列，各组按上限取（8/5/6/5），**不打分** |

- 未收盘的日子不出复盘；不在交易日历里的日子不出复盘。
- 候选股的板块优先用当天口径（涨停明细的东财行业 → 当天全市场快照的行业），只剩股票表上的
  「当前主板块」时标 `*`。
- 股票日快照的 `pct_change_20d` 是非空列默认 0.0，0 当「不知道」，不显示成 0%。

## 三、观察池与关联

- **观察池跨日保留**，直到主动取消；取消只写 `removed_date`，之前那些天照样查得到。★ = 要建深入关联图谱的。
- **关联知识**（高标 → 属性 → 关联股）跨日继承。依据性质由属性类型决定，不让人手填：
  A 行业及产业链 / 事件催化；B 共享概念 / 历史联动 / 情绪结构；**C 名称联想**。名称联想永远是 C。
  A/B/C 只表示依据性质，不是机会等级。
- **每日响应**单独记，昨天的联动不算今天成立。领先 / 同步 / 滞后**只有两边当天都有首封时间**才允许记
  （首封时间一起存下来当证据）；没有分时证据只能是「未见明显响应 / 无法判断 / 待验证」。
- 关联建议只是建议，采纳了才进表：名称里相同的两个字（客观字符串匹配）、同概念且当天也涨停（板块关联）。

## 四、预案与锁定

- 一天最多 5 只候选；**入场触发和失效条件都必须写**；可以锁「空仓预案」。
- 「进入买入检查」带上代码、止损价、仓位上限跳到 `/pre-trade-check`。预案本身不是买入许可。
- 锁定 = 把当时的判断、观察池、关联（含当天响应）、预案、候选股锁定时的价格事实、各模块数据状态
  整份快照进 `recap_locked_versions`。
- **盘前 / 事后**：锁定时间晚于目标交易日 09:30 的版本标「事后记录」，不能冒充盘前判断。
  目标交易日在锁定时可能还不在交易日历里（日历只到今天），验证时按日历重新确定。

## 五、次日验证

`GET /market-recap/{D}/verification`：用 D 这天**最后一份盘前锁定版本**（没有就用最新一版并标事后），
对照下一交易日的收盘事实：每只候选的次日涨跌、收盘、是否涨停 / 首封、收盘是否跌破计划止损价，
以及当时的姿态和反证条件。入场有没有触发要人对照分时判断，系统不替你补判。草稿不参与验证。
页面上：打开 D 时顶部显示「上一交易日锁定的预案」，⑤ 区显示「这一天预案的次日验证」。

## 六、接口

| 接口 | 用途 |
|---|---|
| `GET /market-recap/dates` | 能复盘的交易日 + 我每天的草稿 / 锁定情况 |
| `GET /market-recap/context?date=` | 当天市场事实 |
| `GET / PUT /market-recap/{date}` | 读 / 存草稿（含预案） |
| `POST /market-recap/watch`、`DELETE /market-recap/watch/{code}?date=` | 观察池 |
| `GET /market-recap/leader-relations/{code}?date=` | 关联知识 + 关联股当天事实与响应 + 建议 |
| `PUT /market-recap/leader-relations`、`DELETE /market-recap/leader-relations/{id}` | 关联增改 / 软删 |
| `PUT /market-recap/responses` | 当天响应 |
| `POST /market-recap/{date}/lock`、`GET /market-recap/{date}/locked` | 锁定 / 历史版本 |
| `GET /market-recap/{date}/verification` | 次日验证 |

## 七、还没做（Phase 2/3）

分时触发时间与联动时滞、盘前候选响应率、预案自动事实核验（入场是否触发）、与交易日志 / 买入检查的
偏差统计、AI 辅助研究。偏差记录（`deviations`）字段已留，界面目前只收发现 / 错误 / 假设三项。
