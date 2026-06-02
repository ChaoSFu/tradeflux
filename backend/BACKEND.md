# TradeFlux Backend 技术文档

> 面向开发者的后端架构说明，覆盖目录结构、外部接口依赖、数据链路和数据库表结构。
> 最后更新：2026-06-02

---

## 目录

1. [目录结构](#1-目录结构)
2. [技术栈与依赖](#2-技术栈与依赖)
3. [外部接口依赖](#3-外部接口依赖)
4. [数据链路](#4-数据链路)
5. [数据库表结构](#5-数据库表结构)
6. [服务层说明](#6-服务层说明)
7. [路由层说明](#7-路由层说明)
8. [运维脚本说明](#8-运维脚本说明)
9. [配置与环境变量](#9-配置与环境变量)

---

## 1. 目录结构

```
backend/
├── app/
│   ├── main.py               应用入口：注册路由、CORS、lifespan 管理调度器
│   ├── scheduler.py          内置 APScheduler：每日 15:30~16:30 随机触发，失败重试 3 次
│   ├── config.py             全局配置（从 .env 读取，pydantic-settings）
│   ├── database.py           SQLAlchemy 引擎、SessionLocal、init_db()
│   │
│   ├── models/               ORM 表定义（SQLAlchemy 2.x 声明式）
│   │   ├── stock.py          stocks + stock_daily_snapshots
│   │   ├── sector.py         sectors + stock_sector_relations + sector_daily_snapshots
│   │   ├── signal.py         signals（弱转强信号）
│   │   ├── review.py         daily_reviews（每日复盘快照）
│   │   └── screening.py      screening_criteria（可配置入池条件）
│   │
│   ├── schemas/              Pydantic v2 请求/响应模型
│   │   ├── stock.py
│   │   ├── sector.py         含 PHASE_LABELS / PHASE_LABELS_ZH 常量
│   │   ├── signal.py
│   │   ├── review.py
│   │   └── market_state.py   市场综合状态、龙头、弱转强候选
│   │
│   ├── routers/              FastAPI 路由（每模块一个文件）
│   │   ├── stocks.py
│   │   ├── sectors.py
│   │   ├── signals.py
│   │   ├── reviews.py
│   │   ├── market_state.py
│   │   ├── screening.py      筛选条件 CRUD
│   │   └── admin.py          后台管理：触发更新/同步（含 meta_only 参数）、板块可见性、持久化状态
│   │
│   └── services/             核心业务逻辑层
│       ├── eastmoney_fetcher.py      外部行情抓取（主力 + 备用接口）
│       ├── screening_service.py      K线→窗口统计→入池判断
│       ├── strong_stock_service.py   强势股池 DB 查询层
│       ├── sector_phase_service.py   板块生命周期阶段判断
│       ├── sector_top_stocks_service.py  板块龙头计算
│       ├── dragon_leader_service.py  全市场龙头评分与识别
│       ├── weak_to_strong_service.py 弱转强信号检测
│       ├── market_state_service.py   市场状态综合计算
│       └── ai_service.py             AI叙事生成（当前为规则模板）
│
├── scripts/                  运维脚本（每日更新流程）
│   ├── daily_update.py       每日核心更新主流程
│   ├── sync_boards.py        东方财富板块全量同步（已重写，见§8）
│   ├── init_screening.py     初始化默认筛选条件
│   ├── seed_mock_data.py     注入模拟数据（开发调试）
│   ├── import_xuangu.py      从文件导入股票数据
│   ├── backfill_daily_reviews.py  历史复盘数据补录
│   ├── seed_kline_history.py      一次性补填历史 close_price 快照（K线DB重建初始化）
│   └── backfill_close_price.py   从已有快照反推 close_price（辅助工具）
│
├── requirements.txt
├── .env.example
└── BACKEND.md                本文档
```

---

## 2. 技术栈与依赖

### 核心依赖（`requirements.txt`）

| 包 | 版本 | 用途 |
|----|------|------|
| `fastapi` | 0.115.0 | Web 框架 |
| `apscheduler` | 3.10.4 | 内置调度器（每日自动更新） |
| `uvicorn[standard]` | 0.32.0 | ASGI 服务器 |
| `sqlalchemy` | 2.0.36 | ORM |
| `alembic` | 1.13.3 | 数据库迁移 |
| `pydantic` | 2.9.2 | 数据校验与序列化 |
| `pydantic-settings` | 2.6.1 | 环境变量配置 |
| `pydantic-core` | 2.23.4 | Pydantic 内核（需固定版本，Python 3.13 兼容） |
| `python-dotenv` | 1.0.1 | `.env` 文件读取 |
| `httpx` | 0.27.2 | 异步 HTTP 客户端（行情抓取） |
| `psycopg[binary]` | ≥3.3.0 | PostgreSQL 驱动（psycopg3） |
| `akshare` | ≥1.14.0 | A 股数据备用接口（交易所列表） |
| `python-multipart` | 0.0.12 | 文件上传支持 |

> **Python 版本要求**：3.13（`pydantic-core==2.23.4` 为当前 Python 3.13 可用的最新兼容版本）

---

## 3. 外部接口依赖

系统依赖三条外部数据源，按优先级降序排列：

### 3.1 东方财富（主力行情源）

| 接口 | URL | 用途 | 调用场景 |
|------|-----|------|---------|
| 股票列表 clist | `https://push2.eastmoney.com/api/qt/clist/get` | 获取全市场股票列表（涨跌幅、换手率） | `daily_update.py` 备用 |
| K 线接口 | `https://push2his.eastmoney.com/api/qt/stock/kline/get` | 获取单股日线 K 线（含换手率） | `fetch_kline()` 主力 |
| 板块列表（WAP） | `https://push2delay.eastmoney.com/api/qt/clist/get`（板块 fs 参数） | 获取概念/行业/地区板块元数据 | `sync_boards.py` 第1步 |
| F10 板块归属 | `https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax` | 查询个股所属板块 BK 码列表 | `fetch_stock_bk_codes()` |
| 智能选股 | `https://np-tjxg-g.eastmoney.com/api/smart-tag/stock/v3/pw/search-code` | 自然语言筛股，返回满足条件的代码集合 | `fetch_strong_pool_codes()` |

**请求频率限制**：东方财富对高频请求有 TLS 指纹检测，批量 K 线抓取 `max_workers=5`，请求间隔 ≥0.1s。

**Headers（必须带 Referer）**：
```
Referer: https://quote.eastmoney.com/
User-Agent: Mozilla/5.0 ...
```

### 3.2 AkShare（股票列表主力源）

| 方法 | 数据内容 | 调用场景 |
|------|---------|---------|
| `stock_info_sh_name_code("主板A股")` | 沪主板股票列表（含上市日期） | `fetch_main_board_stocks()` 主力 |
| `stock_info_sh_name_code("科创板")` | 科创板列表（含上市日期） | 同上 |
| `stock_info_sz_name_code("A股列表")` | 深市主板 + 创业板列表 | 同上 |

AkShare 数据来源于交易所官方公告，提供精确的**上市日期**（用于次新股判断），是股票列表的主力来源。约耗时 20–30s。

### 3.3 新浪财经（涨跌幅补充）

| 接口 | URL | 用途 |
|------|-----|------|
| 实时行情 | `https://hq.sinajs.cn/list=sh600000,sz000001,...` | 批量查询今日涨跌幅 |

与 AkShare 配合：AkShare 提供股票列表但不含今日涨跌幅，新浪接口批量补充（每批 150 只，约 5–10s）。

### 3.4 腾讯财经（K 线备用）

| 接口 | URL | 用途 |
|------|-----|------|
| K 线 | `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get` | 东方财富 K 线失败时备用 |

不含换手率，涨跌幅通过相邻收盘价推算，影响情绪/龙头评分精度但不影响涨跌停判断。

### 3.5 数据源切换策略

```
fetch_main_board_stocks():
  主力：AkShare列表 + 新浪涨跌幅  → 约 4500 只，含上市日期，约 30s
  备用：东方财富 clist             → 较快但数据可能不完整（<800条时自动切换）

fetch_kline(code):
  主力：东方财富历史 K 线          → 含换手率
  备用：腾讯财经历史 K 线          → 无换手率
```

---

## 4. 数据链路

### 4.1 每日更新链路（写入路径）

> 实测耗时（稳定后）：**50–90s**，主要瓶颈为 AkShare/新浪行情接口网络抖动（3–30s）和 K线拉取（24–80s）。
>
> **触发方式**：
> - `POST /api/admin/update`（UI 手动触发，后台线程）
> - `python scripts/daily_update.py`（命令行）
> - **内置调度器**（每个交易日 15:30 触发，jitter=3600 随机延迟，失败自动重试最多3次，间隔10分钟）
>
> **互斥机制**：所有触发方式共享 `/tmp/tradeflux_daily_update.lock` 文件锁，确保同一时刻只有一个实例运行。

```
┌─────────────────────────────────────────────────────────────────┐
│  触发方式：                                                       │
│    A) POST /api/admin/update（管理页面，后台线程异步执行）          │
│    B) python scripts/daily_update.py（命令行/cron）               │
└───────────────────┬─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 1  fetch_main_board_stocks()                      ~3–30s  │
│  数据源：AkShare（主） + 新浪财经涨跌幅                            │
│  输出：List[StockBasicInfo]（约 4500 只）                         │
└───────────────────┬─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 2  确定候选股 & 强势池                                ~3–4s │
│  ① 调用东财智能选股 API（fetch_strong_pool_codes）               │
│     keyWord："主板非ST;非退市;非新股次新;近60日最高连板>3 OR      │
│              60日涨停>9 OR 10日涨停>4 OR 20日涨幅前10"           │
│     → 返回强势股代码集合（约 60–80 只）                           │
│  ② API 失败时回退：读 DB 中 in_strong_pool=True 的股票           │
│  候选股 = 强势池代码 + 今日涨跌停 + 涨幅>7% 的股票（共 ~260 只）  │
└───────────────────┬─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 3  K 线数据获取（DB重建路径优化）                   ~24–80s │
│  分组：                                                           │
│    DB重建组（有≥60条历史快照）→ 从快照重建历史 KLineBar             │
│                                + API 只拉今日 2 根（days=2）      │
│                                  并发 max_workers=8              │
│    全量拉取组（新股/历史不足）→ API 拉完整 65 日（days=65）         │
│                                  并发 max_workers=5              │
│                                                                   │
│  ⚠️ 注意：db_group 拉 days=2（不是 days=1），因东财 API 单日      │
│  请求的涨跌幅字段 pct_change 固定返回 0，需前一日数据才能正确计算。  │
│  输出：Dict[code, List[KLineBar]]                                 │
└───────────────────┬─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 4  compute_window_stats(code, bars, ...)           ~0.5s  │
│  纯内存计算，输出 StockWindowStats                                 │
│    包含：连板统计、涨停天数、累计涨幅、情绪/风险/龙头评分、          │
│          阶段分类（normal/weakening/broken）、ma60/ma30            │
└───────────────────┬─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 5  入池判断                                                 │
│  in_pool = stats.code in strong_pool_codes（Step2 API结果集）    │
│  更新：Stock.in_strong_pool = True/False                          │
│  写入：StockDailySnapshot（含 close_price 字段，供次日DB重建使用）  │
└───────────────────┬─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 6  补全涨跌停板块关联（并发 F10，增量）               ~0s   │
│  对今日涨跌停但无板块关联的股票，并发调 F10 接口补建关联            │
│  已有关联时直接跳过（0.0s）                                        │
└───────────────────┬─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 7  refresh_sector_phases() + 更新主板块           ~1–2s   │
│  聚合板块内强势股统计 → 更新 sectors 表 → 写入 sector_daily_snaps │
└───────────────────┬─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 8  生成并写入 DailyReview + 弱转强信号              ~0.3s  │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 查询链路（读取路径）

```
前端 React Query
    │  axios GET /api/xxx
    ▼
Vite Dev Proxy（5173 → 8000）
    │
    ▼
FastAPI Router  →  Service Layer  →  SQLAlchemy Session  →  PostgreSQL
                                               │
                                     Pydantic Schema 序列化
                                               │
                                             JSON
```

> 查询路径不涉及任何外部接口调用，所有数据均从 PostgreSQL 读取（由每日更新脚本预计算写入）。

### 4.3 板块同步链路（两种模式）

板块同步已拆分为两种模式，满足不同频率需求：

#### 模式A：板块行情同步（meta_only=true，每日）

> 耗时：**~30s**，每日收盘后由内置调度器自动执行（跟随每日数据更新之后）

```
POST /api/admin/sync-boards?meta_only=true
  OR python scripts/sync_boards.py --meta-only
    │
    ▼
【第1步】板块元数据同步（~30s，跳过成份股数量）
    更新：名称/涨幅(今日/5日/10日/20日/60日)/市值/成交额/换手率
    跳过：成份股数量（stock_count）、个股关联
```

#### 模式B：板块全量同步（meta_only=false，每周）

> 耗时：**~5–8 分钟**，每周手动触发一次

```
POST /api/admin/sync-boards
  OR python scripts/sync_boards.py
    │
    ▼
【第1步】板块元数据同步（~30s）
    同上，更新名称/涨幅/市值等
    │
    ▼
【第1步补充】并发拉取成份股数量（~15s）
    对每个板块调用 fs=b:{bk_code}&pn=1&pz=1 接口
    从 data.total 获取真实成份股总数 → 更新 sectors.stock_count
    并发 max_workers=20，失败保持原值不覆盖
    │
    ▼
【第2步】个股→板块关联同步（~38s）
    目标股票：当日涨跌停 + 当日强势股池（约 200 只）
    → 并发调 F10 接口（max_workers=10）获取每只股票的 BK 码
    → 与 DB 现有关联对比，只有变化才写入（增量更新）
    → 只关联 is_watched=True 的板块

    设计原则：
    - 反向思路：个股→板块（而非传统的板块→遍历成分股）
    - 只维护前端实际展示的股票（涨跌停池 + 强势股池）
    - stock_count 不由关联表统计，避免部分数据污染全量数据
```

> **网络容错**：拉取板块列表时，每页最多重试 3 次（间隔 3/6/9s），SSL 超时等网络抖动不会导致数据丢失。

---

## 5. 数据库表结构

数据库：**PostgreSQL**（默认）  
ORM：**SQLAlchemy 2.0 声明式**  
驱动：**psycopg3**（`psycopg[binary]`）

---

### 5.1 `stocks` — 股票主表

> 缓存态，由 `daily_update.py` 每日写入，查询时无需实时计算。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INTEGER | PK | 主键 |
| `code` | VARCHAR(10) | UNIQUE, NOT NULL | 股票代码（如 `600000`） |
| `name` | VARCHAR(50) | NOT NULL | 股票名称 |
| `market` | VARCHAR(10) | NOT NULL, default='SH' | 市场：`SH` / `SZ` |
| `is_st` | BOOLEAN | NOT NULL | 是否 ST / *ST |
| `is_new_stock` | BOOLEAN | NOT NULL | 是否次新股 |
| `ipo_date` | DATE | nullable | 上市日期 |
| `in_strong_pool` | BOOLEAN | NOT NULL | 是否在强势股池（每日更新） |
| `phase` | VARCHAR(30) | nullable | 阶段：`normal` / `weakening` / `broken` |
| `leader_score` | FLOAT | NOT NULL, default=0 | 龙头得分 0–100 |
| `risk_score` | FLOAT | NOT NULL, default=0 | 风险得分 0–100 |
| `emotion_score` | FLOAT | NOT NULL, default=0 | 情绪得分 0–100 |
| `board_count_60d` | INTEGER | NOT NULL, default=0 | 近60日最高连涨停板数 |
| `board_down_count_60d` | INTEGER | NOT NULL, default=0 | 近60日最高连跌停数 |
| `limit_up_days_60d` | INTEGER | NOT NULL, default=0 | 近60日涨停天数 |
| `limit_up_days_20d` | INTEGER | NOT NULL, default=0 | 近20日涨停天数 |
| `limit_up_days_10d` | INTEGER | NOT NULL, default=0 | 近10日涨停天数 |
| `pct_change_60d` | FLOAT | NOT NULL, default=0 | 近60日累计涨幅 % |
| `pct_change_20d` | FLOAT | NOT NULL, default=0 | 近20日累计涨幅 % |
| `pct_change_10d` | FLOAT | NOT NULL, default=0 | 近10日累计涨幅 % |
| `top_10_pct_change_20d` | BOOLEAN | NOT NULL | 20日涨幅是否进入全市场前10% |
| `primary_sector_id` | INTEGER | FK→sectors.id, SET NULL | 主板块外键 |
| `primary_sector_name` | VARCHAR(100) | nullable | 主板块名称（冗余缓存，避免 JOIN） |
| `created_at` | TIMESTAMP | server_default=now() | 创建时间 |
| `updated_at` | TIMESTAMP | server_default=now(), onupdate=now() | 更新时间 |

**索引**：`code`（UNIQUE），`primary_sector_id`

---

### 5.2 `stock_daily_snapshots` — 股票日快照

> 每只股票每个交易日一条，存储 K 线计算后的结果。原始 OHLCV 行情不持久化（计算后丢弃）。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INTEGER | PK | 主键 |
| `stock_id` | INTEGER | FK→stocks.id, NOT NULL | 关联股票 |
| `date` | DATE | NOT NULL | 日期 |
| `close_price` | FLOAT | nullable | 当日收盘价（用于次日 K线DB重建计算 MA60/MA30） |
| `pct_change` | FLOAT | nullable | 当日涨跌幅 % |
| `turnover_rate` | FLOAT | nullable | 换手率 % |
| `is_limit_up` | BOOLEAN | NOT NULL | 当日涨停 |
| `is_limit_down` | BOOLEAN | NOT NULL | 当日跌停 |
| `is_broken_board` | BOOLEAN | NOT NULL | 当日炸板（曾触涨停价但未封板收盘） |
| `board_count` | INTEGER | NOT NULL, default=0 | 当前连续涨停数（截至当日） |
| `limit_down_count` | INTEGER | NOT NULL, default=0 | 当前连续跌停数 |
| `board_count_60d` | INTEGER | NOT NULL, default=0 | 近60日最高连板数 |
| `board_down_count_60d` | INTEGER | NOT NULL, default=0 | 近60日最高连跌停数 |
| `limit_up_days_60d` | INTEGER | NOT NULL, default=0 | 近60日涨停天数 |
| `limit_up_days_20d` | INTEGER | NOT NULL, default=0 | 近20日涨停天数 |
| `limit_up_days_10d` | INTEGER | NOT NULL, default=0 | 近10日涨停天数 |
| `pct_change_60d` | FLOAT | NOT NULL, default=0 | 近60日累计涨幅 % |
| `pct_change_20d` | FLOAT | NOT NULL, default=0 | 近20日累计涨幅 % |
| `pct_change_10d` | FLOAT | NOT NULL, default=0 | 近10日累计涨幅 % |
| `top_10_pct_change_20d` | BOOLEAN | NOT NULL | 20日涨幅进前10% |
| `phase` | VARCHAR(30) | nullable | 阶段（与 stocks.phase 同步） |
| `leader_score` | FLOAT | NOT NULL, default=0 | 龙头得分 |
| `risk_score` | FLOAT | NOT NULL, default=0 | 风险得分 |
| `emotion_score` | FLOAT | NOT NULL, default=0 | 情绪得分 |
| `is_weak_to_strong` | BOOLEAN | NOT NULL | 是否触发弱转强信号 |
| `created_at` | TIMESTAMP | server_default=now() | 写入时间 |

**索引**：`stock_id`，`date`

**唯一约束**：`uq_snapshot_stock_date(stock_id, date)` — 每只股票每天只能有一条快照

---

### 5.3 `sectors` — 板块主表

> 由 `sync_boards.py` 初始化，由 `daily_update.py` 每日更新阶段和统计数据。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INTEGER | PK | 主键 |
| `code` | VARCHAR(20) | UNIQUE, NOT NULL | 板块代码（东方财富板块 ID） |
| `name` | VARCHAR(100) | NOT NULL | 板块名称 |
| `description` | TEXT | nullable | 板块描述 |
| `sector_type` | VARCHAR(20) | nullable | `concept`（概念）/ `industry`（行业）/ `region`（地区） |
| `stock_count` | INTEGER | NOT NULL, default=0 | 板块成员股票数 |
| `total_market_cap` | FLOAT | NOT NULL, default=0 | 总市值（亿元） |
| `turnover_rate` | FLOAT | NOT NULL, default=0 | 换手率 % |
| `amount` | FLOAT | NOT NULL, default=0 | 今日成交额（亿元） |
| `pct_change_30d` | FLOAT | NOT NULL, default=0 | **今日**涨幅 %（字段名为历史遗留，存今日涨幅） |
| `pct_change_5d` | FLOAT | NOT NULL, default=0 | 近5日累计涨幅 % |
| `pct_change_10d` | FLOAT | NOT NULL, default=0 | 近10日累计涨幅 % |
| `pct_change_20d` | FLOAT | NOT NULL, default=0 | 近20日累计涨幅 % |
| `pct_change_60d` | FLOAT | NOT NULL, default=0 | 近60日累计涨幅 % |
| `is_watched` | BOOLEAN | NOT NULL, default=False | 是否纳入分析（用户在管理页配置） |
| `phase` | INTEGER | NOT NULL, default=0 | 生命周期阶段 0–6 |
| `strong_stock_count` | INTEGER | NOT NULL, default=0 | 板块内强势股数量 |
| `limit_up_count` | INTEGER | NOT NULL, default=0 | 今日涨停数 |
| `limit_down_count` | INTEGER | NOT NULL, default=0 | 今日跌停数 |
| `board_height` | INTEGER | NOT NULL, default=0 | 板块内最高连板数（板高） |
| `continuity_score` | FLOAT | NOT NULL, default=0 | 连续性得分 0–100 |
| `risk_score` | FLOAT | NOT NULL, default=0 | 板块风险得分 0–100 |
| `emotion_score` | FLOAT | NOT NULL, default=0 | 板块情绪得分 0–100 |
| `leader_stock_id` | INTEGER | FK→stocks.id, nullable | 龙头股 |
| `created_at` | TIMESTAMP | server_default=now() | 创建时间 |
| `updated_at` | TIMESTAMP | server_default=now(), onupdate=now() | 更新时间 |

**索引**：`code`（UNIQUE）

> ⚠️ `pct_change_30d` 字段名为历史遗留问题，实际存储的是**今日涨幅**，读取时注意。

---

### 5.4 `stock_sector_relations` — 股票-板块多对多关系

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INTEGER | PK | 主键 |
| `stock_id` | INTEGER | FK→stocks.id, NOT NULL | 股票 |
| `sector_id` | INTEGER | FK→sectors.id, NOT NULL | 板块 |
| `is_leader` | BOOLEAN | NOT NULL, default=False | 是否板块龙头 |
| `is_core` | BOOLEAN | NOT NULL, default=False | 是否板块核心股 |
| `is_compensation` | BOOLEAN | NOT NULL, default=False | 是否补涨股 |
| `created_at` | TIMESTAMP | server_default=now() | 创建时间 |

**索引**：`stock_id`，`sector_id`

**唯一约束**：`uq_stock_sector(stock_id, sector_id)` — 防止重复插入同一股票-板块关联

---

### 5.5 `sector_daily_snapshots` — 板块日快照

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INTEGER | PK | 主键 |
| `sector_id` | INTEGER | FK→sectors.id, NOT NULL | 关联板块 |
| `date` | DATE | NOT NULL | 日期 |
| `phase` | INTEGER | NOT NULL, default=0 | 当日阶段 0–6 |
| `strong_stock_count` | INTEGER | NOT NULL, default=0 | 当日强势股数量 |
| `limit_up_count` | INTEGER | NOT NULL, default=0 | 当日涨停数 |
| `board_height` | INTEGER | NOT NULL, default=0 | 当日板高 |
| `continuity_score` | FLOAT | NOT NULL, default=0 | 连续性得分 |
| `risk_score` | FLOAT | NOT NULL, default=0 | 风险得分 |
| `emotion_score` | FLOAT | NOT NULL, default=0 | 情绪得分 |
| `leader_stock_id` | INTEGER | FK→stocks.id, nullable | 当日龙头 |
| `created_at` | TIMESTAMP | server_default=now() | 写入时间 |

**索引**：`sector_id`，`date`

---

### 5.6 `signals` — 弱转强信号

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INTEGER | PK | 主键 |
| `stock_id` | INTEGER | FK→stocks.id, nullable | 关联股票（可为空） |
| `sector_id` | INTEGER | FK→sectors.id, nullable | 关联板块（可为空） |
| `date` | DATE | NOT NULL | 信号日期 |
| `signal_type` | VARCHAR(50) | NOT NULL | 信号类型（见下表） |
| `confidence_score` | FLOAT | NOT NULL, default=0 | 置信度 0–100 |
| `risk_level` | VARCHAR(20) | NOT NULL, default='medium' | `low` / `medium` / `high` |
| `explanation` | TEXT | nullable | 中文解释文本 |
| `suggested_action` | VARCHAR(30) | NOT NULL | 操作建议（见下表） |
| `is_active` | BOOLEAN | NOT NULL | 是否有效 |
| `is_triggered` | BOOLEAN | NOT NULL | 是否已触发 |
| `created_at` | TIMESTAMP | server_default=now() | 创建时间 |
| `updated_at` | TIMESTAMP | onupdate=now() | 更新时间 |

**`signal_type` 枚举值**：

| 值 | 含义 |
|----|------|
| `broken_board_recovery` | 炸板修复（近5日炸板后反弹>5%） |
| `divergence_repair` | 分歧修复（板块分歧期但个股情绪回暖） |
| `rebound_acceleration` | 反弹加速（回调后动能加速恢复） |
| `sector_repair` | 板块修复 |
| `emotional_recovery` | 情绪恢复 |
| `dragon_leader_change` | 龙头更迭 |

**`suggested_action` 枚举值**：

| 值 | 中文 | 含义 |
|----|------|------|
| `observe` | 观察 | 仅监控，不参与 |
| `watchlist` | 关注 | 加入自选，等待确认 |
| `low_position_trial` | 轻仓试探 | 小仓位试仓 |
| `hold` | 持有 | 维持现有仓位 |
| `reduce` | 减仓 | 降低风险敞口 |
| `avoid` | 回避 | 不介入 |

**索引**：`stock_id`，`sector_id`，`date`

---

### 5.7 `daily_reviews` — 每日复盘快照

> 每日一条，date 唯一。存储市场状态的聚合快照，用于历史情绪曲线查询。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INTEGER | PK | 主键 |
| `date` | DATE | UNIQUE, NOT NULL | 日期 |
| `market_phase` | VARCHAR(50) | nullable | 市场阶段（如 `warm` / `neutral`） |
| `profit_effect_score` | FLOAT | NOT NULL, default=0 | 赚钱效应评分 0–100（合成值） |
| `loss_effect_score` | FLOAT | NOT NULL, default=0 | 亏钱效应评分 0–100（合成值） |
| `strong_pool_avg_pct` | FLOAT | nullable | 强势股池当日均涨幅 %（真实值） |
| `emotion_cycle` | VARCHAR(50) | nullable | 情绪周期标签（如"情绪回暖"） |
| `emotional_temperature` | FLOAT | NOT NULL, default=50 | 情绪温度 0–100 |
| `suggested_position_level` | FLOAT | NOT NULL, default=30 | 建议仓位 % |
| `overall_up_count` | INTEGER | nullable | 当日强势股池上涨数 |
| `overall_down_count` | INTEGER | nullable | 当日强势股池下跌数 |
| `overall_limit_up_count` | INTEGER | nullable | 当日强势股池涨停数 |
| `overall_limit_down_count` | INTEGER | nullable | 当日强势股池跌停数 |
| `strong_sectors` | JSON | nullable | 强势板块名称列表 |
| `dangerous_sectors` | JSON | nullable | 危险板块名称列表 |
| `active_sectors` | JSON | nullable | 活跃板块快照（phase≥2）|
| `dragon_changes` | JSON | nullable | 龙头列表快照 |
| `tomorrow_watchlist` | JSON | nullable | 明日关注代码列表 |
| `profit_effect_groups` | JSON | nullable | 赚钱效应分组数据（phase分组统计） |
| `profit_effect_sectors` | JSON | nullable | 板块赚钱效应快照 |
| `market_summary` | TEXT | nullable | 市场叙事文本（当前为规则生成，将替换为 LLM） |
| `created_at` | TIMESTAMP | server_default=now() | 创建时间 |
| `updated_at` | TIMESTAMP | onupdate=now() | 更新时间 |

**索引**：`date`（UNIQUE）

---

### 5.8 `screening_criteria` — 筛选条件配置

> `is_active=True` 的那一条为当前生效条件，同一时刻只有一条生效。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INTEGER | PK | 主键 |
| `name` | VARCHAR(100) | NOT NULL | 条件名称 |
| `description` | TEXT | nullable | 备注说明 |
| `is_active` | BOOLEAN | NOT NULL, default=True | 是否生效（index） |
| `include_sh_main` | BOOLEAN | NOT NULL | 纳入沪主板 |
| `include_sz_main` | BOOLEAN | NOT NULL | 纳入深主板 |
| `exclude_st` | BOOLEAN | NOT NULL | 排除 ST / *ST |
| `exclude_new_stock` | BOOLEAN | NOT NULL | 排除次新股 |
| `new_stock_months` | INTEGER | NOT NULL, default=12 | 上市不足 X 月视为次新 |
| `min_board_count_60d` | INTEGER | nullable, default=3 | 近60日最高连板数 > X（NULL=不启用） |
| `min_limit_up_days_60d` | INTEGER | nullable, default=9 | 近60日涨停天数 > X |
| `min_limit_up_days_10d` | INTEGER | nullable, default=4 | 近10日涨停天数 > X |
| `top_pct_rank_20d` | INTEGER | nullable, default=10 | 近20日涨幅进全市场前 X% |
| `created_at` | TIMESTAMP | server_default=now() | 创建时间 |
| `updated_at` | TIMESTAMP | server_default=now(), onupdate=now() | 更新时间 |

**入池判断逻辑**：
```
静态过滤（AND，全部不命中才不排除）:
  exclude_st=True  → 排除 is_st=True
  exclude_new_stock=True → 排除 trading_days < new_stock_months × 21

动态入池条件（OR，任一满足即入池）:
  board_count_60d > min_board_count_60d
  limit_up_days_60d > min_limit_up_days_60d
  limit_up_days_10d > min_limit_up_days_10d
  pct_rank_20d <= top_pct_rank_20d
```

---

### 表关系总览

```
stocks ──────────────────────┬──── stock_daily_snapshots   (1:N)
       │                     │
       │   primary_sector_id └──── sectors (M:1, SET NULL on delete)
       │
       └──── stock_sector_relations (N:M)
                    │
sectors ────────────┴──── stock_sector_relations (1:N)
        │
        └──── sector_daily_snapshots (1:N)
        │     (leader_stock_id → stocks)
        │
        └──── signals (1:N, nullable)

stocks  ─────────────────────── signals (1:N, nullable)

daily_reviews          （独立表，无外键，按日期汇总）
screening_criteria     （独立表，配置项）
```

---

## 6. 服务层说明

| 文件 | 职责 | 关键函数 |
|------|------|---------|
| `eastmoney_fetcher.py` | 外部行情抓取 | `fetch_main_board_stocks()`, `fetch_kline()`, `fetch_klines_batch()`, `fetch_strong_pool_codes()`, `fetch_stock_bk_codes()` |
| `screening_service.py` | K线→统计指标 | `compute_window_stats()`, `get_active_criteria()` |
| `sector_phase_service.py` | 板块生命周期阶段判断 | `refresh_sector_phases()`, `_classify_phase()` |
| `sector_top_stocks_service.py` | 板块龙头股计算 | 配合 `daily_update.py` 识别板块内 is_leader |
| `strong_stock_service.py` | 强势股池 DB 查询 | `get_strong_pool()`, `_enrich_stock_response()` |
| `dragon_leader_service.py` | 全市场龙头评分 | `identify_dragon_leaders()` → Top6 |
| `weak_to_strong_service.py` | 弱转强形态检测 | `detect_weak_to_strong_candidates()` → Top10 |
| `market_state_service.py` | 市场状态综合计算 | `get_current_market_state()` |
| `ai_service.py` | 复盘叙事生成 | `generate_market_review()`, `explain_signal()` |

---

## 7. 路由层说明

所有路由挂载在 `/api` 前缀下：

| 模块 | 前缀 | 主要路由 |
|------|------|---------|
| `stocks.py` | `/api/stocks` | 股票列表、强势股池、涨跌停池、个股详情、日快照 |
| `sectors.py` | `/api/sectors` | 板块列表、板块详情、历史快照、排行榜 |
| `signals.py` | `/api/signals` | 弱转强信号查询 |
| `reviews.py` | `/api/reviews` | 复盘增删改查、AI 生成今日复盘 |
| `market_state.py` | `/api/market-state` | 当前市场状态、情绪历史曲线 |
| `screening.py` | `/api/screening` | 筛选条件 CRUD |
| `admin.py` | `/api/admin` | 触发每日更新、板块同步（meta_only/full）、板块可见性管理、调度器状态查询 |
| `auth.py` | `/api/auth` | JWT 登录认证（admin 操作需登录） |

Swagger 文档：`http://localhost:8000/docs`

---

## 8. 运维脚本说明

### `daily_update.py` — 每日核心更新

```bash
# 基本用法
python scripts/daily_update.py

# 补录指定日期（历史数据回填）
python scripts/daily_update.py --date 2026-05-26

# 跳过板块同步（仅更新股票数据）
python scripts/daily_update.py --skip-boards
```

### `sync_boards.py` — 板块同步（两种模式）

```bash
# 板块全量同步（每周，约 5-8 分钟）：元数据 + 成份股数量 + 个股关联
python scripts/sync_boards.py

# 板块行情同步（每日，约 30s）：只更新涨跌幅/换手/市值
python scripts/sync_boards.py --meta-only

# 只更新个股关联（跳过元数据）
python scripts/sync_boards.py --stocks-only
```

**设计**：
1. **元数据同步**（每次都跑）：httpx 拉取 887 个板块基础数据，失败页重试 3 次
2. **成份股数量**（仅全量模式）：并发 20 workers 调用 `fs=b:{code}` API，从 `data.total` 获取真实成份股数
3. **个股→板块关联**（仅全量模式）：对当日涨跌停 + 强势股池并发调 F10，增量更新

> `stock_count` 字段由东财 API 获取，不由 `stock_sector_relations` 统计（避免部分数据污染）。
> 新板块默认 `is_watched=False`，需在管理页手动开启。日志写入 `logs/sync_boards_YYYY-MM-DD.log`。

### `init_screening.py` — 初始化筛选条件

```bash
python scripts/init_screening.py
# 写入默认筛选条件（首次部署必须执行）
```

### `seed_mock_data.py` — 注入模拟数据

```bash
python scripts/seed_mock_data.py
# 生成 20 只模拟股票 × 21 个交易日快照（开发调试用）
# 生成场景：AI芯片(高潮期)、军工(扩张期)、储能(启动期)、新能源车(衰退期)等
```

### `backfill_daily_reviews.py` — 历史复盘补录

```bash
python scripts/backfill_daily_reviews.py
# 为历史已有的日快照数据补录 daily_reviews 记录
```

### `seed_kline_history.py` — K线历史初始化

```bash
python scripts/seed_kline_history.py
# 一次性为 DB 中活跃股票批量拉取 65 日 K 线，填充历史 close_price 快照
# 使 _build_klines_from_db（DB重建路径）立即生效
# 首次部署或新环境迁移后运行一次即可
```

### `backfill_close_price.py` — close_price 反推补填

```bash
python scripts/backfill_close_price.py
# 从已知今日收盘价出发，利用 pct_change 向历史反推 close_price
# 适用于 seed_kline_history 前的辅助补填
```

---

## 9. 配置与环境变量

配置文件：`backend/.env`（从 `backend/.env.example` 复制）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+psycopg://chao@localhost/tradeflux` | 数据库连接串 |
| `API_PREFIX` | `/api` | API 路由前缀 |
| `DEBUG` | `True` | 调试模式 |
| `APP_NAME` | `TradeFlux` | 应用名称 |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | 允许跨域的前端地址 |

**SQLite 本地开发模式**（无需安装 PostgreSQL）：
```bash
DATABASE_URL=sqlite:///./tradeflux.db
```

> ⚠️ `backend/.env` 含数据库凭据，已加入 `.gitignore`，不会提交到版本库。

**生产环境追加变量**：

| 变量 | 示例值 | 说明 |
|------|--------|------|
| `ADMIN_USERNAME` | `admin` | 管理员账号 |
| `ADMIN_PASSWORD` | `your-password` | 管理员密码（建议修改） |
| `JWT_SECRET_KEY` | `openssl rand -hex 32` | JWT 签名密钥 |

---

## 10. 部署运维（阿里云 ECS）

### 服务管理

```bash
# 后端服务（systemd 守护）
sudo systemctl start/stop/restart tradeflux
sudo systemctl status tradeflux

# 查看实时日志
sudo journalctl -u tradeflux -f --no-pager

# 查看调度器启动日志
sudo journalctl -u tradeflux --since today | grep SCHED
```

### 数据更新流程

```
每个交易日 15:30（随机到 16:30）
  ↓ 内置调度器自动触发（jitter=3600）
  ↓ Step1: daily_update.py（~50s）
  ↓ Step2: sync_boards.py --meta-only（~30s）
  ↓ 失败自动重试，间隔 10 分钟，最多 3 次
  ↓ 结果写入 logs/daily_update_YYYY-MM-DD.log

每周一次（手动，前端下拉框）
  ↓ 板块全量同步（~5-8 分钟）
  ↓ 更新成份股数量 + 个股板块关联
```

### 更新部署流程

```bash
cd /opt/code/tradeflux

# 拉取最新代码
git pull origin main

# 后端：若有新依赖
cd backend && source .venv/bin/activate
pip install -r requirements.txt
deactivate

# 后端：若有 schema 变更（_apply_schema_patches 幂等执行）
source .venv/bin/activate
python -c "from app.database import init_db; init_db()"
deactivate

# 重启服务
sudo systemctl restart tradeflux

# 前端：重新构建
cd frontend && npm run build
```

### 日志文件

| 文件 | 内容 |
|------|------|
| `logs/daily_update_YYYY-MM-DD.log` | 每日数据更新 + 板块行情同步 + 调度器标签（[SCHED]/[RETRY1]/[UI]） |
| `logs/sync_boards_YYYY-MM-DD.log` | 板块全量同步详细过程 |
| `logs/last_update_status.json` | 最后一次更新的来源/状态/时间/时长（持久化，服务重启后仍可读） |
