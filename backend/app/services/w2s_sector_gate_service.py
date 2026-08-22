"""
弱转强雷达 Sector Gate：板块强度/动量打分 + 7 分类。

核心评分函数是纯函数（只读传入的 Sector/SectorDailySnapshot 对象属性，不开
DB session），方便单测直接构造轻量对象调用，不需要真实数据库。DB 相关的
"查昨天快照"/"写今天快照"逻辑放在薄封装函数里，跟纯函数分开。
"""
from __future__ import annotations

from datetime import date as date_cls
from typing import Optional

from sqlalchemy.orm import Session

from ..models.sector import Sector, SectorDailySnapshot
from . import w2s_config_service as cfg

# 7 分类
NEW_START = "NEW_START"
EXPANDING = "EXPANDING"
MAIN_UPTREND = "MAIN_UPTREND"
HEALTHY_DIVERGENCE = "HEALTHY_DIVERGENCE"
HIGH_LEVEL_WARNING = "HIGH_LEVEL_WARNING"
DECLINING = "DECLINING"
DEAD = "DEAD"


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _rank_score(r: Optional[int]) -> float:
    """dense rank 1-5（Sector.rank_5d 等，None=未上榜）→ 0-100 分。rank1=100 ... rank5=20。"""
    if r is None or r < 1:
        return 0.0
    return _clamp((6 - r) * 20)


def compute_sector_strength_score(sector: Sector) -> float:
    """纯函数：板块强度分（0-100），只用 sector 上的排名 tag + 当前活跃度字段。"""
    rank_score = (
        0.35 * _rank_score(sector.rank_5d)
        + 0.25 * _rank_score(sector.rank_10d)
        + 0.15 * _rank_score(sector.rank_20d)
        + 0.10 * _rank_score(sector.rank_lu)
        + 0.15 * _rank_score(sector.rank_board)
    )
    activity_score = _clamp(
        (sector.board_height or 0) * 12
        + (sector.limit_up_count or 0) * 6
        + (sector.strong_stock_count or 0) * 4
    )
    return round(_clamp(0.6 * rank_score + 0.4 * activity_score), 1)


def compute_sector_momentum_score(sector: Sector, prev: Optional[SectorDailySnapshot]) -> Optional[float]:
    """
    纯函数：板块动量分（0-100，50=持平）。prev=None（比如刚上线、还没有"昨天"
    快照）时返回 None，不用一个看起来像真实分数的 50 冒充"算出了持平"——
    调用方/展示层应该显示"数据积累中"，而不是把 None 悄悄当成 50 参与排序
    或判断（2026-08-22 修订：此前固定返回50.0，容易被误读成真实分数）。
    """
    if prev is None:
        return None
    amount_chg_pct = ((sector.amount or 0) - (prev.amount or 0)) / max(prev.amount or 0, 1) * 100
    momentum_raw = (
        (sector.pct_change_30d or 0) * 2.0  # 该字段实际是"今日涨幅"，命名是历史遗留
        + ((sector.limit_up_count or 0) - (prev.limit_up_count or 0)) * 8
        + ((sector.board_height or 0) - (prev.board_height or 0)) * 10
        + ((sector.emotion_score or 0) - (prev.emotion_score or 0)) * 0.5
        + ((sector.strong_stock_count or 0) - (prev.strong_stock_count or 0)) * 4
        + amount_chg_pct * 0.3
    )
    return round(_clamp(50 + momentum_raw), 1)


def classify_sector_category(
    sector: Sector, prev: Optional[SectorDailySnapshot], divergence_health_threshold: float = 50.0,
) -> str:
    """
    纯函数：7 分类。phase 0/1/2/3/5/6 直接映射；phase=4（原有"分歧"阶段）按
    健康度公式再细分 HEALTHY_DIVERGENCE / HIGH_LEVEL_WARNING。
    """
    phase = sector.phase or 0
    if phase in (0, 1):
        return NEW_START
    if phase == 2:
        return EXPANDING
    if phase == 3:
        return MAIN_UPTREND
    if phase == 5:
        return DECLINING
    if phase == 6:
        return DEAD
    # phase == 4：分歧健康度
    if prev is None:
        return HEALTHY_DIVERGENCE  # 无历史对比基准时不主动判负面，保守放行
    health = (
        100
        - ((prev.board_height or 0) - (sector.board_height or 0)) * 15
        - ((sector.risk_score or 0) - (prev.risk_score or 0)) * 0.6
        + ((sector.emotion_score or 0) - 50) * 0.4
        + min(0, (sector.limit_up_count or 0) - (prev.limit_up_count or 0)) * 5
    )
    return HEALTHY_DIVERGENCE if _clamp(health) >= divergence_health_threshold else HIGH_LEVEL_WARNING


def get_prev_snapshot(db: Session, sector_id: int, before: date_cls) -> Optional[SectorDailySnapshot]:
    """最近一条早于 before 的板块快照（"昨天"，实际是最近一条历史记录，跳过节假日/停牌空档）。"""
    return (
        db.query(SectorDailySnapshot)
        .filter(SectorDailySnapshot.sector_id == sector_id, SectorDailySnapshot.date < before)
        .order_by(SectorDailySnapshot.date.desc())
        .first()
    )


def score_sector(db: Session, sector: Sector, today: date_cls) -> dict:
    """薄封装：查 prev 快照 + 调纯函数，供 w2s_candidate_service/w2s_refresh_service 直接用。"""
    prev = get_prev_snapshot(db, sector.id, today)
    threshold = cfg.get_numeric(db, cfg.KEY_DIVERGENCE_HEALTH_THRESHOLD)
    return {
        "sector_strength_score": compute_sector_strength_score(sector),
        "sector_momentum_score": compute_sector_momentum_score(sector, prev),
        "sector_category": classify_sector_category(sector, prev, threshold),
    }


def upsert_sector_daily_snapshot(db: Session, today: date_cls) -> int:
    """
    把当日 Sector 的关键字段落一条 SectorDailySnapshot（这张表此前从未被生产
    流程写过，daily_update 板块刷新步骤之后调这个函数，把它从死表变活表，
    是弱转强雷达 Sector Momentum Score 能算出来的前提）。只对 is_watched 板块写，
    幂等 upsert（同一天重跑覆盖，不重复插入）。返回写入的行数。
    """
    sectors = db.query(Sector).filter(Sector.is_watched == True).all()  # noqa: E712
    existing = {
        s.sector_id: s
        for s in db.query(SectorDailySnapshot).filter(SectorDailySnapshot.date == today).all()
    }
    count = 0
    for sector in sectors:
        snap = existing.get(sector.id)
        if snap is None:
            snap = SectorDailySnapshot(sector_id=sector.id, date=today)
            db.add(snap)
        snap.phase = sector.phase or 0
        snap.strong_stock_count = sector.strong_stock_count or 0
        snap.limit_up_count = sector.limit_up_count or 0
        snap.board_height = sector.board_height or 0
        snap.continuity_score = sector.continuity_score or 0.0
        snap.risk_score = sector.risk_score or 0.0
        snap.emotion_score = sector.emotion_score or 0.0
        snap.amount = sector.amount or 0.0
        snap.leader_stock_id = sector.leader_stock_id
        count += 1
    db.commit()
    return count
