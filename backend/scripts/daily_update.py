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
from datetime import date, datetime
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_
from app.database import SessionLocal, init_db
from app.models.stock import Stock, StockDailySnapshot
from app.models.sector import Sector, StockSectorRelation, SectorDailySnapshot
from app.models.review import DailyReview
from app.models.signal import Signal
from app.services.eastmoney_fetcher import (
    StockBasicInfo, KLineBar,
    fetch_main_board_stocks, fetch_klines_batch, get_limit_pct,
)
from app.services.screening_service import (
    StockWindowStats,
    compute_window_stats, evaluate_criteria, get_active_criteria,
)
from app.services.sector_phase_service import refresh_sector_phases


# ---------------------------------------------------------------------------
# 候选股选取策略
# ---------------------------------------------------------------------------

PCT_CANDIDATE_THRESHOLD = 7.0   # 今日涨幅超过此值视为潜在候选（可能是涨停）
MAX_OTHER_CANDIDATES    = 300   # 非涨跌停的高涨幅候选上限（防止过多 K 线请求）


def _limit_threshold(code: str, is_st: bool = False) -> float:
    """
    根据股票代码返回涨跌停检测阈值（留 0.5% 误差空间）。
    主板 ±10% → 9.5；科创板/创业板 ±20% → 19.5；ST ±5% → 4.5
    """
    lp = get_limit_pct(code, is_st)  # 精确值（4.95 / 9.90 / 19.90）
    return lp - 0.4                  # 留误差，统一减 0.4%


def _select_candidates(
    all_stocks: List[StockBasicInfo],
    strong_pool_codes: set,
    criteria_include_sh: bool,
    criteria_include_sz: bool,
    exclude_st: bool,
    db=None,
) -> List[StockBasicInfo]:
    """
    选取需要拉取 K 线的候选股：
    - 当前强势池中的股票（必须重新评估）
    - 今日涨停股票（pct_change >= 9.5%）：无数量上限，确保涨停池完整
    - 今日跌停股票（pct_change <= -9.5%）：无数量上限，确保跌停池完整
    - 其他高涨幅股票（7% ~ 9.5%）：上限 MAX_OTHER_CANDIDATES

    注：若主板列表因 API 限流不完整，强势池中缺失的股票从 DB 补充，
        确保每次更新都覆盖全部强势池股票。
    """
    in_pool = []
    found_pool_codes: set = set()
    limit_ups: List[StockBasicInfo] = []
    limit_downs: List[StockBasicInfo] = []
    other_candidates: List[StockBasicInfo] = []

    for s in all_stocks:
        # 市场过滤
        if s.market == 1 and not criteria_include_sh:
            continue
        if s.market == 0 and not criteria_include_sz:
            continue
        # ST 过滤
        if exclude_st and s.is_st:
            continue

        threshold = _limit_threshold(s.code, s.is_st)
        if s.code in strong_pool_codes:
            in_pool.append(s)
            found_pool_codes.add(s.code)
        elif s.pct_change >= threshold:
            limit_ups.append(s)
        elif s.pct_change <= -threshold:
            limit_downs.append(s)
        elif s.pct_change >= PCT_CANDIDATE_THRESHOLD:
            other_candidates.append(s)

    # 强势池中未被主板列表覆盖的股票，从 DB 补充
    # （防止东方财富 API 限流/TLS 指纹检测只返回首页 200 条，导致余下股票永远漏更新）
    missing_codes = strong_pool_codes - found_pool_codes
    if missing_codes and db is not None:
        print(f"  ⚠️  {len(missing_codes)} 只强势池股票不在主板列表（API限流），从DB补充: "
              f"{', '.join(sorted(missing_codes))}")
        db_stocks = db.query(Stock).filter(Stock.code.in_(missing_codes)).all()
        for s in db_stocks:
            in_pool.append(StockBasicInfo(
                code=s.code,
                name=s.name,
                market=1 if s.market == "SH" else 0,
                is_st=s.is_st,
                pct_change=0.0,
                turnover_rate=0.0,
            ))

    # 其他高涨幅候选按涨幅降序，限制数量（涨跌停不受此限制）
    other_candidates.sort(key=lambda x: x.pct_change, reverse=True)
    other_candidates = other_candidates[:MAX_OTHER_CANDIDATES]

    new_candidates = limit_ups + limit_downs + other_candidates
    print(
        f"  候选：强势池重评 {len(in_pool)} 只，"
        f"涨停 {len(limit_ups)} 只，跌停 {len(limit_downs)} 只，"
        f"其他高涨幅 {len(other_candidates)} 只"
    )
    return in_pool + new_candidates


# ---------------------------------------------------------------------------
# 股票入库（upsert）
# ---------------------------------------------------------------------------

def _upsert_stock(db, info: StockBasicInfo, stats: StockWindowStats, in_pool: bool) -> Stock:
    """更新或创建 Stock 记录"""
    stock = db.query(Stock).filter(Stock.code == info.code).first()
    if not stock:
        stock = Stock(code=info.code)
        db.add(stock)

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


def _upsert_snapshot(db, stock: Stock, stats: StockWindowStats, today: date) -> None:
    """写入今日快照（存在则更新，不存在则新建）"""
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

    snap.pct_change = stats.today_pct_change
    snap.turnover_rate = stats.today_turnover
    snap.is_limit_up = stats.today_is_limit_up
    snap.is_limit_down = stats.today_is_limit_down
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

def _refresh_sector_stats(db) -> None:
    """
    根据最新强势股数据重新计算板块统计指标。
    """
    sectors = db.query(Sector).all()
    for sector in sectors:
        relations = (
            db.query(StockSectorRelation)
            .filter(StockSectorRelation.sector_id == sector.id)
            .all()
        )
        stock_ids = [r.stock_id for r in relations]
        if not stock_ids:
            continue

        stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all()
        strong = [s for s in stocks if s.in_strong_pool]

        sector.strong_stock_count = len(strong)
        sector.limit_up_count = sum(
            1 for s in stocks
            if s.daily_snapshots and s.daily_snapshots[-1].is_limit_up
        )
        sector.limit_down_count = sum(
            1 for s in stocks
            if s.daily_snapshots and s.daily_snapshots[-1].is_limit_down
        )
        sector.board_height = max(
            (s.daily_snapshots[-1].board_count for s in strong if s.daily_snapshots),
            default=0,
        )
        sector.emotion_score = (
            sum(s.emotion_score for s in strong) / len(strong) if strong else 0.0
        )
        sector.risk_score = (
            sum(s.risk_score for s in strong) / len(strong) if strong else 0.0
        )
        sector.continuity_score = min(100.0, sector.board_height * 15.0 + len(strong) * 5.0)

        # 龙头股：强势池中 leader_score 最高的
        if strong:
            leader = max(strong, key=lambda s: s.leader_score)
            sector.leader_stock_id = leader.id

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

def run_daily_update(target_date: date, skip_boards: bool = False) -> None:
    print(f"\n{'='*55}")
    print(f"  TradeFlux 每日更新  {target_date}")
    print(f"{'='*55}")

    db = SessionLocal()
    try:
        # ── 0. 确保 DB 结构存在 ──────────────────────────────────
        init_db()

        # ── 1. 加载筛选条件 ──────────────────────────────────────
        criteria = get_active_criteria(db)
        if not criteria:
            print("❌ 未找到生效的筛选条件，请先运行 scripts/init_screening.py")
            return
        print(f"\n[筛选条件] {criteria.name}")
        print(f"  连板>={criteria.min_board_count_60d+1} | "
              f"60日涨停>={criteria.min_limit_up_days_60d+1} | "
              f"10日涨停>={criteria.min_limit_up_days_10d+1} | "
              f"20日涨幅前{criteria.top_pct_rank_20d}%")

        # ── 2. 获取今日主板股票列表 ──────────────────────────────
        print("\n[第1步] 拉取主板股票列表...")
        all_stocks = fetch_main_board_stocks()
        print(f"  主板总数: {len(all_stocks)} 只")

        stock_map: Dict[str, StockBasicInfo] = {s.code: s for s in all_stocks}

        # ── 3. 确定候选股 ────────────────────────────────────────
        strong_pool_codes = {
            s.code for s in db.query(Stock).filter(Stock.in_strong_pool == True).all()  # noqa
        }
        print(f"\n[第2步] 确定候选股（当前强势池: {len(strong_pool_codes)} 只）...")
        # ── 查询今日已写入涨/跌停标记的股票（防止盘中标记收盘后未被修正）──────
        today_flagged_rows = (
            db.query(Stock.code)
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
        previously_flagged_codes = {row[0] for row in today_flagged_rows}

        candidates = _select_candidates(
            all_stocks=all_stocks,
            strong_pool_codes=strong_pool_codes,
            criteria_include_sh=criteria.include_sh_main,
            criteria_include_sz=criteria.include_sz_main,
            exclude_st=criteria.exclude_st,
            db=db,
        )
        # 将 DB 补充的股票也加入 stock_map，确保后续处理不会因 code 缺失而跳过
        for c in candidates:
            if c.code not in stock_map:
                stock_map[c.code] = c

        # ── 补充今日曾标记涨/跌停但不在候选列表的股票（收盘回撤后修正标记）────
        candidate_codes = {c.code for c in candidates}
        missed_flagged = previously_flagged_codes - candidate_codes
        if missed_flagged:
            for code in missed_flagged:
                if code in stock_map:
                    candidates.append(stock_map[code])
            print(f"  ↩ 补充强制重评（涨跌停标记修正）: {len(missed_flagged)} 只 "
                  f"({', '.join(sorted(missed_flagged)[:5])}{'…' if len(missed_flagged) > 5 else ''})")

        if not candidates:
            print("  ⚠️  无候选股，退出")
            return

        # 预取板块龙头 codes（用于龙头分加成）
        from app.models.sector import StockSectorRelation
        leader_code_set: set = {
            row[0]
            for row in db.query(Stock.code)
            .join(StockSectorRelation, StockSectorRelation.stock_id == Stock.id)
            .filter(StockSectorRelation.is_leader == True)  # noqa
            .all()
        }

        # ── 4. 并发拉取 K 线 ─────────────────────────────────────
        print(f"\n[第3步] 拉取 {len(candidates)} 只股票 K 线（并发 5 线程，主力+腾讯备用）...")
        klines_map = fetch_klines_batch(candidates, days=65, max_workers=5)
        fetched = sum(1 for v in klines_map.values() if v)
        print(f"  成功: {fetched} 只，失败: {len(candidates) - fetched} 只")

        # ── 非交易日自动修正 ──────────────────────────────────────
        # K 线数据只到最后一个交易日；若 target_date 是非交易日（周末/节假日），
        # 快照和 DailyReview 会打上错误日期。取第一只有效 K 线的末尾日期修正。
        for _bars in klines_map.values():
            if _bars:
                kline_latest_date = _bars[-1].date
                if kline_latest_date != target_date:
                    print(
                        f"  ⚠️  目标日期 {target_date} 非交易日（或提前运行），"
                        f"自动修正为最近交易日 {kline_latest_date}"
                    )
                    target_date = kline_latest_date
                break

        # 修正后再检查：若该交易日已有快照，说明今日已跑过，幂等继续即可
        existing_snap_count = (
            db.query(StockDailySnapshot)
            .filter(StockDailySnapshot.date == target_date)
            .count()
        )
        if existing_snap_count > 0:
            print(f"  ℹ️  {target_date} 已有 {existing_snap_count} 条快照，本次为覆盖更新")

        # ── 5. 计算统计 & 排名 ────────────────────────────────────
        print("\n[第4步] 计算指标、评估入池条件...")
        stats_list: List[StockWindowStats] = []
        for info in candidates:
            bars = klines_map.get(info.code, [])
            stats = compute_window_stats(
                code=info.code,
                name=info.name,
                is_st=info.is_st,
                bars=bars,
                new_stock_months=criteria.new_stock_months,
                listing_date=getattr(info, "listing_date", None),
                is_sector_leader=info.code in leader_code_set,
            )
            if stats:
                stats_list.append(stats)

        # 全部候选的 20 日涨幅列表（用于百分位排名）
        all_pct_20d = [s.pct_change_20d for s in stats_list]

        # ── 6. 评估入池 & 写库 ────────────────────────────────────
        new_in_pool = 0
        removed_from_pool = 0
        total_in_pool = 0

        for stats in stats_list:
            in_pool = evaluate_criteria(stats, criteria, all_pct_20d)
            info = stock_map.get(stats.code)
            if not info:
                continue

            was_in_pool = stats.code in strong_pool_codes
            if in_pool and not was_in_pool:
                new_in_pool += 1
            elif not in_pool and was_in_pool:
                removed_from_pool += 1
            if in_pool:
                total_in_pool += 1

            stock = _upsert_stock(db, info, stats, in_pool)
            db.flush()  # 确保 stock.id 存在
            _upsert_snapshot(db, stock, stats, target_date)

        db.commit()
        print(f"  强势池: 新增 +{new_in_pool}，移除 -{removed_from_pool}，当前 {total_in_pool} 只")

        # ── 7. 刷新板块统计 & 阶段 ────────────────────────────────
        print("\n[第5步] 刷新板块统计与阶段...")
        _refresh_sector_stats(db)
        refresh_sector_phases(db)
        sector_count = db.query(Sector).count()
        print(f"  已更新 {sector_count} 个板块")

        # ── 8. 更新主板块（必须在板块统计刷新后）─────────────────
        print("\n[第6步] 更新股票主板块...")
        _update_primary_sectors(db)

        # ── 9. 写入每日 Review ────────────────────────────────────
        print("\n[第7步] 保存每日市场状态...")
        _save_daily_review(db, target_date)

        # ── 10. 写入弱转强信号 ────────────────────────────────────
        print("\n[第8步] 写入弱转强信号...")
        _save_weak_to_strong_signals(db, target_date)

        # ── 11. 摘要 ─────────────────────────────────────────────
        review = db.query(DailyReview).filter(DailyReview.date == target_date).first()
        if review:
            print(f"\n{'─'*55}")
            print(f"  日期: {target_date}")
            print(f"  市场阶段: {review.market_phase}")
            print(f"  情绪温度: {review.emotional_temperature}")
            print(f"  建议仓位: {review.suggested_position_level}%")
            print(f"  强势池股票: {total_in_pool} 只")
            print(f"{'─'*55}")

        print("\n✅ 每日更新完成\n")

    except Exception as e:
        db.rollback()
        print(f"\n❌ 更新失败: {e}")
        raise
    finally:
        db.close()
        from app.database import engine
        engine.dispose()

    # ── 板块同步（独立步骤，失败不影响主流程）──────────────────────────
    if not skip_boards:
        print("\n[附加] 同步东财概念板块...")
        try:
            from scripts.sync_boards import run_sync_boards
            run_sync_boards()
        except Exception as e:
            print(f"  ⚠️  板块同步失败（不影响主流程）: {e}")


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
