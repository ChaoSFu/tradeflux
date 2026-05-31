# TradeFlux · 短线晴雨表

> AI 驱动的 A 股短线市场状态研判平台。

⚠️ **郑重声明**：本工具**不提供**投资建议，**不做**自动交易，**不保证**盈利。
这是一个市场结构分析与认知辅助工具，一切交易决策由用户自行承担。

---

## 项目架构

```
tradeflux/
├── backend/          FastAPI + SQLAlchemy + PostgreSQL
│   ├── app/
│   │   ├── models/   SQLAlchemy ORM 数据模型
│   │   ├── schemas/  Pydantic v2 请求/响应类型
│   │   ├── routers/  REST API 路由（含 admin、screening）
│   │   └── services/ 核心分析引擎 + 真实行情抓取层
│   └── scripts/      每日更新、板块同步、数据导入等运维脚本
└── frontend/         React 18 + TypeScript + Vite + TailwindCSS
    └── src/
        ├── api/       Axios 接口层
        ├── components/ 布局、图表、基础 UI 组件
        ├── pages/     12 个主页面（含涨跌停分析、板块管理）
        ├── store/     Zustand 全局状态
        └── types/     完整 TypeScript 类型定义
```

## 核心分析引擎

| 引擎 | 文件 | 功能说明 |
|------|------|----------|
| 真实数据抓取 | `services/eastmoney_fetcher.py` | 东方财富/AkShare/新浪三路并备 |
| 动态筛选引擎 | `services/screening_service.py` | K线→窗口统计→可配置入池条件 |
| 强势股池 | `services/strong_stock_service.py` | 强势股池 DB 查询层（分页/排序） |
| 板块生命周期 | `services/sector_phase_service.py` | 0–6 阶段分类器 |
| 龙头识别 | `services/dragon_leader_service.py` | 多类型龙头评分 |
| 弱转强信号 | `services/weak_to_strong_service.py` | 三类信号模式检测 |
| 市场情绪综合 | `services/market_state_service.py` | 跨板块市场状态合成 |
| AI 点评层 | `services/ai_service.py` | 规则生成 + LLM 接口预留 |

## 快速启动

### 前置条件：PostgreSQL

```bash
# macOS（Homebrew）
brew install postgresql@16
brew services start postgresql@16

# 创建数据库
createdb tradeflux
```

### 1. 后端

```bash
cd backend

# 创建虚拟环境（需 Python 3.13）
python3.13 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 安装依赖（含 psycopg3 PostgreSQL 驱动 + AkShare + httpx）
pip install -r requirements.txt

# 复制并按需修改环境变量
cp .env.example .env
# DATABASE_URL 默认：postgresql+psycopg://chao@localhost/tradeflux

# 启动服务（首次启动自动建表）
uvicorn app.main:app --reload --port 8000

# 初始化默认筛选条件
python scripts/init_screening.py

# 选项 A：注入模拟数据（开发调试，无需网络）
python scripts/seed_mock_data.py

# 选项 B：同步真实数据（生产）
python scripts/sync_boards.py      # 同步东方财富板块（约 5-8 分钟）
# 在管理页 /admin/sectors 开启关注的板块后：
python scripts/daily_update.py     # 抓取今日行情并计算指标
```

接口文档：http://localhost:8000/docs

### 2. 前端

```bash
cd frontend
npm install
npm run dev
```

访问：http://localhost:5173

---

## 板块生命周期模型（0–6 阶段）

| 阶段 | 名称 | 特征描述 |
|------|------|----------|
| 0 | 隐匿期 | 无明显活动，强势股蛰伏 |
| 1 | 启动期 | 首批涨停出现，开始萌芽 |
| 2 | 扩张期 | 多只强势股发力，板高提升 |
| 3 | 高潮期 | 情绪顶点，5 板以上，FOMO 蔓延 |
| 4 | 分歧期 | 开始炸板，信号混乱 |
| 5 | 衰退期 | 抛压加剧，涨停数量枯竭 |
| 6 | 死亡区 | 毫无活力，风险最高 |

## 强势股入池条件（主板、非 ST、非次新）

满足以下**任一**条件即入池：
- `board_count_60d > 3`（60 日内连板次数 > 3）
- `limit_up_days_60d > 9`（60 日内涨停天数 > 9）
- `limit_up_days_10d > 4`（10 日内涨停天数 > 4）
- `top_10_pct_change_20d == true`（20 日内涨幅进入全市场前 10%）

## 操作建议对照

| 操作 | 含义 |
|------|------|
| 观察 | 盯盘，暂不建仓 |
| 关注 | 加入自选，等待机会 |
| 轻仓试探 | 小仓位试仓 |
| 持有 | 维持现有仓位 |
| 减仓 | 降低仓位 |
| 回避 | 远离，不参与 |

## API 接口总览

```
GET  /api/stocks                       获取所有股票列表
GET  /api/stocks/strong-pool           强势股池（支持排序筛选）
GET  /api/stocks/limit-moves           今日涨停/跌停列表
GET  /api/stocks/limit-moves/trend     近N日涨跌停趋势
GET  /api/stocks/{code}                股票详情
GET  /api/stocks/{code}/snapshots      30 日每日快照

GET  /api/sectors                      关注板块列表及阶段
GET  /api/sectors/{code}               板块详情 + 成员股票
GET  /api/sectors/{code}/snapshots     板块历史快照
GET  /api/sectors/ranking              板块排行榜（多维度排序）

GET  /api/signals                      弱转强信号列表
GET  /api/market-state                 当前市场状态综合
GET  /api/market-state/history         情绪曲线历史

GET  /api/reviews                      每日点评列表
GET  /api/reviews/latest               最新一条点评
POST /api/reviews/generate-today       AI 生成今日点评

GET  /api/screening/criteria           筛选条件列表
GET  /api/screening/criteria/active    当前生效的筛选条件
POST /api/screening/criteria           创建新筛选条件

POST /api/admin/update                 触发每日数据更新（异步）
GET  /api/admin/update/status          查询更新任务状态
POST /api/admin/sync-boards            触发板块全量同步
GET  /api/admin/sectors                全量板块（含 is_watched 状态）
PATCH /api/admin/sectors/{id}/watch    切换板块关注状态
```

## 未来规划

- **LLM 升级** — 将 `AIReviewGenerator` 后端替换为 Claude Sonnet，复盘质量大幅提升
- **夜间任务调度** — cron 自动执行 `daily_update.py`，无需手动触发
- **WebSocket 推送** — 实时市场状态推送
- **更多弱转强形态** — 量能异动、缩量企稳、涨停放量等
- **龙头记忆系统** — 龙头轮动追踪，识别换手换龙逻辑
- **策略市场** — 用户自定义评分公式参数
- **情绪知识图谱** — 板块间联动关系建模
- **多周期检测** — 跨时间框架市场周期识别
- **移动端 PWA** — 响应式点评界面

---

> TradeFlux 是市场研究与认知工具，不提供投资建议，不产生交易信号，不保证任何收益。
