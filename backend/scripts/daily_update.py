"""
每日复盘数据更新脚本。

用法：
    cd backend && .venv/bin/python scripts/daily_update.py
    cd backend && .venv/bin/python scripts/daily_update.py --date 2026-05-26  # 补录指定日期
    cd backend && .venv/bin/python scripts/daily_update.py --skip-boards  # 跳过板块同步

流程：
    1. 从东方财富拉取主板股票列表（含今日涨跌幅）
    2. 确定候选股：当前强势池 + 今日高涨幅股（潜在新入池）
    3. 并发拉取候选股 60 日 K 线
    4. 计算窗口统计指标
    5. 与 ScreeningCriteria 对比 → 更新 in_strong_pool
    6. 写入今日 StockDailySnapshot
    7. 更新板块统计 → 刷新板块阶段
    8. 生成并写入今日 DailyReview（市场状态快照）
    9. 输出统计摘要
"""
import sys
import os
import argparse
import time
from datetime import date, datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# 结构化步骤日志
# ---------------------------------------------------------------------------

class StepLogger:
    """
    记录每日更新各步骤的耗时和关键指标，运行结束后输出汇总表。
    同时写入 logs/daily_update_YYYY-MM-DD.log 文件，便于事后排查。
    """

    def __init__(self, run_date: date):
        self.run_date = run_date
        self.started_at = datetime.now()
        self.steps: list[dict] = []          # 已完成步骤
        self._step_start: Optional[float] = None
        self._current_name: str = ""

        # 确保 logs/ 目录存在
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"daily_update_{run_date}.log")
        self._file = open(log_path, "a", encoding="utf-8")
        self._log(f"\n{'='*60}")
        self._log(f"  TradeFlux 每日更新  {run_date}  启动于 {self.started_at.strftime('%H:%M:%S')}")
        self._log(f"{'='*60}")

    def _log(self, msg: str):
        """同时输出到 stdout（被 admin 端捕获）和日志文件。"""
        print(msg)
        self._file.write(msg + "\n")
        self._file.flush()

    def begin(self, name: str):
        """开始计时一个步骤。"""
        self._current_name = name
        self._step_start = time.time()
        self._log(f"\n[{name}]")

    def end(self, ok: bool = True, detail: str = ""):
        """结束当前步骤，记录耗时和状态。"""
        elapsed = time.time() - (self._step_start or time.time())
        status = "✅" if ok else "❌"
        self.steps.append({
            "name": self._current_name,
            "ok": ok,
            "elapsed": elapsed,
            "detail": detail,
        })
        suffix = f"  {detail}" if detail else ""
        self._log(f"  {status} 完成  耗时 {elapsed:.1f}s{suffix}")

    def error(self, msg: str):
        """标记当前步骤失败。"""
        self.end(ok=False, detail=msg)

    def info(self, msg: str):
        """步骤内的详情日志。"""
        self._log(f"  {msg}")

    def summary(self):
        """输出最终汇总表。"""
        total = time.time() - self.started_at.timestamp()
        self._log(f"\n{'─'*60}")
        self._log(f"  {'步骤':<20} {'状态':^4} {'耗时':>7}  关键指标")
        self._log(f"{'─'*60}")
        for s in self.steps:
            status = "✅" if s["ok"] else "❌"
            name = s["name"][:20]
            elapsed = f"{s['elapsed']:.1f}s"
            self._log(f"  {name:<20} {status:^4} {elapsed:>7}  {s['detail']}")
        self._log(f"{'─'*60}")
        self._log(f"  总耗时: {total:.1f}s  完成于 {datetime.now().strftime('%H:%M:%S')}")
        self._log(f"{'='*60}\n")
        self._file.close()

from sqlalchemy import or_
from app.database import SessionLocal, init_db
from app.models.stock import Stock, StockDailySnapshot
from app.models.sector import Sector, StockSectorRelation, SectorDailySnapshot
from app.models.review import DailyReview
from app.models.signal import Signal
from app.services.eastmoney_fetcher import (
    StockBasicInfo, KLineBar,
    fetch_main_board_stocks, fetch_klines_batch, get_limit_pct,
    fetch_strong_pool_codes, fetch_stock_bk_codes, fetch_limit_move_codes,
)
from app.services.screening_service import (
    StockWindowStats,
    compute_window_stats, get_active_criteria,
)
from app.services.sector_phase_service import refresh_sector_phases




# ---------------------------------------------------------------------------
# 板块关联补全（针对无关联的涨跌停股票）
# ---------------------------------------------------------------------------


def _sync_missing_sector_relations(db, limit_move_stocks: list, log=None) -> int:
    """
    对今日涨跌停但 stock_sector_relations 为空的股票，并发补建板块关联。

    数据来源：东财 emweb F10 接口（CoreConception）。
    只关联 is_watched=True 的板块，动态噪声板块自动过滤。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not limit_move_stocks:
        return 0

    # 批量查出有关联的 stock_id（避免 N+1）
    stock_ids = [s.id for s in limit_move_stocks]
    has_rel_ids = {
        row[0]
        for row in db.query(StockSectorRelation.stock_id)
        .filter(StockSectorRelation.stock_id.in_(stock_ids))
        .distinct()
        .all()
    }
    stocks_no_rel = [s for s in limit_move_stocks if s.id not in has_rel_ids]

    if not stocks_no_rel:
        return 0

    if log:
        log.info(f"  {len(stocks_no_rel)} 只无关联股，并发从东财F10补建板块关联...")

    # 预加载所有 watched 板块的 code→Sector 映射
    sector_map: dict = {
        s.code: s
        for s in db.query(Sector).filter(Sector.is_watched == True).all()  # noqa
    }

    # 并发拉取各股的板块归属
    def fetch_one(stock) -> tuple:
        return stock, fetch_stock_bk_codes(stock.code)

    results: list[tuple] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_one, s): s for s in stocks_no_rel}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                pass

    # 写入关联（单线程操作 DB，跳过已存在的）
    # 先批量读出已有关联，避免重复插入触发 uq_stock_sector 约束
    existing_rel_keys = {
        (r.stock_id, r.sector_id)
        for r in db.query(StockSectorRelation.stock_id, StockSectorRelation.sector_id)
        .filter(StockSectorRelation.stock_id.in_([s.id for s, _ in results]))
        .all()
    }
    total_created = 0
    for stock, bk_codes in results:
        created = 0
        for bk_code in bk_codes:
            sector = sector_map.get(bk_code)
            if not sector:
                continue
            if (stock.id, sector.id) in existing_rel_keys:
                continue
            db.add(StockSectorRelation(stock_id=stock.id, sector_id=sector.id))
            existing_rel_keys.add((stock.id, sector.id))
            created += 1
        if created:
            total_created += created
            if log:
                log.info(f"    {stock.code} {stock.name}: 补建 {created} 条板块关联")

    db.commit()
    return total_created




# ---------------------------------------------------------------------------
# DB 历史快照 → KLineBar 重建（避免重复拉取 60 日 K 线）
# ---------------------------------------------------------------------------

# 当 DB 快照数量达到此阈值时，使用 DB 重建路径；否则走全量拉取路径
_MIN_SNAPSHOTS_FOR_DB_REBUILD = 60


def _snapshots_to_klinebars(snaps: list) -> List[KLineBar]:
    """
    将已排序的 StockDailySnapshot 列表转换为 KLineBar 列表。
    open/high/low 在 compute_window_stats 中未被使用，填 0.0。
    close_price 为 None（老快照迁移前无此字段）时降级为 0.0，
    此时 MA60/MA30 计算结果为 0，阶段判定回退为 "normal"（可接受的保守策略）。
    """
    bars: List[KLineBar] = []
    for s in snaps:
        bars.append(KLineBar(
            date=s.date,
            open_price=0.0,
            close_price=s.close_price or 0.0,
            high_price=0.0,
            low_price=0.0,
            pct_change=s.pct_change or 0.0,
            turnover_rate=s.turnover_rate or 0.0,
            is_limit_up=bool(s.is_limit_up),
            is_limit_down=bool(s.is_limit_down),
            is_broken_board=bool(s.is_broken_board),
        ))
    return bars


def _build_klines_from_db(
    candidates: List[StockBasicInfo],
    db,
    target_date,
) -> tuple[dict, List[StockBasicInfo], List[StockBasicInfo]]:
    """
    将候选股分为两组：
      - db_group：DB 已有 ≥60 条快照，从历史快照重建 KLineBar，只需 API 拉今日一根
      - full_group：DB 快照不足，需 API 拉完整 65 日

    返回：
      (db_klines_map, db_group, full_group)
      db_klines_map — {code: [KLineBar, ...]}（含今日占位 KLineBar，today_bar 待替换）
    """
    from sqlalchemy import func as sqlfunc

    codes = [c.code for c in candidates]

    # 查每只股票在 DB 里非今日历史快照数量 & 批量获取最近 65 条快照
    stock_id_map: dict[str, int] = {
        row[0]: row[1]
        for row in db.query(Stock.code, Stock.id).filter(Stock.code.in_(codes)).all()
    }

    # 快照数量统计（排除 target_date，只看历史）
    snap_counts: dict[int, int] = {}
    if stock_id_map:
        rows = (
            db.query(StockDailySnapshot.stock_id, sqlfunc.count())
            .filter(
                StockDailySnapshot.stock_id.in_(list(stock_id_map.values())),
                StockDailySnapshot.date < target_date,
                StockDailySnapshot.close_price.isnot(None),  # 只有存过 close_price 的才算有效历史
            )
            .group_by(StockDailySnapshot.stock_id)
            .all()
        )
        snap_counts = {sid: cnt for sid, cnt in rows}

    # 批量拉最近 65 条历史快照（date < target_date）
    valid_stock_ids = [
        sid for sid in stock_id_map.values()
        if snap_counts.get(sid, 0) >= _MIN_SNAPSHOTS_FOR_DB_REBUILD
    ]

    # {stock_id: [snap, ...]} 按日期升序，最多取 65 条
    snaps_by_stock: dict[int, list] = {}
    if valid_stock_ids:
        from sqlalchemy import and_
        # 用窗口函数方式：每个 stock_id 取最近 65 条
        subq = (
            db.query(StockDailySnapshot)
            .filter(
                StockDailySnapshot.stock_id.in_(valid_stock_ids),
                StockDailySnapshot.date < target_date,
            )
            .order_by(StockDailySnapshot.stock_id, StockDailySnapshot.date.desc())
            .all()
        )
        for snap in subq:
            lst = snaps_by_stock.setdefault(snap.stock_id, [])
            if len(lst) < 65:
                lst.append(snap)
        # 每组反转为升序
        for sid in snaps_by_stock:
            snaps_by_stock[sid].reverse()

    db_group: List[StockBasicInfo] = []
    full_group: List[StockBasicInfo] = []
    db_klines_map: dict[str, List[KLineBar]] = {}

    for info in candidates:
        sid = stock_id_map.get(info.code)
        snaps = snaps_by_stock.get(sid, []) if sid else []
        if len(snaps) >= _MIN_SNAPSHOTS_FOR_DB_REBUILD:
            db_group.append(info)
            db_klines_map[info.code] = _snapshots_to_klinebars(snaps)
        else:
            full_group.append(info)

    return db_klines_map, db_group, full_group


# ---------------------------------------------------------------------------
# 候选股精算入库（评分 + 快照）
# ---------------------------------------------------------------------------

def _upsert_stock(db, info: StockBasicInfo, stats: StockWindowStats, in_pool: bool) -> Stock:
    """更新或创建 Stock 记录"""
    stock = db.query(Stock).filter(Stock.code == info.code).first()
    if not stock:
        stock = Stock(code=info.code)
        db.add(stock)
        db.flush()  # 确保 id 生成，防止同一 code 重复插入

    stock.name = info.name
    stock.market = "SH" if info.market == 1 else "SZ"
    stock.is_st = info.is_st
    stock.is_new_stock = stats.is_new_stock
    stock.in_strong_pool = in_pool
    stock.emotion_score = stats.emotion_score
    stock.risk_score = stats.risk_score
    stock.leader_score = stats.leader_score
    stock.board_count_60d = stats.board_count_60d
    stock.board_down_count_60d = stats.board_down_count_60d
    stock.limit_up_days_60d = stats.limit_up_days_60d
    stock.limit_up_days_20d = stats.limit_up_days_20d
    stock.limit_up_days_10d = stats.limit_up_days_10d
    stock.pct_change_60d = round(stats.pct_change_60d, 2)
    stock.pct_change_20d = round(stats.pct_change_20d, 2)
    stock.pct_change_10d = round(stats.pct_change_10d, 2)
    stock.top_10_pct_change_20d = (stats.pct_change_20d > 0 and stats.pct_change_20d > 30)  # 粗判
    # 阶段：仅对强势池股票标记，移出池时清空
    stock.phase = stats.phase if in_pool else None
    return stock


def _upsert_snapshot(
    db, stock: Stock, stats: StockWindowStats, today: date,
    is_limit_up: bool | None = None, is_limit_down: bool | None = None,
) -> None:
    """
    写入今日快照（存在则更新，不存在则新建）。
    is_limit_up / is_limit_down 传入非 None 时为权威值（来自涨跌停选股 API），
    覆盖本地 K 线反推结果；传入 None 时退回本地计算值。
    """
    snap = (
        db.query(StockDailySnapshot)
        .filter(
            StockDailySnapshot.stock_id == stock.id,
            StockDailySnapshot.date == today,
        )
        .first()
    )
    if not snap:
        snap = StockDailySnapshot(stock_id=stock.id, date=today)
        db.add(snap)

    snap.close_price = stats.today_close_price
    snap.pct_change = stats.today_pct_change
    snap.turnover_rate = stats.today_turnover
    snap.is_limit_up = stats.today_is_limit_up if is_limit_up is None else is_limit_up
    snap.is_limit_down = stats.today_is_limit_down if is_limit_down is None else is_limit_down
    snap.is_broken_board = stats.today_is_broken_board
    snap.board_count = stats.board_count_current
    snap.limit_down_count = stats.limit_down_count_current
    snap.board_count_60d = stats.board_count_60d
    snap.board_down_count_60d = stats.board_down_count_60d
    snap.limit_up_days_60d = stats.limit_up_days_60d
    snap.limit_up_days_20d = stats.limit_up_days_20d
    snap.limit_up_days_10d = stats.limit_up_days_10d
    snap.pct_change_60d = round(stats.pct_change_60d, 2)
    snap.pct_change_20d = round(stats.pct_change_20d, 2)
    snap.pct_change_10d = round(stats.pct_change_10d, 2)
    snap.top_10_pct_change_20d = stats.pct_change_20d > 30  # 粗判阈值
    snap.phase = stats.phase                                  # 落库当日阶段，供次日赚钱效应分组用
    snap.emotion_score = stats.emotion_score
    snap.risk_score = stats.risk_score
    snap.leader_score = stats.leader_score


# ---------------------------------------------------------------------------
# 板块统计更新
# ---------------------------------------------------------------------------

def _refresh_sector_stats(db, target_date) -> None:
    """
    重新计算板块统计指标，使用直接 DB 联查替代懒加载，避免 N+1 查询。
    limit_up_count / limit_down_count 限定 target_date 当日快照，
    覆盖 stock_sector_relations 中的所有成员（不限于强势池）。
    """
    from sqlalchemy import func as sqlfunc

    # ── 一次性批量查询今日所有快照 ────────────────────────────────────────────
    # {stock_id: StockDailySnapshot}
    today_snap_map: dict = {
        snap.stock_id: snap
        for snap in db.query(StockDailySnapshot)
        .filter(StockDailySnapshot.date == target_date)
        .all()
    }

    # ── 一次性批量查询所有板块的成员关系 ─────────────────────────────────────
    # {sector_id: [stock_id, ...]}
    from collections import defaultdict
    sector_stock_map: dict = defaultdict(list)
    for rel in db.query(StockSectorRelation).all():
        sector_stock_map[rel.sector_id].append(rel.stock_id)

    # ── 按 stock_id 查强势股 ─────────────────────────────────────────────────
    strong_ids: set = {
        s.id for s in db.query(Stock.id).filter(Stock.in_strong_pool == True).all()  # noqa: E712
    }
    # 强势股完整对象（用于 leader_score / emotion_score 等字段）
    strong_map: dict = {
        s.id: s for s in db.query(Stock).filter(Stock.in_strong_pool == True).all()  # noqa: E712
    }

    sectors = db.query(Sector).all()
    for sector in sectors:
        stock_ids = sector_stock_map.get(sector.id, [])
        if not stock_ids:
            continue

        # 今日有快照的成员（非强势股也参与涨停/跌停统计）
        snaps_today = [today_snap_map[sid] for sid in stock_ids if sid in today_snap_map]

        # 板块内强势股（用于情绪/风险/板高等评分）
        strong_in_sector = [strong_map[sid] for sid in stock_ids if sid in strong_map]

        sector.strong_stock_count = len(strong_in_sector)
        sector.limit_up_count   = sum(1 for s in snaps_today if s.is_limit_up)
        sector.limit_down_count = sum(1 for s in snaps_today if s.is_limit_down)
        sector.board_height = max(
            (today_snap_map[s.id].board_count for s in strong_in_sector if s.id in today_snap_map),
            default=0,
        )
        sector.emotion_score = (
            sum(s.emotion_score for s in strong_in_sector) / len(strong_in_sector)
            if strong_in_sector else 0.0
        )
        sector.risk_score = (
            sum(s.risk_score for s in strong_in_sector) / len(strong_in_sector)
            if strong_in_sector else 0.0
        )
        sector.continuity_score = min(100.0, sector.board_height * 15.0 + len(strong_in_sector) * 5.0)

        if strong_in_sector:
            leader = max(strong_in_sector, key=lambda s: s.leader_score)
            sector.leader_stock_id = leader.id

    db.commit()


# ---------------------------------------------------------------------------
# 板块排名 tag 写入
# ---------------------------------------------------------------------------

def _refresh_sector_ranks(db) -> None:
    """
    对 is_watched=True 的板块，计算7个维度的 dense rank（前5名，value>0）并落库。
    必须在 _refresh_sector_stats / refresh_sector_phases 之后调用。
    """
    sectors = db.query(Sector).filter(Sector.is_watched == True).all()  # noqa

    RANK_FIELDS = [
        ("rank_5d",     "pct_change_5d"),
        ("rank_10d",    "pct_change_10d"),
        ("rank_20d",    "pct_change_20d"),
        ("rank_60d",    "pct_change_60d"),
        ("rank_lu",     "limit_up_count"),
        ("rank_board",  "board_height"),
        ("rank_strong", "strong_stock_count"),
    ]

    # 先全部清空本轮 rank（防止旧数据残留）
    for s in sectors:
        for rank_key, _ in RANK_FIELDS:
            setattr(s, rank_key, None)

    for rank_key, field in RANK_FIELDS:
        eligible = [s for s in sectors if (getattr(s, field, 0) or 0) > 0]
        eligible.sort(key=lambda s: getattr(s, field) or 0, reverse=True)
        rank, prev_val, count = 1, None, 0
        for s in eligible:
            val = getattr(s, field) or 0
            if prev_val is not None and val != prev_val:
                rank = count + 1
                if rank > 5:
                    break
            if rank <= 5:
                setattr(s, rank_key, rank)
            prev_val = val
            count += 1

    db.commit()


# ---------------------------------------------------------------------------
# 每日 Review 快照
# ---------------------------------------------------------------------------

def _update_primary_sectors(db) -> None:
    """
    为所有强势股计算并落库主板块（primary_sector_id / primary_sector_name）。
    优先级：watched板块 → strong_stock_count 最多 → board_height 最高 → emotion_score 最高。
    必须在 _refresh_sector_stats / refresh_sector_phases 之后调用（保证统计数据当日最新）。
    """
    stocks = db.query(Stock).all()  # 全量：包含曾经入池的股票也要更新
    if not stocks:
        return

    stock_ids = [s.id for s in stocks]
    all_rels = (
        db.query(StockSectorRelation)
        .filter(StockSectorRelation.stock_id.in_(stock_ids))
        .all()
    )

    sector_id_set = {rel.sector_id for rel in all_rels}
    sector_map: dict[int, Sector] = {}
    if sector_id_set:
        sector_map = {
            s.id: s
            for s in db.query(Sector).filter(
                Sector.id.in_(sector_id_set),
                Sector.is_watched == True,  # noqa: E712
            ).all()
        }

    # stock_id → [sector_id, ...]（保留原始关联顺序）
    stock_rel_sids: dict[int, list[int]] = {}
    for rel in all_rels:
        stock_rel_sids.setdefault(rel.stock_id, []).append(rel.sector_id)

    updated = 0
    for stock in stocks:
        sids = stock_rel_sids.get(stock.id, [])
        watched = [sector_map[sid] for sid in sids if sid in sector_map]

        if not watched:
            if stock.primary_sector_id is not None or stock.primary_sector_name is not None:
                stock.primary_sector_id = None
                stock.primary_sector_name = None
                updated += 1
            continue

        # 优先级：股票数最多 → 连板高度最高 → 情绪分最高
        best = max(watched, key=lambda s: (s.strong_stock_count, s.board_height, s.emotion_score))

        if stock.primary_sector_id != best.id:
            stock.primary_sector_id = best.id
            stock.primary_sector_name = best.name
            updated += 1

    db.commit()
    print(f"  主板块已更新: {updated} 只股票")


def _save_weak_to_strong_signals(db, today: date) -> None:
    """将今日弱转强候选写入 Signal 表（幂等：先标记旧信号失效，再写新的）"""
    from app.services.weak_to_strong_service import detect_weak_to_strong_candidates

    # 把今天以前未失效的信号全部标记 is_active=False
    db.query(Signal).filter(
        Signal.date < today,
        Signal.is_active == True,  # noqa: E712
    ).update({"is_active": False}, synchronize_session=False)

    # 删除今天已有的（重跑幂等）
    db.query(Signal).filter(Signal.date == today).delete()

    candidates = detect_weak_to_strong_candidates(db, as_of=today)
    if not candidates:
        db.commit()
        print("  弱转强信号: 无候选")
        return

    # 预取 stock_id（候选里带 stock_code）
    code_to_id: dict[str, int] = {
        row[0]: row[1]
        for row in db.query(Stock.code, Stock.id)
        .filter(Stock.code.in_([c.stock_code for c in candidates]))
        .all()
    }

    new_signals = []
    for c in candidates:
        stock_id = code_to_id.get(c.stock_code)
        sig = Signal(
            stock_id=stock_id,
            date=today,
            signal_type=c.signal_type,
            confidence_score=c.confidence_score,
            risk_level=c.risk_level,
            explanation=c.explanation,
            suggested_action=c.suggested_action,
            is_active=True,
            is_triggered=False,
        )
        new_signals.append(sig)

    db.add_all(new_signals)
    db.commit()
    print(f"  弱转强信号: 写入 {len(new_signals)} 条 "
          f"({', '.join(c.stock_name for c in candidates[:3])}"
          f"{'…' if len(candidates) > 3 else ''})")


def _save_daily_review(db, today: date) -> None:
    """将今日市场状态写入 DailyReview 表（用于情绪曲线历史及赚钱效应历史）"""
    from app.services.market_state_service import (
        _compute_profit_effect, _compute_loss_effect,
        _emotion_temperature, _classify_market_phase, _classify_emotion_cycle,
        _suggested_position, get_profit_effect,
    )
    from app.services.dragon_leader_service import identify_dragon_leaders

    # 只使用「已关注」板块（与 get_current_market_state 保持一致）
    sectors = db.query(Sector).filter(Sector.is_watched == True).all()  # noqa: E712
    profit = _compute_profit_effect(sectors)
    loss = _compute_loss_effect(sectors)
    temp = _emotion_temperature(profit, loss)
    phase = _classify_market_phase(temp)
    cycle = _classify_emotion_cycle(temp)
    position = _suggested_position(phase, loss)

    # ── 强势股今日快照（批量，统一用于均涨幅 + 涨跌停统计）─────────────────
    today_snaps = (
        db.query(StockDailySnapshot)
        .join(Stock, Stock.id == StockDailySnapshot.stock_id)
        .filter(
            StockDailySnapshot.date == today,
            Stock.in_strong_pool == True,  # noqa: E712
        )
        .all()
    )
    pcts = [s.pct_change for s in today_snaps if s.pct_change is not None]
    strong_pool_avg_pct = round(sum(pcts) / len(pcts), 2) if pcts else None

    def _cls(p: float) -> str:
        return "up" if p > 0.5 else ("down" if p < -0.5 else "flat")

    overall_up    = sum(1 for p in pcts if _cls(p) == "up")
    overall_down  = sum(1 for p in pcts if _cls(p) == "down")
    overall_lu    = sum(1 for s in today_snaps if s.is_limit_up)
    overall_ld    = sum(1 for s in today_snaps if s.is_limit_down)

    # ── 赚钱效应分组 & 板块快照 ──────────────────────────────────────────
    pe = get_profit_effect(db)
    profit_groups = None
    profit_sectors_json = None
    if pe.has_data:
        profit_groups = [
            {
                "key": g.key, "label": g.label,
                "stock_count": g.stock_count, "avg_pct": g.avg_pct,
                "up_count": g.up_count, "down_count": g.down_count, "flat_count": g.flat_count,
            }
            for g in pe.groups
        ]
        profit_sectors_json = [
            {
                "sector_code": s.sector_code, "sector_name": s.sector_name,
                "stock_count": s.stock_count, "avg_pct": s.avg_pct,
                "up_count": s.up_count, "down_count": s.down_count,
            }
            for s in pe.sectors
        ]

    # ── 活跃板块快照（phase >= 2）────────────────────────────────────────
    active_sectors_json = [
        {
            "code": s.code, "name": s.name,
            "phase": s.phase, "emotion_score": s.emotion_score,
            "strong_stock_count": s.strong_stock_count, "board_height": s.board_height,
        }
        for s in sorted(sectors, key=lambda x: x.emotion_score, reverse=True)
        if s.phase in (2, 3)
    ]

    # ── 板块强弱名单 ──────────────────────────────────────────────────────
    strong_sectors_list   = [s.name for s in sectors if s.phase in (2, 3)]
    dangerous_sectors_list = [s.name for s in sectors if s.phase in (5, 6)]

    # ── 龙头股快照 ────────────────────────────────────────────────────────
    leaders = identify_dragon_leaders(db)
    dragon_changes_json = [
        {
            "stock_code": l.stock_code, "stock_name": l.stock_name,
            "sector_name": l.sector_name, "leader_type": l.leader_type,
            "board_height": l.board_height, "leader_score": l.leader_score,
            "risk_score": l.risk_score,
        }
        for l in leaders
    ]

    # ── 写入 DailyReview ─────────────────────────────────────────────────
    db.query(DailyReview).filter(DailyReview.date == today).delete()
    review = DailyReview(
        date=today,
        market_phase=phase,
        profit_effect_score=round(profit, 1),
        loss_effect_score=round(loss, 1),
        strong_pool_avg_pct=strong_pool_avg_pct,
        overall_up_count=overall_up,
        overall_down_count=overall_down,
        overall_limit_up_count=overall_lu,
        overall_limit_down_count=overall_ld,
        emotional_temperature=round(temp, 1),
        suggested_position_level=round(position, 1),
        emotion_cycle=cycle,
        strong_sectors=strong_sectors_list,
        dangerous_sectors=dangerous_sectors_list,
        active_sectors=active_sectors_json,
        dragon_changes=dragon_changes_json,
        profit_effect_groups=profit_groups,
        profit_effect_sectors=profit_sectors_json,
        market_summary=(
            f"[{today}] 自动生成 — 情绪温度 {round(temp, 1)}，"
            f"市场阶段 {phase}，强势股均涨幅 {strong_pool_avg_pct}%，"
            f"涨停 {overall_lu} 只"
        ),
    )
    db.add(review)
    db.commit()
    print(f"  DailyReview 已写入: 龙头 {len(dragon_changes_json)} 只，"
          f"活跃板块 {len(active_sectors_json)} 个，"
          f"赚钱效应分组 {len(profit_groups or [])} 组，"
          f"板块快照 {len(profit_sectors_json or [])} 个")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_daily_update(target_date: date, skip_boards: bool = False) -> dict:
    """
    执行每日更新。返回汇总 dict：
      {"degraded": bool, "warnings": [str]}
    degraded=True 表示有数据源 API 降级（已回退 DB），界面应提示数据可能不完整/过时。
    抛异常的硬失败由调用方捕获，不在此返回。
    """
    log = StepLogger(target_date)
    db = SessionLocal()
    api_warnings: list[str] = []   # API 降级告警（供界面提示，数据可能不完整或过时）

    def _result() -> dict:
        return {"degraded": bool(api_warnings), "warnings": list(api_warnings)}

    try:
        init_db()

        # ── 筛选条件加载 ─────────────────────────────────────────
        criteria = get_active_criteria(db)
        if not criteria:
            log.info("❌ 未找到生效的筛选条件，请先运行 scripts/init_screening.py")
            return _result()
        log.info(f"筛选条件: {criteria.name} | "
                 f"连板>={criteria.min_board_count_60d+1} | "
                 f"60日涨停>={criteria.min_limit_up_days_60d+1} | "
                 f"10日涨停>={criteria.min_limit_up_days_10d+1} | "
                 f"20日涨幅前{criteria.top_pct_rank_20d}%")

        # ── 第1步：确定候选股（通过东财选股 API）──────────────────
        log.begin("确定候选股")

        # 并发调两个东财选股 API（with_names=True 顺带带回股票名，省去全市场拉取）
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_strong = ex.submit(fetch_strong_pool_codes, with_names=True)
            fut_limit  = ex.submit(fetch_limit_move_codes, with_detail=True)
            api_pool_names    = fut_strong.result()   # {code: name}
            api_limit_detail  = fut_limit.result()    # {code: {"name", "limit_dir"}}

        api_pool_codes  = set(api_pool_names)
        api_limit_codes = set(api_limit_detail)
        # 选股结果均为「非ST」，故出现在此 map 的股票当日 is_st=False（摘帽自动修正）。
        # 用于刷新已知候选股的 name/is_st，并给新股直接命名，替代全市场列表拉取。
        api_name_map: dict[str, str] = {
            **api_pool_names,
            **{c: d["name"] for c, d in api_limit_detail.items()},
        }

        # 涨跌停 API 是否真正返回数据（决定是否以其为涨跌停的权威来源）。
        # 失败回退到 DB 时不做权威覆盖/对账，避免循环依赖与误清。
        limit_api_ok = bool(api_limit_codes)

        # 强势池：API 结果为准，失败回退 DB
        db_pool_codes = {
            s.code for s in db.query(Stock).filter(Stock.in_strong_pool == True).all()  # noqa
        }
        if api_pool_codes:
            strong_pool_codes = api_pool_codes
            log.info(f"强势股 API: {len(api_pool_codes)} 只")
            in_db_not_api = db_pool_codes - api_pool_codes
            in_api_not_db = api_pool_codes - db_pool_codes
            if in_db_not_api:
                log.info(f"  DB有但API无（待退出）: {sorted(in_db_not_api)}")
            if in_api_not_db:
                log.info(f"  API有但DB无（待入池）: {sorted(in_api_not_db)}")
        else:
            strong_pool_codes = db_pool_codes
            log.info(f"强势股 API 不可用，回退 DB {len(db_pool_codes)} 只")
            api_warnings.append("强势股选股 API 调用失败，已回退数据库历史，强势池可能未更新")

        if api_limit_codes:
            log.info(f"涨跌停 API: {len(api_limit_codes)} 只")
        else:
            # 涨跌停 API 失败时回退：从昨日快照补充
            api_limit_codes = {
                row[0]
                for row in db.query(Stock.code)
                .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
                .filter(
                    StockDailySnapshot.date == target_date,
                    or_(
                        StockDailySnapshot.is_limit_up == True,    # noqa
                        StockDailySnapshot.is_limit_down == True,  # noqa
                    ),
                ).all()
            }
            log.info(f"涨跌停 API 不可用，回退 DB {len(api_limit_codes)} 只")
            api_warnings.append("涨跌停选股 API 调用失败，已回退数据库历史，今日涨跌停数据可能不完整或过时")

        # 候选股 = 强势池 ∪ 涨跌停
        all_candidate_codes = strong_pool_codes | api_limit_codes

        # 强势池回收：DB 标记 in_strong_pool 但已不在选股 API 结果的「待退出」股，
        # 并入候选重抓 → 走入池判断(in_pool=code in strong_pool_codes=False)自动回收，
        # 同时补今日快照。否则它们永久滞留强势池(幽灵)、且 today_* 长期陈旧。
        # 仅在强势池 API 成功时执行（失败回退 DB 时 strong_pool_codes==db_pool_codes，无差集）。
        if api_pool_codes:
            retire_codes = db_pool_codes - strong_pool_codes
            if retire_codes:
                all_candidate_codes = all_candidate_codes | retire_codes
                log.info(f"强势池回收：并入 {len(retire_codes)} 只待退出股重抓并回收")

        # 涨跌停复核：当日快照已标涨跌停、但已不在 API 名单里的股票，并入候选重抓，
        # 以收盘价重算（解决盘中涨跌停、尾盘打开后因退出候选集而状态无法更新的问题）。
        if limit_api_ok:
            recheck_codes = {
                row[0]
                for row in db.query(Stock.code)
                .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
                .filter(
                    StockDailySnapshot.date == target_date,
                    or_(
                        StockDailySnapshot.is_limit_up == True,    # noqa: E712
                        StockDailySnapshot.is_limit_down == True,  # noqa: E712
                    ),
                    Stock.code.notin_(api_limit_codes),
                ).all()
            }
            if recheck_codes:
                all_candidate_codes = all_candidate_codes | recheck_codes
                log.info(f"涨跌停复核：并入 {len(recheck_codes)} 只（曾标记今已退出名单）重抓")

        # 从 DB 构建 StockBasicInfo（已知股票直接读库，未知股票创建 stub）
        known_stocks = {
            s.code: s
            for s in db.query(Stock).filter(Stock.code.in_(all_candidate_codes)).all()
        }

        # 刷新已知候选股的 name / is_st：名字由选股 API 直接带回（api_name_map），
        # 且关键词为「非ST」→ 出现在结果中即当日非 ST（摘帽/改名自动修正）。
        refreshed = 0
        for code, s in known_stocks.items():
            fresh = api_name_map.get(code)
            if fresh and (s.name != fresh or s.is_st):
                s.name = fresh
                s.is_st = False
                refreshed += 1
        if refreshed:
            log.info(f"  刷新股票名称/ST状态 {refreshed} 只（选股API）")

        # 仍缺名字的新代码（不在选股结果里，如复核股/极少数新股）才回退全市场列表补名
        new_codes = all_candidate_codes - set(known_stocks.keys())
        unnamed_new = {c for c in new_codes if c not in api_name_map}
        fallback_name_map: dict[str, str] = {}
        if unnamed_new:
            log.info(f"  {len(unnamed_new)} 只新代码不在选股结果，回退全市场列表补名...")
            try:
                fallback_name_map = {s.code: s.name for s in fetch_main_board_stocks()}
            except Exception as e:
                log.info(f"  全市场列表补名失败（忽略，用 code 占位）: {e}")

        candidates: List[StockBasicInfo] = []
        for code in all_candidate_codes:
            s = known_stocks.get(code)
            if s:
                candidates.append(StockBasicInfo(
                    code=s.code,
                    name=s.name,
                    market=1 if s.market == "SH" else 0,
                    is_st=s.is_st,
                    pct_change=0.0,
                    turnover_rate=0.0,
                    listing_date=getattr(s, "ipo_date", None),
                ))
            else:
                # 新股：按代码前缀推断市场；名称优先选股API，其次全市场列表，最后用 code 占位
                mkt = 1 if code.startswith(("6", "5", "9")) else 0
                real_name = api_name_map.get(code) or fallback_name_map.get(code) or code
                is_st_new = "ST" in real_name   # 选股结果非ST→False；fallback名字含ST则True
                stub = Stock(
                    code=code,
                    name=real_name,
                    market="SH" if mkt == 1 else "SZ",
                    is_st=is_st_new,
                    is_new_stock=False,
                )
                db.add(stub)
                db.flush()
                candidates.append(StockBasicInfo(
                    code=code, name=real_name, market=mkt,
                    is_st=is_st_new, pct_change=0.0, turnover_rate=0.0,
                ))
        db.commit()

        stock_map: Dict[str, StockBasicInfo] = {c.code: c for c in candidates}

        if not candidates:
            log.end(ok=False, detail="无候选股，退出")
            return _result()

        lu_cnt = len(api_limit_codes - strong_pool_codes)
        ld_cnt = 0   # API 不区分涨停/跌停，统计合并
        log.end(detail=(
            f"强势池 {len(strong_pool_codes)} 只，涨跌停 {len(api_limit_codes)} 只，"
            f"候选共 {len(candidates)} 只（去重后）"
        ))

        # 预取板块龙头 codes
        from app.models.sector import StockSectorRelation
        leader_code_set: set = {
            row[0]
            for row in db.query(Stock.code)
            .join(StockSectorRelation, StockSectorRelation.stock_id == Stock.id)
            .filter(StockSectorRelation.is_leader == True)  # noqa
            .all()
        }

        # ── 第3步：拉取 K 线 ─────────────────────────────────────
        log.begin("拉取K线数据")

        # 分组：DB 历史足够的只拉今日，其余拉完整 65 日
        db_klines_map, db_group, full_group = _build_klines_from_db(candidates, db, target_date)
        log.info(f"DB重建 {len(db_group)} 只（拉近2日），全量拉取 {len(full_group)} 只")

        # 全量拉取（新股 / 历史不足）：提高并发并去掉逐请求延迟。
        # 同一 K 线接口在 DB 重建组已用 20 并发/0 延迟稳定运行，这里取 15 留余量。
        full_klines = fetch_klines_batch(
            full_group, days=65, max_workers=15, delay_between=0.0,
        ) if full_group else {}

        # DB 重建组拉取天数：仅拉 2 天、payload 极小，可用更高并发（取 30 防限流）。
        # 边界2：按「DB 最新快照 → target_date」的最大缺口决定天数，避免连续停机多日后
        # days=2 只补 1 根、中间留空洞（MA60/连板数会偏差）。常态 gap=1 → 拉 3 天。
        db_fetch_days = 2
        if db_group:
            latest_hist = [bars[-1].date for bars in db_klines_map.values() if bars]
            if latest_hist:
                gap = (target_date - min(latest_hist)).days
                db_fetch_days = max(2, min(gap + 2, 65))
        if db_fetch_days > 3:
            log.info(f"  DB重建检测到缺口，拉取近 {db_fetch_days} 天补齐")
        today_klines = fetch_klines_batch(
            db_group, days=db_fetch_days, max_workers=30, delay_between=0.0,
        ) if db_group else {}

        # 合并：历史快照 + 新拉 bar，按日期并集去重（新数据覆盖同日历史并补齐缺口日）
        klines_map: dict = {}
        for info in full_group:
            klines_map[info.code] = full_klines.get(info.code, [])
        for info in db_group:
            hist_bars = db_klines_map[info.code]
            new_bars = today_klines.get(info.code, [])
            if not new_bars:
                klines_map[info.code] = hist_bars  # 今日无数据，降级用历史
                continue
            by_date = {b.date: b for b in hist_bars}
            for b in new_bars:
                by_date[b.date] = b                # 新数据覆盖/补齐缺口
            klines_map[info.code] = [by_date[d] for d in sorted(by_date)]

        fetched = sum(1 for v in klines_map.values() if v)
        failed = len(candidates) - fetched

        # 今日数据缺失检测（疑似限流）：
        #   full_group 拉空（bars 为空）；db_group 今日 bar 缺失（限流时会静默退回旧历史，
        #   不计入 failed，必须单独检测）。缺失比例偏高 → 标记 degraded，界面提示数据不完整。
        full_missing = sum(1 for info in full_group if not full_klines.get(info.code))
        db_today_missing = sum(1 for info in db_group if not today_klines.get(info.code))
        missing_today = full_missing + db_today_missing
        if candidates and missing_today / len(candidates) >= 0.1:
            api_warnings.append(
                f"K线今日数据缺失 {missing_today}/{len(candidates)} 只（疑似限流/接口异常），"
                f"今日涨跌停与评分可能不完整"
            )
            log.info(f"⚠️  今日数据缺失 {missing_today}/{len(candidates)} 只（full {full_missing} / db {db_today_missing}）")

        # target_date 跟随「最新一根 K 线」自动修正：用所有股票的最大日期，
        # 避免个别停牌股的陈旧末日 bar 把 target_date 误导到过去。
        all_latest = [bars[-1].date for bars in klines_map.values() if bars]
        if all_latest:
            kline_latest_date = max(all_latest)
            if kline_latest_date != target_date:
                log.info(f"⚠️  {target_date} 非交易日/无当日数据，自动修正为 {kline_latest_date}")
                target_date = kline_latest_date

        # 边界3：选股API数据日期须与（修正后的）target_date 一致，才以其为涨跌停权威。
        # 盘前等场景 API 可能返回另一交易日的数据，错配会把标志写到错误日期或误清对账。
        # 不一致时回退本地 K 线判定（不做权威覆盖/对账）。
        limit_dates = {d.get("limit_date") for d in api_limit_detail.values() if d.get("limit_date")}
        limit_authority_ok = limit_api_ok and (
            not limit_dates or (len(limit_dates) == 1 and next(iter(limit_dates)) == target_date)
        )
        if limit_api_ok and not limit_authority_ok:
            log.info(f"⚠️  选股API数据日期 {sorted(map(str, limit_dates))} ≠ target_date {target_date}，"
                     f"跳过涨跌停权威覆盖，回退本地K线判定")

        existing_snap_count = (
            db.query(StockDailySnapshot)
            .filter(StockDailySnapshot.date == target_date)
            .count()
        )
        if existing_snap_count > 0:
            log.info(f"ℹ️  {target_date} 已有 {existing_snap_count} 条快照（覆盖更新）")
        log.end(detail=f"成功 {fetched}/{len(candidates)} 只" + (f"，失败 {failed} 只" if failed else ""))

        # ── 第4步：计算指标 & 写入快照 ───────────────────────────
        log.begin("计算指标&写入快照")
        stats_list: List[StockWindowStats] = []
        for info in candidates:
            bars = klines_map.get(info.code, [])
            stats = compute_window_stats(
                code=info.code, name=info.name, is_st=info.is_st, bars=bars,
                new_stock_months=criteria.new_stock_months,
                listing_date=getattr(info, "listing_date", None),
                is_sector_leader=info.code in leader_code_set,
            )
            if stats:
                stats_list.append(stats)

        new_in_pool = removed_from_pool = total_in_pool = 0

        for stats in stats_list:
            # 入池判断：直接以选股 API 返回的代码集合为准
            in_pool = stats.code in strong_pool_codes
            info = stock_map.get(stats.code)
            if not info:
                continue
            was_in_pool = stats.code in db_pool_codes
            if in_pool and not was_in_pool:
                new_in_pool += 1
            elif not in_pool and was_in_pool:
                removed_from_pool += 1
            if in_pool:
                total_in_pool += 1
            stock = _upsert_stock(db, info, stats, in_pool)
            db.flush()
            # 涨跌停以选股 API 名单为权威来源：方向取 API 显式字段 limit_dir，
            # 缺失时回退 pct 符号。规避本地用前收价反推跌停价的脆弱逻辑
            # （脏前收→漏判 / 北交所阈值缺失）。仅在数据日期与 target_date 一致时生效。
            if limit_authority_ok:
                detail = api_limit_detail.get(stats.code)
                if detail:
                    d = detail["limit_dir"]
                    if d is None:   # API 未给方向 → 回退当日 pct 符号
                        pct = stats.today_pct_change or 0.0
                        d = "up" if pct > 0 else ("down" if pct < 0 else None)
                    auth_lu = d == "up"
                    auth_ld = d == "down"
                else:
                    auth_lu = auth_ld = False
                _upsert_snapshot(db, stock, stats, target_date,
                                 is_limit_up=auth_lu, is_limit_down=auth_ld)
            else:
                _upsert_snapshot(db, stock, stats, target_date)

        db.commit()
        log.end(detail=f"快照写入 {len(stats_list)} 只，强势池: +{new_in_pool}/-{removed_from_pool}，当前 {total_in_pool} 只")

        # ── 第4.05步：历史快照自举 ────────────────────────────────
        # full_group 这次全量拉到的 65 日 K 线，把历史日(< target_date)一并落库，
        # 使该股下次更新即可走 DB 重建（仅拉今日）——每只股票全量拉取一生只发生一次。
        # 历史快照仅存 K 线原始字段（close_price/pct/换手/涨跌停标志），供窗口重建用。
        if full_group:
            fg_codes = [info.code for info in full_group]
            sid_by_code = {
                row[0]: row[1]
                for row in db.query(Stock.code, Stock.id).filter(Stock.code.in_(fg_codes)).all()
            }
            sids = list(sid_by_code.values())
            existing_pairs = {
                (r[0], r[1])
                for r in db.query(StockDailySnapshot.stock_id, StockDailySnapshot.date)
                .filter(StockDailySnapshot.stock_id.in_(sids)).all()
            } if sids else set()
            backfilled = 0
            for info in full_group:
                sid = sid_by_code.get(info.code)
                if not sid:
                    continue
                for bar in klines_map.get(info.code, []):
                    if bar.date >= target_date or (bar.close_price or 0) <= 0:
                        continue
                    if (sid, bar.date) in existing_pairs:
                        continue
                    db.add(StockDailySnapshot(
                        stock_id=sid, date=bar.date,
                        close_price=round(bar.close_price, 4),
                        pct_change=round(bar.pct_change or 0.0, 4),
                        turnover_rate=round(bar.turnover_rate or 0.0, 4),
                        is_limit_up=bar.is_limit_up,
                        is_limit_down=bar.is_limit_down,
                        is_broken_board=bar.is_broken_board,
                    ))
                    existing_pairs.add((sid, bar.date))
                    backfilled += 1
            if backfilled:
                db.commit()
                log.info(f"历史快照自举：补录 {backfilled} 条（full_group {len(full_group)} 只，下次可走DB重建）")

        # ── 第4.1步：涨跌停对账 ──────────────────────────────────
        # 当日快照中仍标着涨跌停、但已不在选股 API 名单里的股票，强制清除标记。
        # 解决「盘中涨跌停、尾盘打开」的票因退出候选集而无法被后续更新修正的问题。
        # 仅在数据日期与 target_date 一致时执行，避免用错日期的名单误清。
        if limit_authority_ok:
            stale_snaps = (
                db.query(StockDailySnapshot)
                .join(Stock, Stock.id == StockDailySnapshot.stock_id)
                .filter(
                    StockDailySnapshot.date == target_date,
                    or_(
                        StockDailySnapshot.is_limit_up == True,    # noqa: E712
                        StockDailySnapshot.is_limit_down == True,  # noqa: E712
                    ),
                    Stock.code.notin_(api_limit_codes),
                )
                .all()
            )
            for snap in stale_snaps:
                snap.is_limit_up = False
                snap.is_limit_down = False
            if stale_snaps:
                db.commit()
                log.info(f"涨跌停对账：清除过期标记 {len(stale_snaps)} 只")

        # ── 第4.5步：补全涨跌停股板块关联 ────────────────────────
        # 对今日涨跌停但 stock_sector_relations 为空的股票，
        # 实时拉取东方财富板块归属并建立关联，确保涨停池展示正确的板块
        log.begin("补全涨跌停板块关联")
        limit_move_stocks = (
            db.query(Stock)
            .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
            .filter(
                StockDailySnapshot.date == target_date,
                or_(
                    StockDailySnapshot.is_limit_up == True,    # noqa: E712
                    StockDailySnapshot.is_limit_down == True,  # noqa: E712
                ),
            )
            .all()
        )
        created_rels = _sync_missing_sector_relations(db, limit_move_stocks, log=log)
        log.end(detail=f"涨跌停共 {len(limit_move_stocks)} 只，补建关联 {created_rels} 条")

        # ── 第5步：刷新板块统计 & 阶段 & 排名tag ────────────────
        log.begin("刷新板块统计")
        _refresh_sector_stats(db, target_date)
        refresh_sector_phases(db)
        _refresh_sector_ranks(db)
        sector_count = db.query(Sector).count()
        watched_count = db.query(Sector).filter(Sector.is_watched == True).count()  # noqa
        log.end(detail=f"共 {sector_count} 个板块，关注 {watched_count} 个")

        # ── 第6步：更新主板块 ────────────────────────────────────
        log.begin("更新主板块")
        _update_primary_sectors(db)
        log.end()

        # ── 第7步：写入 DailyReview ──────────────────────────────
        log.begin("写入DailyReview")
        _save_daily_review(db, target_date)
        review = db.query(DailyReview).filter(DailyReview.date == target_date).first()
        log.end(detail=(
            f"市场阶段={review.market_phase}，温度={review.emotional_temperature:.0f}，"
            f"仓位={review.suggested_position_level:.0f}%"
        ) if review else "写入成功")

        # ── 第8步：弱转强信号 ────────────────────────────────────
        log.begin("写入弱转强信号")
        _save_weak_to_strong_signals(db, target_date)
        sig_count = db.query(Signal).filter(Signal.date == target_date).count()
        log.end(detail=f"信号 {sig_count} 条")

        log.summary()

        # ── 基准指数日线同步（偏离值用，失败不影响主流程）──────────────────
        try:
            from app.services.deviation_service import sync_indices
            idx = sync_indices(db)
            log.info(f"基准指数日线同步：新增 {idx.get('count')} 条，ok={idx.get('ok')}")
            if not idx.get("ok"):
                api_warnings.append("基准指数日线 API 调用失败，偏离值预警可能不准")
        except Exception as e:
            log.info(f"[indices] 指数日线同步失败（不影响主流程）: {e}")
            db.rollback()

        # ── 重点监管名单同步（独立步骤，失败不影响主流程）──────────────────
        try:
            from app.services.regulatory_service import sync_regulatory_unusual
            reg = sync_regulatory_unusual(db)
            if reg.get("ok"):
                log.info(f"重点监管名单同步完成：{reg.get('count')} 条")
            else:
                api_warnings.append("重点监管名单 API 调用失败，已保留旧名单")
        except Exception as e:
            log.info(f"[regulatory] 重点监管名单同步失败（不影响主流程）: {e}")
            db.rollback()

    except Exception as e:
        import traceback
        log.error(str(e))
        log.info(traceback.format_exc())
        log.summary()
        db.rollback()
        raise
    finally:
        db.close()
        from app.database import engine
        engine.dispose()

    # ── 板块同步（独立步骤，失败不影响主流程）──────────────────────────
    if not skip_boards:
        try:
            from scripts.sync_boards import run_sync_boards
            run_sync_boards()
        except Exception as e:
            print(f"[sync_boards] 板块同步失败（不影响主流程）: {e}")

    return _result()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradeFlux 每日复盘更新")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="指定更新日期，格式 YYYY-MM-DD，默认为今天",
    )
    parser.add_argument(
        "--skip-boards",
        action="store_true",
        default=False,
        help="跳过东财概念板块同步（sync_boards.py），节省约10分钟",
    )
    args = parser.parse_args()

    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = date.today()

    run_daily_update(target, skip_boards=args.skip_boards)
