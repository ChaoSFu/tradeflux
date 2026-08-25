"""
涨停明细同步：外部接口 → LimitUpDailyDetail / BrokenBoardDailyDetail 落库。
2026-08-25新增（涨停板块雷达）。

这一层只做"把外部涨停事实存下来"，不做任何聚合、不碰 Stock/Sector/Snapshot。
聚合在 limit_up_radar_service 里，读的是本地库。
"""
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models.app_config import AppConfig
from ..models.stock import Stock
from ..models.limit_up_detail import LimitUpDailyDetail, BrokenBoardDailyDetail
from .eastmoney_fetcher import CORE_RECALL_KEYWORD, fetch_strong_pool_codes
from .limit_up_detail_fetcher import (
    SOURCE_NAME, BrokenBoardDetail, LimitUpDetail, fetch_limit_up_details,
)

# 东财选股召回结果按交易日存一份（沿用仓库既有的 AppConfig KV 模式，不为一份
# 代码清单单独建表）
CORE_RECALL_KEY_PREFIX = "limit_up_radar:core_recall:"


def core_recall_key(trade_date: date) -> str:
    return f"{CORE_RECALL_KEY_PREFIX}{trade_date.isoformat()}"


def sync_core_recall_codes(db: Session, trade_date: date) -> int:
    """
    用东财条件选股把"近期活跃、值得进核心区"的股票捞一份存下来。

    这一路存在的意义是补本地重算的缺口：本地是从快照数涨停日，而快照只在股票进
    候选池那天才写，历史上有缺失就会数少（生产实测覆盖率94.3%）。数少 ⇒ 漏召回，
    而"不能漏掉板块核心"是这个功能的第一原则。东财这边是服务端实时算的，不依赖
    我们的历史完整性。

    只用来决定"该不该出现在核心区"，**不用来展示次数**——这个接口的解析器只回传
    名称/涨跌停方向，拿不到具体次数；展示仍用本地重算值（可验证的下界）。
    """
    codes = fetch_strong_pool_codes(keyword=CORE_RECALL_KEYWORD)
    if not codes:
        return 0                      # 拉空视为失败，不覆盖上一份（避免整体漏召回）
    key = core_recall_key(trade_date)
    row = db.query(AppConfig).filter(AppConfig.key == key).first()
    val = ",".join(sorted(codes))
    if row:
        row.value = val
    else:
        db.add(AppConfig(key=key, value=val))
    db.commit()
    return len(codes)


def get_core_recall_codes(db: Session, trade_date: date) -> set:
    """读当日的东财召回名单；当天没有就退回最近一份（名单是"近期活跃"，隔天仍有效）。"""
    row = db.query(AppConfig).filter(AppConfig.key == core_recall_key(trade_date)).first()
    if not row:
        row = (
            db.query(AppConfig)
            .filter(AppConfig.key.like(f"{CORE_RECALL_KEY_PREFIX}%"))
            .order_by(AppConfig.key.desc())
            .first()
        )
    return set((row.value or "").split(",")) if row and row.value else set()


def _stock_id_map(db: Session, codes: List[str]) -> Dict[str, int]:
    if not codes:
        return {}
    return {
        row[0]: row[1]
        for row in db.query(Stock.code, Stock.id).filter(Stock.code.in_(codes)).all()
    }


def _ensure_stock(db: Session, code: str, name: Optional[str], market: Optional[int]) -> int:
    """
    涨停股不在 stocks 表里时建一个存根。涨停池是全市场的，完全可能出现从没进过
    候选池的股票（北交所、冷门票）；没有 stock_id 就只能整只丢掉，那正是这个功能
    最不该发生的事——漏掉一只涨停股比多一行存根严重得多。
    name 必须在 flush 前给上（stocks.name 是 NOT NULL）。
    """
    stock = Stock(
        code=code,
        name=name or code,
        market="SH" if market == 1 else "SZ",
    )
    db.add(stock)
    db.flush()
    return stock.id


def sync_limit_up_details(
    db: Session, trade_date: date, timeout: int = 20,
) -> Tuple[int, int, List[str]]:
    """
    拉取并 upsert 指定交易日的涨停/炸板明细。
    返回 (涨停写入数, 炸板写入数, 警告列表)。

    只写这两张新表，**不触碰** Stock 的评分/快照/板块统计等任何既有数据
    （唯一的例外是给完全没见过的涨停股补一行 stocks 存根，见 _ensure_stock）。
    外部接口失败时抛异常交给调用方处理——绝不删除已有数据，页面会继续显示上一份
    并明确标注刷新失败时间，这跟本仓库"stale ≠ fresh，但 stale 也好过没有"的
    数据可信度原则一致。
    """
    details, broken, warnings = fetch_limit_up_details(trade_date, timeout=timeout)
    now = datetime.now()

    all_codes = [d.code for d in details] + [b.code for b in broken]
    id_map = _stock_id_map(db, all_codes)

    lu_written = _upsert_limit_ups(db, details, id_map, trade_date, now)
    bb_written = _upsert_broken_boards(db, broken, id_map, trade_date, now)

    # 当日已不在涨停名单里的旧行要清掉：盘中多次刷新时，一只14:30炸板打开的股票
    # 会从涨停池消失、进入炸板池，如果不删除它上一次刷新留下的涨停行，页面会同时
    # 把它显示成"涨停"和"炸板"。以 target_date 当日为范围，不影响历史。
    lu_removed = _prune_stale(db, LimitUpDailyDetail, trade_date, {d.code for d in details})
    bb_removed = _prune_stale(db, BrokenBoardDailyDetail, trade_date, {b.code for b in broken})
    if lu_removed or bb_removed:
        warnings.append(f"清理已不在名单中的旧行：涨停 {lu_removed} 条 / 炸板 {bb_removed} 条")

    db.commit()
    return lu_written, bb_written, warnings


def _upsert_limit_ups(
    db: Session, details: List[LimitUpDetail], id_map: Dict[str, int],
    trade_date: date, now: datetime,
) -> int:
    existing = {
        r.stock_code: r
        for r in db.query(LimitUpDailyDetail)
        .filter(LimitUpDailyDetail.trade_date == trade_date).all()
    }
    written = 0
    for d in details:
        sid = id_map.get(d.code) or _ensure_stock(db, d.code, d.name, d.market)
        id_map[d.code] = sid
        row = existing.get(d.code)
        if not row:
            row = LimitUpDailyDetail(stock_id=sid, stock_code=d.code, trade_date=trade_date)
            db.add(row)
        row.stock_id = sid
        row.stock_name = d.name or row.stock_name
        row.limit_reason = d.limit_reason
        row.limit_content = d.limit_content
        row.first_limit_time = d.first_limit_time
        row.last_limit_time = d.last_limit_time
        row.seal_amount = d.seal_amount
        row.broken_times = d.broken_times
        row.board_count = d.board_count
        row.limit_stat_days = d.limit_stat_days
        row.limit_stat_count = d.limit_stat_count
        row.price = d.price
        row.pct_change = d.pct_change
        row.amount = d.amount
        row.turnover_rate = d.turnover_rate
        row.float_market_cap = d.float_market_cap
        row.em_industry = d.em_industry
        row.source = SOURCE_NAME
        row.source_trade_date = trade_date
        row.refreshed_at = now
        written += 1
    return written


def _upsert_broken_boards(
    db: Session, broken: List[BrokenBoardDetail], id_map: Dict[str, int],
    trade_date: date, now: datetime,
) -> int:
    existing = {
        r.stock_code: r
        for r in db.query(BrokenBoardDailyDetail)
        .filter(BrokenBoardDailyDetail.trade_date == trade_date).all()
    }
    written = 0
    for b in broken:
        sid = id_map.get(b.code) or _ensure_stock(db, b.code, b.name, b.market)
        id_map[b.code] = sid
        row = existing.get(b.code)
        if not row:
            row = BrokenBoardDailyDetail(stock_id=sid, stock_code=b.code, trade_date=trade_date)
            db.add(row)
        row.stock_id = sid
        row.stock_name = b.name or row.stock_name
        row.first_limit_time = b.first_limit_time
        row.broken_times = b.broken_times
        row.pct_change = b.pct_change
        row.em_industry = b.em_industry
        row.source = SOURCE_NAME
        row.source_trade_date = trade_date
        row.refreshed_at = now
        written += 1
    return written


def _prune_stale(db: Session, model, trade_date: date, keep_codes: set) -> int:
    rows = db.query(model).filter(model.trade_date == trade_date).all()
    removed = 0
    for r in rows:
        if r.stock_code not in keep_codes:
            db.delete(r)
            removed += 1
    return removed


def get_last_refreshed(db: Session, trade_date: date) -> Optional[datetime]:
    """这一交易日的涨停明细最后一次成功刷新的时刻（页面新鲜度展示用）。"""
    row = (
        db.query(LimitUpDailyDetail.refreshed_at)
        .filter(LimitUpDailyDetail.trade_date == trade_date)
        .order_by(LimitUpDailyDetail.refreshed_at.desc())
        .first()
    )
    return row[0] if row else None


def get_latest_detail_date(db: Session) -> Optional[date]:
    """库里最新有涨停明细的交易日——页面不传日期时默认展示它。"""
    row = (
        db.query(LimitUpDailyDetail.trade_date)
        .order_by(LimitUpDailyDetail.trade_date.desc())
        .first()
    )
    return row[0] if row else None
