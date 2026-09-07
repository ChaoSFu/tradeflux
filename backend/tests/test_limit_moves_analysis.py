"""
涨跌停分析的两个跨日统计。

这两个接口最容易出的错不是算错，是**把"不知道"算成一种结果**：
停牌的票被算成断板、缺连板数的票被当成首板、拿不到日历就用快照日期反推。
每一条都会让晋级率好看一点或难看一点，而且从输出上完全看不出来。
"""
from datetime import date, timedelta

import pytest

from app.models.sector import Sector, StockSectorRelation
from app.models.stock import Stock, StockDailySnapshot
from app.services.limit_moves_analysis_service import (
    compute_advance_ladder, compute_sector_continuation,
)
from app.services.trading_calendar import _write_cache

D1, D2 = date(2026, 9, 3), date(2026, 9, 4)


@pytest.fixture(autouse=True)
def _cal(db):
    """真实交易日历。**没有它两个接口都该拒绝出数**，而不是拿快照日期凑。"""
    _write_cache(db, [date(2026, 9, i) for i in range(1, 8)])
    yield


def _stock(db, code):
    st = Stock(code=code, name=f"股{code}", market="SH")
    db.add(st); db.flush()
    return st


def _snap(db, st, d, *, lu=False, ld=False, board=None):
    db.add(StockDailySnapshot(stock_id=st.id, date=d, close_price=10.0,
                              is_limit_up=lu, is_limit_down=ld,
                              board_count=board, is_settled=True))


class TestAdvanceLadder:

    def test_晋级和断板分开数(self, db):
        a, b = _stock(db, "600001"), _stock(db, "600002")
        _snap(db, a, D1, lu=True, board=1); _snap(db, a, D2, lu=True, board=2)
        _snap(db, b, D1, lu=True, board=1); _snap(db, b, D2, lu=False, board=0)
        db.commit()
        r = compute_advance_ladder(db, D2)
        assert r["prev_date"] == D1
        row = r["rows"][0]
        assert (row["from_board"], row["to_board"]) == (1, 2)
        assert row["advanced_count"] == 1 and row["broken_count"] == 1
        assert row["advance_ratio"] == 0.5

    def test_今天没有快照的票既不算晋级也不算断板(self, db):
        """停牌 / 退市不是断板。并进 broken 会让晋级率无故变低。"""
        a, b = _stock(db, "600003"), _stock(db, "600004")
        _snap(db, a, D1, lu=True, board=2); _snap(db, a, D2, lu=True, board=3)
        _snap(db, b, D1, lu=True, board=2)          # 今天没有行
        db.commit()
        row = compute_advance_ladder(db, D2)["rows"][0]
        assert row["previous_count"] == 2
        assert row["unknown_count"] == 1 and row["broken_count"] == 0
        assert row["observed_count"] == 1
        assert row["advance_ratio"] == 1.0, "分母是今天观测到的那些，不是昨天的总数"

    def test_一只都没观测到时不给比率(self, db):
        a = _stock(db, "600005")
        _snap(db, a, D1, lu=True, board=1)
        db.commit()
        row = compute_advance_ladder(db, D2)["rows"][0]
        assert row["advance_ratio"] is None, "算不出就是算不出，不是 0%"

    def test_缺连板数的票不当成首板(self, db):
        a = _stock(db, "600006")
        _snap(db, a, D1, lu=True, board=None)
        _snap(db, a, D2, lu=True, board=None)
        db.commit()
        assert compute_advance_ladder(db, D2)["rows"] == [], \
            "不知道它昨天是几板，就不能替它选一个板位"

    def test_拿不到日历就不出数(self, db, monkeypatch):
        import app.services.limit_moves_analysis_service as mod
        monkeypatch.setattr(mod, "get_trading_days", lambda *a, **k: None)
        a = _stock(db, "600007")
        _snap(db, a, D1, lu=True, board=1); _snap(db, a, D2, lu=True, board=2)
        db.commit()
        r = compute_advance_ladder(db, D2)
        assert r["rows"] == [] and r["prev_date"] is None
        assert any("交易日历" in n for n in r["notes"]), \
            "用快照日期反推会把隔着开市日的两天判成相邻"

    def test_不产出接力分(self, db):
        a = _stock(db, "600008")
        _snap(db, a, D1, lu=True, board=1); _snap(db, a, D2, lu=True, board=2)
        db.commit()
        keys = set(compute_advance_ladder(db, D2)["rows"][0])
        assert not any("score" in k for k in keys), "多个事实加权成总分就是又一个黑箱"


class TestSectorContinuation:

    def _sector(self, db, name, *stocks):
        sec = Sector(code=f"BK{abs(hash(name)) % 9000 + 1000}", name=name,
                     is_watched=True)
        db.add(sec); db.flush()
        for st in stocks:
            db.add(StockSectorRelation(stock_id=st.id, sector_id=sec.id))
        return sec

    def test_延续新增炸板分开数(self, db):
        a, b, c = _stock(db, "600011"), _stock(db, "600012"), _stock(db, "600013")
        self._sector(db, "AI应用", a, b, c)
        _snap(db, a, D1, lu=True); _snap(db, a, D2, lu=True)      # 延续
        _snap(db, b, D1, lu=True); _snap(db, b, D2, lu=False)     # 断
        _snap(db, c, D1, lu=False); _snap(db, c, D2, lu=True)     # 新增
        db.commit()
        row = compute_sector_continuation(db, D2)["rows"][0]
        assert row["yesterday_limit_up_count"] == 2
        assert row["today_continued_limit_up_count"] == 1
        assert row["today_broken_count"] == 1
        assert row["today_new_limit_up_count"] == 1
        assert row["continuation_ratio"] == 0.5

    def test_昨日涨停今天没行的单独计(self, db):
        a = _stock(db, "600014")
        self._sector(db, "农林牧渔", a)
        _snap(db, a, D1, lu=True)
        db.commit()
        row = compute_sector_continuation(db, D2)["rows"][0]
        assert row["today_unknown_count"] == 1 and row["today_broken_count"] == 0
        assert row["continuation_ratio"] is None

    def test_一只票属于多个板块时各板块都算(self, db):
        """所以各行相加大于全市场涨停数——这件事必须写进 notes。"""
        a = _stock(db, "600015")
        self._sector(db, "PCB", a)
        self._sector(db, "消费电子", a)
        _snap(db, a, D1, lu=True); _snap(db, a, D2, lu=True)
        db.commit()
        r = compute_sector_continuation(db, D2)
        assert len(r["rows"]) == 2
        assert any("多个板块" in n for n in r["notes"])

    def test_没有关注板块时如实说(self, db):
        a = _stock(db, "600016")
        _snap(db, a, D1, lu=True); _snap(db, a, D2, lu=True)
        db.commit()
        r = compute_sector_continuation(db, D2)
        assert r["rows"] == [] and any("is_watched" in n for n in r["notes"])

    def test_不产出延续分(self, db):
        a = _stock(db, "600017")
        self._sector(db, "谷子经济", a)
        _snap(db, a, D1, lu=True); _snap(db, a, D2, lu=True)
        db.commit()
        keys = set(compute_sector_continuation(db, D2)["rows"][0])
        assert not any("score" in k for k in keys)
