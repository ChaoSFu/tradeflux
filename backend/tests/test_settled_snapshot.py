"""
盘中价不得冒充收盘价（2026-08-26 生产事故的回归测试）。

事故复盘：daily_update 可被 UI 随时手动触发，用户当天盘中点了 9 次（09:46~14:00）。
腾讯K线接口盘中就发当日那根未收盘的 bar，`bar.date == today` 成立，于是每次都把
当时的现价写成了"今日收盘价"；收盘后 15:30 那一跑对 19% 的股票K线拉取失败，走
"保留上次可信值"分支，14:00 的盘中价就地转正。600984 因此在库里是
close=5.43/+9.92%（盘中封板那一刻），实际收盘 4.66/-5.67% 是炸板大阴线。
"""
import importlib.util
from datetime import date, datetime
from pathlib import Path

from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import bar_is_settled, SH_TZ
from app.services.screening_service import StockWindowStats

_SPEC = importlib.util.spec_from_file_location(
    "_du_settled", Path(__file__).resolve().parents[1] / "scripts" / "daily_update.py"
)
du = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(du)

TODAY = date(2026, 8, 26)
YESTERDAY = date(2026, 8, 25)


def _stats(close, pct):
    """600984 建设机械：盘中封板 5.43/+9.92%，实际收盘 4.66/-5.67% 炸板。"""
    return StockWindowStats(
        code="600984", name="建设机械", is_st=False, is_new_stock=False, trading_days=65,
        today_bar_date=TODAY,
        today_close_price=close, today_pct_change=pct, today_turnover=None,
        today_is_limit_up=False, today_is_limit_down=False, today_is_broken_board=False,
        today_is_one_word_limit_up=False, today_is_one_word_limit_down=False,
        board_count_current=0, limit_down_count_current=0,
        board_count_60d=0, board_down_count_60d=0,
        limit_up_days_60d=0, limit_up_days_20d=0, limit_up_days_10d=0,
        pct_change_60d=0.0, pct_change_20d=0.0, pct_change_10d=0.0,
        phase="normal", emotion_score=50.0, risk_score=50.0, leader_score=50.0,
        ma30=4.2, ma60=4.0, consecutive_declines=0,
    )


def _stock(db):
    st = Stock(code="600984", name="建设机械", market="SH")
    db.add(st); db.flush()
    return st


class TestBarIsSettled:
    """判定"这个交易日收盘了没有"。光看 bar.date == today 是分不出来的。"""

    def test_盘中当日bar未收盘(self):
        assert bar_is_settled(TODAY, datetime(2026, 8, 26, 14, 0, 55, tzinfo=SH_TZ)) is False

    def test_盘后当日bar已收盘(self):
        assert bar_is_settled(TODAY, datetime(2026, 8, 26, 15, 30, tzinfo=SH_TZ)) is True

    def test_整点15点算已收盘(self):
        assert bar_is_settled(TODAY, datetime(2026, 8, 26, 15, 0, tzinfo=SH_TZ)) is True

    def test_历史交易日必然已收盘(self):
        # 盘前 09:27 那一跑写的是上一交易日，不该被当成"盘中"
        assert bar_is_settled(YESTERDAY, datetime(2026, 8, 26, 9, 27, tzinfo=SH_TZ)) is True

    def test_盘前想写当日则未收盘(self):
        assert bar_is_settled(TODAY, datetime(2026, 8, 26, 9, 27, tzinfo=SH_TZ)) is False

    def test_未来日期防御性判否(self):
        assert bar_is_settled(date(2026, 8, 27), datetime(2026, 8, 26, 16, 0, tzinfo=SH_TZ)) is False


class TestUpsertSnapshotSettled:
    """_upsert_snapshot 对 is_settled 的处理——事故的直接现场。"""

    def test_盘中写入标记为未结算(self, db):
        stock = _stock(db)
        du._upsert_snapshot(db, stock, _stats(5.43, 9.92), TODAY,
                         settled=False)
        db.flush()
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price == 5.43
        assert snap.is_settled is False, "盘中写入必须标记 is_settled=False"

    def test_收盘后拿到真数据则覆盖并标记结算(self, db):
        stock = _stock(db)
        du._upsert_snapshot(db, stock, _stats(5.43, 9.92), TODAY, settled=False)
        db.flush()
        du._upsert_snapshot(db, stock, _stats(4.66, -5.67), TODAY, settled=True)
        db.flush()
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price == 4.66, "收盘价必须覆盖盘中价"
        assert snap.pct_change == -5.67
        assert snap.is_settled is True

    def test_收盘后拉取失败必须清掉盘中价而不是留着(self, db):
        """事故的核心分支。旧代码在这里什么都不做，日志还写着"保留上次可信值"。"""
        stock = _stock(db)
        du._upsert_snapshot(db, stock, _stats(5.43, 9.92), TODAY, settled=False)
        db.flush()
        # 收盘后这一跑：K线拉取失败 → close_pct_fresh=False，但本次是已收盘的跑
        du._upsert_snapshot(db, stock, _stats(None, None), TODAY,
                         close_pct_fresh=False, turnover_fresh=False, derived_fresh=False,
                         settled=True)
        db.flush()
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price is None, "盘中价不能就地转正成收盘价，宁可缺失"
        assert snap.pct_change is None
        assert snap.is_settled is False

    def test_已结算的旧值在拉取失败时才真的保留(self, db):
        """"保留上次可信值"只有在上次确实是收盘后终值时才成立。"""
        stock = _stock(db)
        du._upsert_snapshot(db, stock, _stats(4.66, -5.67), TODAY, settled=True)
        db.flush()
        du._upsert_snapshot(db, stock, _stats(None, None), TODAY,
                         close_pct_fresh=False, turnover_fresh=False, derived_fresh=False,
                         settled=True)
        db.flush()
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price == 4.66, "上次是收盘后终值，这次拉取失败应当保留"
        assert snap.is_settled is True

    def test_盘中跑不会清掉已结算的历史值(self, db):
        """盘中跑上一交易日的行时，绝不能把昨天的终值清掉。"""
        stock = _stock(db)
        du._upsert_snapshot(db, stock, _stats(4.94, 10.02), YESTERDAY, settled=True)
        db.flush()
        du._upsert_snapshot(db, stock, _stats(None, None), YESTERDAY,
                         close_pct_fresh=False, turnover_fresh=False, derived_fresh=False,
                         settled=False)
        db.flush()
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price == 4.94
        assert snap.is_settled is True
