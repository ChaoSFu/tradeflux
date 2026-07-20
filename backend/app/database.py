from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,   # 连接前检查存活，自动重连
    echo=settings.DEBUG,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from .models import stock, sector, signal, review, screening, regulatory, market_index, app_config, trade_journal  # noqa: F401 - imports trigger table registration
    Base.metadata.create_all(bind=engine)
    _apply_schema_patches()


def _apply_schema_patches():
    """
    幂等地补充新增字段（CREATE TABLE 不处理 ALTER TABLE）。
    每次启动/init_db 调用时执行，已存在的列会被跳过。
    """
    from sqlalchemy import text

    patches = [
        # Sector 新增字段（板块筛选指标）
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS sector_type VARCHAR(20)",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS stock_count INTEGER DEFAULT 0 NOT NULL",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS total_market_cap FLOAT DEFAULT 0.0 NOT NULL",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS turnover_rate FLOAT DEFAULT 0.0 NOT NULL",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS amount FLOAT DEFAULT 0.0 NOT NULL",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS pct_change_30d FLOAT DEFAULT 0.0 NOT NULL",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS limit_down_count INTEGER DEFAULT 0 NOT NULL",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS pct_change_5d FLOAT DEFAULT 0.0 NOT NULL",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS pct_change_10d FLOAT DEFAULT 0.0 NOT NULL",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS pct_change_20d FLOAT DEFAULT 0.0 NOT NULL",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS pct_change_60d FLOAT DEFAULT 0.0 NOT NULL",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS is_watched BOOLEAN DEFAULT FALSE NOT NULL",
        # Sector 排名 tag 字段（daily_update 刷新板块统计时写入）
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS rank_5d INTEGER",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS rank_10d INTEGER",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS rank_20d INTEGER",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS rank_60d INTEGER",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS rank_lu INTEGER",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS rank_board INTEGER",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS rank_strong INTEGER",
        # Snapshot 新增字段（收盘价，用于历史 KLine 重建计算 MA60/MA30）
        "ALTER TABLE stock_daily_snapshots ADD COLUMN IF NOT EXISTS close_price FLOAT",
        # Snapshot 新增字段（连续跌停数）
        "ALTER TABLE stock_daily_snapshots ADD COLUMN IF NOT EXISTS limit_down_count INTEGER DEFAULT 0 NOT NULL",
        # Snapshot 新增字段（股票阶段，用于历史赚钱效应分组）
        "ALTER TABLE stock_daily_snapshots ADD COLUMN IF NOT EXISTS phase VARCHAR(30)",
        # Snapshot 新增字段（一字板涨停/跌停标记）
        "ALTER TABLE stock_daily_snapshots ADD COLUMN IF NOT EXISTS is_one_word_limit_up BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE stock_daily_snapshots ADD COLUMN IF NOT EXISTS is_one_word_limit_down BOOLEAN DEFAULT FALSE NOT NULL",
        # Sector 新增字段（当日一字板涨停/跌停数，daily_update 板块统计写入）
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS one_word_up_count INTEGER DEFAULT 0 NOT NULL",
        "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS one_word_down_count INTEGER DEFAULT 0 NOT NULL",
        # 交易复盘:代码放开非必填（代码/名称填一个即可）
        "ALTER TABLE trade_journal ALTER COLUMN stock_code DROP NOT NULL",
        # 指数日线补 OHLC/量额（大盘趋势页K线）
        "ALTER TABLE index_daily_snapshots ADD COLUMN IF NOT EXISTS open FLOAT",
        "ALTER TABLE index_daily_snapshots ADD COLUMN IF NOT EXISTS high FLOAT",
        "ALTER TABLE index_daily_snapshots ADD COLUMN IF NOT EXISTS low FLOAT",
        "ALTER TABLE index_daily_snapshots ADD COLUMN IF NOT EXISTS volume FLOAT",
        "ALTER TABLE index_daily_snapshots ADD COLUMN IF NOT EXISTS amount FLOAT",
        # DailyReview 新增字段（强势股真实均涨幅）
        "ALTER TABLE daily_reviews ADD COLUMN IF NOT EXISTS strong_pool_avg_pct FLOAT",
        # Stock 主板块字段（计算一致性：所有展示模块读同一个字段）
        "ALTER TABLE stocks ADD COLUMN IF NOT EXISTS primary_sector_id INTEGER REFERENCES sectors(id) ON DELETE SET NULL",
        "ALTER TABLE stocks ADD COLUMN IF NOT EXISTS primary_sector_name VARCHAR(100)",
        # DailyReview 新增字段（涨跌停计数 & 赚钱效应历史快照）
        "ALTER TABLE daily_reviews ADD COLUMN IF NOT EXISTS overall_up_count INTEGER",
        "ALTER TABLE daily_reviews ADD COLUMN IF NOT EXISTS overall_down_count INTEGER",
        "ALTER TABLE daily_reviews ADD COLUMN IF NOT EXISTS overall_limit_up_count INTEGER",
        "ALTER TABLE daily_reviews ADD COLUMN IF NOT EXISTS overall_limit_down_count INTEGER",
        "ALTER TABLE daily_reviews ADD COLUMN IF NOT EXISTS profit_effect_groups JSONB",
        "ALTER TABLE daily_reviews ADD COLUMN IF NOT EXISTS profit_effect_sectors JSONB",
        # DailyReview 现有 JSON 字段改 JSONB（幂等，已是 JSONB 时会报错被静默跳过）
        # 注：active_sectors / dragon_changes 若为 JSON 类型则补 JSONB 列不影响现有列
        # StockSectorRelation 联合唯一约束（防止重复插入）
        "ALTER TABLE stock_sector_relations ADD CONSTRAINT uq_stock_sector UNIQUE (stock_id, sector_id)",
        # StockDailySnapshot 联合唯一约束（每只股票每天只能有一条快照）
        "ALTER TABLE stock_daily_snapshots ADD CONSTRAINT uq_snapshot_stock_date UNIQUE (stock_id, date)",
    ]
    # 每条补丁独立事务：Postgres 中任一语句报错会使整个事务进入 aborted 状态，
    # 若共用事务，末尾 ADD CONSTRAINT（无 IF NOT EXISTS）已存在时报错，
    # 会导致本次所有新增列一起被回滚。
    for sql in patches:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except Exception:
            pass  # 字段/约束已存在时静默跳过
