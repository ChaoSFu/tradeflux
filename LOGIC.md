# TradeFlux · 短线晴雨表 — 项目逻辑梳理

> 本文档梳理项目的完整逻辑架构，供开发者快速理解系统设计与数据流转。
> 最后更新：2026-05-31

---

## 目录

1. [产品定位](#1-产品定位)
2. [整体架构](#2-整体架构)
3. [数据库设计](#3-数据库设计)
4. [六大核心引擎](#4-六大核心引擎)
   - 4.1 真实数据抓取层
   - 4.2 强势股筛选引擎
   - 4.3 板块生命周期引擎
   - 4.4 龙头识别引擎
   - 4.5 弱转强引擎
   - 4.6 市场状态引擎
5. [AI服务层](#5-ai服务层)
6. [API接口设计](#6-api接口设计)
7. [后台管理与运维](#7-后台管理与运维)
8. [前端设计](#8-前端设计)
9. [数据流全链路](#9-数据流全链路)
10. [启动与运行](#10-启动与运行)
11. [未来扩展方向](#11-未来扩展方向)

---

## 1. 产品定位

### 核心理念

TradeFlux **不是**选股系统，**不是**预测涨跌的工具。

它是一个**市场情绪与板块状态的观测平台**，帮助投资者：

- 感知当前市场所处的情绪周期
- 识别强势板块的生命阶段
- 跟踪龙头股的领涨逻辑
- 发现弱转强的潜在机会
- 管理仓位风险

> **市场是被观察的，不是被预测的。**

### 明确的非功能边界

| 不做 | 替代方案 |
|------|---------|
| 自动交易 | 人工决策辅助 |
| 投资建议 | 市场状态描述 |
| 盈利保证 | 风险等级标注 |
| 券商接口 | 手动同步数据 |

---

## 2. 整体架构

```
tradeflux/
│
├── backend/                    后端服务（Python / FastAPI）
│   ├── app/
│   │   ├── main.py             应用入口，挂载路由，启动时初始化DB
│   │   ├── config.py           配置（pydantic-settings，读.env）
│   │   ├── database.py         SQLAlchemy引擎、Session工厂、init_db()
│   │   │
│   │   ├── models/             数据库ORM表定义（SQLAlchemy 2.x）
│   │   │   ├── stock.py        股票 + 股票日快照
│   │   │   ├── sector.py       板块 + 板块日快照 + 股票-板块关系
│   │   │   ├── signal.py       弱转强信号
│   │   │   ├── review.py       日复盘
│   │   │   └── screening.py    ★ 筛选条件配置表（可持久化更改入池条件）
│   │   │
│   │   ├── schemas/            请求/响应Pydantic类型（v2）
│   │   │   ├── stock.py
│   │   │   ├── sector.py       含PHASE_LABELS / PHASE_LABELS_ZH常量
│   │   │   ├── signal.py
│   │   │   ├── review.py
│   │   │   └── market_state.py 市场综合状态、龙头、弱转强候选
│   │   │
│   │   ├── routers/            FastAPI路由（每模块一个文件）
│   │   │   ├── stocks.py
│   │   │   ├── sectors.py
│   │   │   ├── signals.py
│   │   │   ├── reviews.py
│   │   │   ├── market_state.py
│   │   │   ├── screening.py    ★ 筛选条件管理接口
│   │   │   └── admin.py        ★ 后台管理：触发更新、板块同步、板块可见性
│   │   │
│   │   └── services/           核心业务引擎（最重要的层）
│   │       ├── eastmoney_fetcher.py  ★ 真实行情抓取（东方财富 + AkShare + 新浪备用）
│   │       ├── screening_service.py  ★ 动态筛选条件计算引擎（K线→窗口统计→入池判断）
│   │       ├── strong_stock_service.py   强势股池查询（DB层）
│   │       ├── sector_phase_service.py   板块生命周期
│   │       ├── sector_top_stocks_service.py ★ 板块龙头股识别（配合daily_update使用）
│   │       ├── dragon_leader_service.py  龙头识别（DB层）
│   │       ├── weak_to_strong_service.py 弱转强信号
│   │       ├── market_state_service.py   市场状态综合
│   │       └── ai_service.py             AI叙事层（当前规则驱动）
│   │
│   └── scripts/                运维脚本（每日数据更新流程）
│       ├── daily_update.py     ★ 每日核心更新（抓行情→算指标→写DB→刷板块）
│       ├── sync_boards.py      ★ 东方财富板块全量同步（概念/行业/地区）
│       ├── seed_mock_data.py   模拟数据生成（开发调试用）
│       ├── init_screening.py   初始化默认筛选条件
│       ├── import_xuangu.py    从选股文件导入股票数据
│       └── backfill_daily_reviews.py  历史复盘数据补录
│
└── frontend/                   前端（React 18 / TypeScript / Vite）
    └── src/
        ├── api/                axios封装，对应后端各模块
        ├── components/         UI组件（layout / ui / charts / common）
        ├── pages/              12个主页面（含涨跌停分析、板块管理）
        ├── store/              Zustand全局状态
        ├── types/              完整TypeScript类型定义
        └── utils/              格式化工具（颜色、标签、枚举映射）
```

### 技术栈

| 层次 | 技术 |
|------|------|
| 后端框架 | FastAPI 0.115 |
| ORM | SQLAlchemy 2.0（声明式） |
| 数据库 | **PostgreSQL**（默认；修改 DATABASE_URL 可用 SQLite 开发） |
| 数据校验 | Pydantic v2 |
| 数据抓取 | httpx（东方财富/腾讯财经）+ AkShare（备用） |
| 前端框架 | React 18 + TypeScript |
| 构建工具 | Vite 5 |
| 样式 | TailwindCSS（深色终端主题） |
| 图表 | Recharts |
| 状态管理 | Zustand |
| 数据请求 | TanStack React Query v5 + axios |

---

## 3. 数据库设计

### 表关系总览

```
stocks ──────────────┬── stock_daily_snapshots  (1:N)
                     └── stock_sector_relations  (N:M 桥接表)
                                │
sectors ─────────────┬── stock_sector_relations  (1:N)
                     └── sector_daily_snapshots   (1:N)
                         (leader_stock_id → stocks)
                         is_watched: bool          ← 用户可在管理页开启/关闭
                         sector_type: concept/industry/region

stocks ──────────────── signals  (1:N, nullable)
sectors ─────────────── signals  (1:N, nullable)

daily_reviews          （独立表，无外键关联，按日期聚合市场状态）

screening_criteria     （★ 新增：可配置的入池筛选条件，is_active=True 者生效）
```

### 核心表字段说明

#### `stocks` — 股票主表

| 字段 | 类型 | 说明 |
|------|------|------|
| code | String | 股票代码（唯一索引） |
| market | String | SH / SZ |
| is_st | Boolean | 是否ST（ST股不进强势股池） |
| is_new_stock | Boolean | 是否次新股（新股不进强势股池） |
| in_strong_pool | Boolean | **是否在强势股池**（由每日更新脚本计算写入） |
| phase | String | 当前阶段（normal / weakening / broken） |
| leader_score | Float | 龙头得分 0–100 |
| risk_score | Float | 风险得分 0–100 |
| emotion_score | Float | 情绪得分 0–100 |
| board_count_60d | Int | 60日最高连板数 |
| limit_up_days_60d | Int | 60日涨停天数 |
| limit_up_days_10d | Int | 10日涨停天数 |
| top_10_pct_change_20d | Boolean | 20日涨幅是否进入全市场前10% |

> `Stock` 字段是**缓存态**，由 `daily_update.py` 夜间重算写入，日内查询直接读取，无需实时计算。

#### `stock_daily_snapshots` — 股票日快照

每只股票每个交易日一条记录，保存完整的当日行情与统计数据：

| 字段组 | 包含 |
|--------|------|
| OHLCV | open/close/high/low_price, volume, turnover_rate |
| 日标志 | is_limit_up, is_limit_down, is_broken_board（炸板）|
| 滚动统计 | board_count（当日连板），board_count_60d, limit_up_days_60d/10d |
| 分析得分 | phase, leader_score, risk_score, emotion_score |
| 弱转强 | is_weak_to_strong |

#### `sectors` — 板块主表

| 字段 | 说明 |
|------|------|
| sector_type | 板块类型：`concept`（概念）/ `industry`（行业）/ `region`（地区） |
| is_watched | 是否纳入分析（默认False，用户在管理页手动开启） |
| phase | Int 0–6，当前生命周期阶段 |
| strong_stock_count | 板块内强势股数量 |
| limit_up_count | 今日涨停数 |
| board_height | 板块内最高连板数（板高） |
| continuity_score | 连续性得分 |
| emotion_score | 板块情绪得分 |
| risk_score | 板块风险得分 |
| amount | 今日成交额（亿元） |
| pct_change_30d | 今日涨幅 %（字段名历史原因，实为today） |
| pct_change_5d/10d/20d/60d | 近5/10/20/60日累计涨幅 % |
| leader_stock_id | 外键 → stocks.id（龙头股） |

#### `stock_sector_relations` — 股票-板块关系

一只股票可属于一个或多个板块，关系表标注角色：

- `is_leader`：**龙头股**（板块最强，引领趋势）
- `is_core`：**核心股**（强势跟随，板块骨干）
- `is_compensation`：**补涨股**（滞涨个股，后续补涨逻辑）

#### `screening_criteria` — 筛选条件配置表（★ 新增）

持久化存储强势股入池条件，`is_active=True` 的那一条为当前生效条件：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| include_sh_main | True | 纳入沪主板 |
| include_sz_main | True | 纳入深主板 |
| exclude_st | True | 排除 ST / *ST |
| exclude_new_stock | True | 排除次新股 |
| new_stock_months | 12 | 上市不足 X 月视为次新 |
| min_board_count_60d | 3 | 60日最高连板数 > X |
| min_limit_up_days_60d | 9 | 60日涨停天数 > X |
| min_limit_up_days_10d | 4 | 10日涨停天数 > X |
| top_pct_rank_20d | 10 | 20日涨幅进入全市场前 X% |

> 入池条件之间是 **OR 关系**（满足任一即入池）；静态过滤（ST/次新）是 **AND 关系**。

#### `signals` — 弱转强信号

| 字段 | 说明 |
|------|------|
| signal_type | 信号类型（见4.5节） |
| confidence_score | 置信度 0–100 |
| risk_level | low / medium / high |
| suggested_action | 操作建议 |
| explanation | 中文解释文本（可被AI替换） |

---

## 4. 六大核心引擎

### 4.1 真实数据抓取层

**文件**：`services/eastmoney_fetcher.py`

从公开行情接口抓取 A 股实时数据，提供两个主要能力：

#### 股票列表抓取（`fetch_main_board_stocks`）

```
主力：AkShare（交易所官方列表 + 新浪财经涨跌幅）
  → 约 4500 只，耗时约 30s，含上市日期（用于次新判断）

备用：东方财富 clist 接口
  → 速度快但受 TLS 指纹限流，数据可能不完整

覆盖范围：沪主板 + 科创板(688) + 深主板 + 创业板(300/301)
排除：北交所(8xxxxx)
```

#### K 线抓取（`fetch_kline` / `fetch_klines_batch`）

```
主力：东方财富历史 K 线接口（含换手率）
备用：腾讯财经历史 K 线接口（无换手率，影响评分精度但不影响涨跌停判断）

并发批量抓取：max_workers=5（保守，避免封锁）
默认拉取 65 日（保证计算 60 日指标有 5 日冗余）
```

#### 涨跌停判断

```
主板（600/601/603/605, 000/001/002/003）：±10%（ST ±5%）
科创板（688）、创业板（300/301）：±20%
炸板：当日最高价触及涨停价，但收盘未封板
判断方法：用实际价格反推前收盘价，再精确计算涨停价，比单纯涨幅阈值更准确
```

---

### 4.2 强势股筛选引擎

**文件**：`services/screening_service.py`

从 K 线序列计算完整的窗口统计指标，并与 `ScreeningCriteria` 对比判断是否入池。

#### 窗口统计指标（`StockWindowStats`）

| 指标组 | 包含 |
|--------|------|
| 连板统计 | 当前连续涨停数、近60日最高连板数、近60日最高连跌停数 |
| 涨停天数 | 近60/20/10日涨停天数 |
| 累计涨幅 | 近60/20/10日累计涨幅 |
| 今日数据 | 今日涨跌幅、换手率、是否涨/跌停、是否炸板 |
| 均线 | ma60、ma30（收盘价均值） |
| 阶段分类 | `normal`（正常）/ `weakening`（走弱）/ `broken`（破位） |

#### 情绪分公式

```
emotion =
    涨停天数_60d × 1.2
  + 最高连板 × 5.0
  + (今日涨停 ? 12 : 0)
  + 换手率趋势扩张因子（近5日 vs 近20日均值，0–15分）★
  + 今日换手率 × 0.8
```

> ★ 量能扩张信号：区分"量能放大"与"缩量维持"，解耦了情绪分与龙头分。

#### 风险分公式

```
risk =
    近3日炸板数 × 28.0    （高危）
  + 3-10日炸板数 × 12.0   （中危）
  + max(0, 最高连板-4) × 8.0  （高板位分歧风险）
  + (今日跌停 ? 15 : 0)
  + min(30, 连续下跌天数 × 8.0) ★
```

> ★ 连续下跌是走弱最直接信号。移除了"活跃度不足"因子（刚启动的票会被误判高风险）。

#### 龙头分公式

| 因子 | 满分 | 说明 |
|------|------|------|
| 当前连板数 | 30 | 正在进行的涨停序列 |
| 近10日涨停密度 | 30 | 最近两周活跃度 |
| 情绪归一化 | 20 | 综合热度 |
| 历史板高 | 12 | 60日最高板数 |
| 60日涨停密度 | 8 | 持续活跃度 |
| 今日涨停加分 | +5 | 状态奖励 |
| 今日换手加分 | +5 | 量能配合 |
| 板块龙头加成 | +12 | 板块内 is_leader 标记 ★ |
| 炸板惩罚 | -12 | 信号破坏 |

---

### 4.3 板块生命周期引擎

**文件**：`services/sector_phase_service.py`

#### 七阶段模型

```
阶段 0 — 隐匿期（Stealth）
  条件：strong_stock_count ≤ 1，无涨停，情绪低
  特征：没有明显活动，少数个股悄悄布局

阶段 1 — 启动期（Initiation）
  条件：strong_stock_count ≥ 2，limit_up_count ≥ 1
  特征：出现首批涨停，板块开始被关注

阶段 2 — 扩张期（Expansion）
  条件：strong_stock_count ≥ 4，board_height ≥ 3，continuity_score ≥ 50
  特征：多只强股轮番涨停，连板高度上升

阶段 3 — 高潮期（Euphoria）
  条件：board_height ≥ 5，limit_up_count ≥ 3，emotion_score ≥ 70
  特征：情绪亢奋，龙头连板高度显著，资金集中

阶段 4 — 分歧期（Divergence）
  条件：board_height ≤ 2，emotion_score < 40，strong_stock_count ≥ 2
  特征：出现炸板，分歧加大，板块分化

阶段 5 — 衰退期（Decline）
  条件：limit_up_count = 0，strong_stock_count ≤ 1
  特征：涨停绝迹，强势股减少，资金撤离

阶段 6 — 死亡区（Dead Zone）
  条件：risk_score ≥ 80，limit_up_count = 0
  特征：板块彻底熄火，高风险，不参与
```

#### 判断逻辑（优先级从高到低）

```python
def _classify_phase(strong_stock_count, limit_up_count, board_height,
                    continuity_score, risk_score, emotion_score) -> int:
    if risk_score >= 80 and limit_up_count == 0:  return 6  # 死亡区优先
    if limit_up_count == 0 and strong_stock_count <= 1: return 5
    if board_height <= 2 and emotion_score < 40:  return 4
    if board_height >= 5 and limit_up_count >= 3 and emotion_score >= 70: return 3
    if strong_stock_count >= 4 and board_height >= 3 and continuity_score >= 50: return 2
    if strong_stock_count >= 2 and limit_up_count >= 1: return 1
    return 0  # 默认隐匿期
```

---

### 4.4 龙头识别引擎

**文件**：`services/dragon_leader_service.py`

#### 龙头类型

| 类型 | 标识 | 识别条件 |
|------|------|---------|
| 情绪龙 | emotion | emotion_score ≥ 80 且 board_count_60d ≥ 5 |
| 总龙头 | overall | limit_up_days_60d ≥ 12 |
| 补涨龙 | compensation | 板块处于分歧/衰退期（phase 4/5）且 risk_score < 40 |
| 趋势龙 | trend | leader_score ≥ 70（其他条件不满足时） |
| 中盘核心 | mid_cap | 以上均不满足 |

对全体强势股评分后，取**前6名**作为当日龙头列表。

---

### 4.5 弱转强引擎

**文件**：`services/weak_to_strong_service.py`

扫描强势股池内每只股票的近期日快照，识别三类弱转强形态：

#### 形态一：炸板修复（broken_board_recovery）

```
近5日内出现过炸板 AND 最近1日涨幅 > 5%
置信度：min(90, 50 + 最近涨幅 × 4)
风险：medium  建议：watchlist（关注）
```

#### 形态二：分歧修复（divergence_repair）

```
所属板块处于分歧期（phase = 4）
AND 最新情绪得分 > 55
AND 个股风险得分 < 50
置信度：min(85, 40 + 情绪得分 × 0.5)
风险：medium  建议：observe（观察）
```

#### 形态三：反弹加速（rebound_acceleration）

```
近5日第3日涨幅 < -3%（回调）
AND 最近1日涨幅 > 3%
AND 最近1日涨幅 > 前1日涨幅（加速）
置信度：min(80, 45 + 最近涨幅 × 3)
风险：low  建议：low_position_trial（轻仓试探）
```

同一只股票只触发**最高优先级**形态，最多返回**前10只**候选股（置信度降序）。

---

### 4.6 市场状态引擎

**文件**：`services/market_state_service.py`

将所有板块数据综合为一个市场级别的状态快照。

#### 计算流程

```
1. 读取全部板块数据（仅 is_watched=True 的板块参与计算）
          ↓
2. 赚钱效应 = 扩张期/高潮期（phase 2/3）板块的平均情绪得分
          ↓
3. 亏钱效应 = 衰退期/死亡区（phase 5/6）板块的加权风险得分
          ↓
4. 情绪温度 = profit - loss × 0.5 + 30，范围 0–100
          ↓
5. 市场阶段：≥80→疯牛 ≥65→偏暖 ≥45→震荡 ≥30→谨慎 <30→熊市恐慌
          ↓
6. 建议仓位：疯牛70% 偏暖55% 震荡40% 谨慎25% 熊市10%
          ↓
7. 调用龙头引擎 + 弱转强引擎
          ↓
8. 组装 MarketStateResponse 返回
```

---

## 5. AI服务层

**文件**：`services/ai_service.py`

`AIReviewGenerator` 类提供四个方法，**当前全部为规则模板生成**：

| 方法 | 用途 |
|------|------|
| `generate_market_review()` | 根据市场阶段生成每日复盘文字 |
| `summarize_sector()` | 生成板块摘要描述 |
| `summarize_stock()` | 生成个股描述 |
| `explain_signal()` | 解释弱转强信号的中文说明 |

### 设计意图：LLM热插拔

```python
# 当前：规则模板
ai_generator = AIReviewGenerator()

# 未来：继承并替换为 Claude Sonnet，路由层代码不变
class ClaudeAIReviewGenerator(AIReviewGenerator):
    def generate_market_review(self, ...):
        # 调用 Anthropic claude-sonnet-4-6 API
        ...
```

---

## 6. API接口设计

所有接口前缀 `/api`，FastAPI自动生成 Swagger 文档（`/docs`）。

### 股票接口（`/api/stocks`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/stocks` | 全量股票列表（分页/筛选/搜索） |
| GET | `/api/stocks/strong-pool` | 强势股池（支持排序：龙头分/风险分/情绪分等） |
| GET | `/api/stocks/limit-moves` | 今日涨停/跌停列表（附板块归属） |
| GET | `/api/stocks/limit-moves/trend` | 近N日涨跌停趋势数据 |
| GET | `/api/stocks/{code}` | 个股详情（含板块信息） |
| GET | `/api/stocks/{code}/snapshots` | 个股近N日快照 |
| POST | `/api/stocks` | 新增股票 |
| PATCH | `/api/stocks/{code}` | 更新股票状态 |

### 板块接口（`/api/sectors`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sectors` | 全部板块（is_watched=True，按情绪得分降序） |
| GET | `/api/sectors/{code}` | 板块详情（含成分股列表） |
| GET | `/api/sectors/{code}/snapshots` | 板块近N日快照 |
| GET | `/api/sectors/ranking` | 板块排行榜（按赚钱效应/5日涨幅等多维度排序） |

### 信号接口（`/api/signals`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/signals` | 弱转强信号（可按类型/风险/股票/板块筛选） |

### 复盘接口（`/api/reviews`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/reviews` | 日复盘列表 |
| GET | `/api/reviews/latest` | 最新一条复盘 |
| GET | `/api/reviews/{date}` | 指定日期复盘 |
| POST | `/api/reviews` | 创建/更新复盘 |
| POST | `/api/reviews/generate-today` | 一键生成今日复盘 |

### 市场状态接口（`/api/market-state`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/market-state` | 当前市场综合状态 |
| GET | `/api/market-state/history` | 近N日情绪曲线历史 |

### 筛选条件接口（`/api/screening`）★ 新增

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/screening/criteria` | 所有筛选条件 |
| GET | `/api/screening/criteria/active` | 当前生效条件 |
| POST | `/api/screening/criteria` | 创建新条件（自动停用其他） |
| PUT | `/api/screening/criteria/{id}` | 更新条件 |

### 管理接口（`/api/admin`）★ 新增

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/admin/update` | 触发每日数据更新（后台线程，异步） |
| GET | `/api/admin/update/status` | 查询更新任务状态 |
| POST | `/api/admin/sync-boards` | 触发东财板块全量同步（约5-8分钟） |
| GET | `/api/admin/sync-boards/status` | 查询板块同步任务状态 |
| GET | `/api/admin/sectors` | 获取全部板块（含 is_watched 状态，供管理页使用） |
| PATCH | `/api/admin/sectors/{id}/watch` | 切换板块 is_watched 状态 |
| POST | `/api/admin/sectors/batch-watch` | 批量设置 is_watched |

---

## 7. 后台管理与运维

### 每日更新流程（`scripts/daily_update.py`）

```
1. fetch_main_board_stocks()          → AkShare列表 + 新浪涨跌幅（约30s）
2. 确定候选股：当前强势池 + 今日高涨幅（>7%）股
3. fetch_klines_batch(candidates)     → 并发拉取60日K线（max_workers=5）
4. compute_window_stats()             → 计算全部窗口统计指标
5. evaluate_criteria()                → 对比 ScreeningCriteria → in_strong_pool
6. 写入 StockDailySnapshot
7. refresh_sector_phases()            → 刷新板块阶段
8. 生成并写入 DailyReview
9. 输出统计摘要
```

用法：
```bash
cd backend
.venv/bin/python scripts/daily_update.py
.venv/bin/python scripts/daily_update.py --date 2026-05-26  # 补录指定日期
.venv/bin/python scripts/daily_update.py --skip-boards      # 跳过板块同步
```

### 板块同步（`scripts/sync_boards.py`）

从东方财富 WAP 接口全量同步板块数据：

| 数据源 | 板块类型 | 数量 |
|--------|---------|------|
| `m:90+e:3` | 概念板块 | ~399个 |
| `b:MK0881` | 行业板块全量（含一/二/三级） | ~457个 |
| `m:90+e:1` | 地区板块 | ~31个 |

> 新板块默认 `is_watched=False`，已有板块保留原有配置。约需 5-8 分钟。

也可通过管理页面触发：`POST /api/admin/sync-boards`

---

## 8. 前端设计

### 路由结构

```
/                   → 仪表盘 Dashboard
/stocks             → 强势股池（板块分组视图）SectorPool
/stocks/list        → 股票表格列表 StockPool
/stocks/:code       → 个股详情 StockDetail
/sectors            → 板块分析 SectorAnalysis
/sectors/ranking    → 板块排行榜 SectorRanking
/signals            → 弱转强信号 Signals
/review             → 日复盘 DailyReview
/limit-moves        → 涨跌停仪表盘 LimitMovesDashboard
/limit-moves/pool   → 今日涨停板池 LimitMovesPool
/limit-moves/sectors → 板块涨跌停分布 LimitMovesSectors
/admin/sectors      → 板块管理（is_watched配置）SectorConfig
```

### 12个页面逻辑

#### 仪表盘（Dashboard）

- 顶部横幅：市场阶段、情绪温度条、赚钱/亏钱效应、仓位建议
- 4格统计卡：活跃板块数、危险板块数、龙头数量、弱转强候选数
- 情绪曲线图（近30日）：赚钱效应/亏钱效应/情绪温度三条线
- 活跃板块列表、龙头股列表、弱转强候选

#### 强势股池（SectorPool）★ 重构为板块分组视图

- 按板块分组展示强势股（而非简单表格）
- 板块头部显示今日平均涨幅（赚钱效应）
- 股票卡片展示：阶段标签（normal/weakening/broken）+ 涨停/跌停/炸板图标
- 搜索框、多维度排序（涨停/连板/情绪等）

#### 股票列表（StockPool）

- 搜索框（按代码/名称）+ 板块筛选
- 可排序表格：龙头分/风险分/情绪分/60日最高板等

#### 个股详情（StockDetail）

- 代码、名称、徽章、板块、阶段标签
- 得分卡、统计格、价格走势图、得分趋势图、历史快照表

#### 板块分析（SectorAnalysis）

- 生命周期分布条形图（7阶段）
- 左侧板块卡片列表 + 右侧雷达图/成分股/趋势折线图

#### 板块排行榜（SectorRanking）★ 新增

- 按多维度（赚钱效应/5日涨幅/涨停数等）排序的板块排行
- 区分概念板块、行业板块、地区板块

#### 弱转强信号（Signals）

- 顶部免责声明 + 信号类型/风险等级过滤
- 信号卡片：类型徽章/置信度/风险/建议操作/中文解释/进度条

#### 日复盘（DailyReview）

- 右上角"生成今日复盘"按钮
- 左侧日期列表 + 右侧详情（市场阶段/赚亏/温度/仓位/强危板块/叙事文本）

#### 涨跌停仪表盘（LimitMovesDashboard）★ 新增

- 近期涨停/跌停趋势曲线（折线图）
- 板块集中度分析（饼图 + 排名列表 + 跨板块分析）

#### 今日涨停板池（LimitMovesPool）★ 新增

- 今日涨停、跌停、炸板股票列表
- 附板块归属、连板数、换手率等维度

#### 板块涨跌停分布（LimitMovesSectors）★ 新增

- 各板块今日涨停/跌停分布热力图或列表视图

#### 板块管理（SectorConfig）★ 新增

- 全量板块列表（概念/行业/地区分类）
- 切换 `is_watched` 控制哪些板块纳入分析
- 批量开启/关闭、按成交额/涨幅排序

### 全局状态（Zustand）

```typescript
interface AppState {
  sidebarCollapsed: boolean
  marketState: MarketState
  selectedSectorCode: string
}
```

### 数据请求（React Query）

- `staleTime: 30s`：30秒内不重复请求
- `retry: 1`：失败后重试1次
- `refetchOnWindowFocus: false`：切换窗口不自动刷新

---

## 9. 数据流全链路

### 查询请求路径

```
用户操作前端
    ↓
React Query 发起 axios GET /api/xxx
    ↓
Vite Dev Proxy（localhost:5173 → localhost:8000）
    ↓
FastAPI Router（routers/xxx.py）解析参数
    ↓
Service Layer（services/xxx.py）执行业务逻辑
    ↓
SQLAlchemy Session 查询 PostgreSQL
    ↓
Pydantic Schema 序列化响应
    ↓
JSON → React 渲染组件
```

### 每日数据更新路径（最核心的写入流程）

```
POST /api/admin/update
  OR
scripts/daily_update.py（定时任务/手动执行）
    ↓
fetch_main_board_stocks()        → 全市场 ~4500 只基础信息
    ↓
候选股筛选：强势池 + 今日高涨幅    → 约 500–800 只
    ↓
fetch_klines_batch(candidates)   → 并发拉取 60 日 K 线
    ↓
compute_window_stats()           → 每只股票的窗口统计指标
    ↓
evaluate_criteria()              → 与 ScreeningCriteria 对比 → in_strong_pool
    ↓
写入 StockDailySnapshot + 更新 Stock 缓存字段
    ↓
refresh_sector_phases()          → 更新各板块的 phase/统计/得分
    ↓
market_state_service → DailyReview  → 写入今日复盘快照
```

### 市场状态查询路径

```
GET /api/market-state
    ↓
market_state_service.get_current_market_state(db)
    ├── 查 is_watched=True 的板块
    ├── 赚钱效应/亏钱效应/情绪温度/市场阶段/建议仓位
    ├── dragon_leader_service.identify_dragon_leaders(db)  → Top6
    └── weak_to_strong_service.detect_weak_to_strong_candidates(db) → Top10
    ↓
MarketStateResponse 返回
```

---

## 10. 启动与运行

### 前置条件

```bash
# macOS（Homebrew）
brew install postgresql@16
brew services start postgresql@16
createdb tradeflux
```

### 首次启动

```bash
# 1. 后端依赖
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 初始化数据库表结构（FastAPI启动时自动执行，也可手动触发）
uvicorn app.main:app --reload --port 8000
# 初始化默认筛选条件
python scripts/init_screening.py

# 3. 选项A：注入模拟数据（开发调试）
python scripts/seed_mock_data.py

# 4. 选项B：抓取真实数据（生产）
#   先同步板块（约5-8分钟）
python scripts/sync_boards.py
#   然后在管理页开启关注的板块（/admin/sectors）
#   再执行每日更新
python scripts/daily_update.py

# 5. 前端
cd ../frontend
npm install
npm run dev
# 前端地址：http://localhost:5173
```

### 日常启动

```bash
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev
```

### 每日数据更新

**方式一**：管理页面触发（推荐）
- 访问 `http://localhost:5173/admin/sectors`
- 点击"触发今日更新"按钮，可实时查看日志

**方式二**：命令行
```bash
cd backend
.venv/bin/python scripts/daily_update.py
# 补录某日数据
.venv/bin/python scripts/daily_update.py --date 2026-05-26
```

**方式三**：API
```bash
curl -X POST http://localhost:8000/api/admin/update
curl http://localhost:8000/api/admin/update/status
```

### 环境变量（`backend/.env`）

```bash
DATABASE_URL=postgresql+psycopg://chao@localhost/tradeflux
# SQLite开发模式（无需PostgreSQL）：
# DATABASE_URL=sqlite:///./tradeflux.db
```

---

## 11. 未来扩展方向

### 数据层

- **历史数据回填**：支持 CSV/JSON 批量导入历史快照
- **AkShare数据丰化**：接入基本面数据（PE/PB/市值）用于筛选条件扩展
- **WebSocket 实时推送**：市场状态变化时主动推送前端

### 分析引擎

- **LLM叙事升级**：将 `ai_service.py` 的 `AIReviewGenerator` 后端替换为 Claude Sonnet，复盘质量大幅提升
- **更多弱转强形态**：量能异动形态、缩量企稳形态、涨停放量形态
- **龙头记忆系统**：追踪龙头切换路径，识别换手换龙逻辑
- **市场周期引擎**：跨周期视角，识别牛熊转换信号
- **情绪知识图谱**：板块间联动关系建模

### 系统层

- **夜间任务调度**：cron 自动执行 `daily_update.py`
- **策略市场**：允许用户自定义评分公式参数
- **移动端 PWA**：响应式点评界面

---

> ⚠️ **免责声明**：本平台仅为市场研究与认知辅助工具，不构成任何投资建议。
> 所有信号、得分、阶段判断均为辅助分析，最终投资决策由用户自行负责。
