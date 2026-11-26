"""顶栏「强势股涨跌停」的历史序列（2026-11-26）：/market-state/strong-pool-limits。"""
from datetime import date

from app.database import Base
from app.models.review import DailyReview
from app.services.market_state_service import get_strong_pool_limit_history


def _review(d, up, down):
    return DailyReview(date=d, overall_limit_up_count=up, overall_limit_down_count=down)


def test_ascending_and_limited_to_most_recent(db):
    Base.metadata.create_all(db.get_bind(), tables=[DailyReview.__table__])
    db.add_all([
        _review(date(2026, 11, 20), 5, 1),
        _review(date(2026, 11, 24), 6, 3),
        _review(date(2026, 11, 21), 4, 0),
        _review(date(2026, 11, 25), 8, 4),
    ])
    db.commit()
    pts = get_strong_pool_limit_history(db, days=3)
    # 取最近 3 天，日期升序（最后一个是最新那天）
    assert [p.date for p in pts] == [date(2026, 11, 21), date(2026, 11, 24), date(2026, 11, 25)]
    assert [(p.limit_up_count, p.limit_down_count) for p in pts] == [(4, 0), (6, 3), (8, 4)]


def test_rows_without_counts_are_skipped_not_zero(db):
    """早期行没有这两列：跳过，不当 0 只（当 0 会把均值拉低、今天的倍数虚高）"""
    Base.metadata.create_all(db.get_bind(), tables=[DailyReview.__table__])
    db.add_all([
        _review(date(2026, 9, 1), None, None),
        _review(date(2026, 9, 2), 3, None),
        _review(date(2026, 9, 3), 2, 1),
    ])
    db.commit()
    pts = get_strong_pool_limit_history(db, days=30)
    assert [p.date for p in pts] == [date(2026, 9, 3)]
