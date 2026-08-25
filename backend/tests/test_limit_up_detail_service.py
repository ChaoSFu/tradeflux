"""
涨停明细落库测试（2026-08-25新增，涨停板块雷达）。

重点是"同一交易日多次手动刷新"这个真实使用场景：用户可能 10:00、13:30、14:50
各刷一次，每次封单额/最终封板时间都会变，而且盘中打开的股票会从涨停池掉到炸板池。
"""
from datetime import date, datetime, time
from unittest.mock import patch

from app.models.stock import Stock
from app.models.limit_up_detail import LimitUpDailyDetail, BrokenBoardDailyDetail
from app.services.limit_up_detail_fetcher import LimitUpDetail, BrokenBoardDetail
from app.services.limit_up_detail_service import sync_limit_up_details, get_last_refreshed

TODAY = date(2026, 8, 25)
_PATCH = "app.services.limit_up_detail_service.fetch_limit_up_details"


def _lu(code="002821", name="凯莱英", **kw):
    base = dict(market=0, price=172.23, pct_change=10.0, seal_amount=1.58e8,
                board_count=1, first_limit_time=time(10, 15, 30),
                last_limit_time=time(10, 15, 30), broken_times=0)
    base.update(kw)
    return LimitUpDetail(code=code, name=name, **base)


def _bb(code="002176", name="江特电机", **kw):
    base = dict(market=0, pct_change=2.19, first_limit_time=time(9, 25), broken_times=1)
    base.update(kw)
    return BrokenBoardDetail(code=code, name=name, **base)


def _seed(db, code, name):
    s = Stock(code=code, name=name, market="SZ")
    db.add(s); db.flush()
    return s


def test_repeated_refresh_updates_in_place_without_duplicating_rows(db):
    """同股票同交易日多次刷新只能 UPDATE，不能新增重复行。"""
    _seed(db, "002821", "凯莱英")

    with patch(_PATCH, return_value=([_lu()], [], [])):
        sync_limit_up_details(db, TODAY)
    with patch(_PATCH, return_value=([_lu(seal_amount=2.4e8, last_limit_time=time(14, 30))], [], [])):
        sync_limit_up_details(db, TODAY)
    with patch(_PATCH, return_value=([_lu(seal_amount=3.1e8, last_limit_time=time(14, 55))], [], [])):
        sync_limit_up_details(db, TODAY)

    rows = db.query(LimitUpDailyDetail).filter(LimitUpDailyDetail.trade_date == TODAY).all()
    assert len(rows) == 1
    assert rows[0].seal_amount == 3.1e8            # 最后一次的值
    assert rows[0].last_limit_time == time(14, 55)
    assert rows[0].first_limit_time == time(10, 15, 30)   # 首封时间不受后续刷新影响


def test_refresh_records_provenance_so_page_can_show_freshness(db):
    _seed(db, "002821", "凯莱英")
    before = datetime.now()
    with patch(_PATCH, return_value=([_lu()], [], [])):
        sync_limit_up_details(db, TODAY)

    row = db.query(LimitUpDailyDetail).one()
    assert row.source == "eastmoney"
    assert row.source_trade_date == TODAY
    assert row.refreshed_at is not None and row.refreshed_at >= before.replace(microsecond=0)
    assert get_last_refreshed(db, TODAY) == row.refreshed_at


def test_stock_that_opens_intraday_moves_from_limit_up_to_broken_board(db):
    """
    盘中刷新的真实场景：一只 13:00 还封着的股票 14:30 炸板打开，会从涨停池消失、
    出现在炸板池。如果不清理上一次刷新留下的涨停行，页面会同时把它显示成"涨停"
    和"炸板"——自相矛盾。
    """
    _seed(db, "002821", "凯莱英")
    with patch(_PATCH, return_value=([_lu()], [], [])):
        sync_limit_up_details(db, TODAY)
    assert db.query(LimitUpDailyDetail).count() == 1

    with patch(_PATCH, return_value=([], [_bb(code="002821", name="凯莱英")], [])):
        sync_limit_up_details(db, TODAY)

    assert db.query(LimitUpDailyDetail).filter(LimitUpDailyDetail.trade_date == TODAY).count() == 0
    assert db.query(BrokenBoardDailyDetail).filter(BrokenBoardDailyDetail.trade_date == TODAY).count() == 1


def test_pruning_is_scoped_to_the_day_and_never_touches_history(db):
    _seed(db, "002821", "凯莱英")
    yesterday = date(2026, 8, 24)
    with patch(_PATCH, return_value=([_lu()], [], [])):
        sync_limit_up_details(db, yesterday)
    # 今天这只股票没涨停
    with patch(_PATCH, return_value=([_lu(code="600127", name="金健米业")], [], [])):
        sync_limit_up_details(db, TODAY)

    assert db.query(LimitUpDailyDetail).filter(LimitUpDailyDetail.trade_date == yesterday).count() == 1
    assert db.query(LimitUpDailyDetail).filter(LimitUpDailyDetail.trade_date == TODAY).count() == 1


def test_unknown_stock_gets_a_stub_instead_of_being_dropped(db):
    """
    涨停池是全市场的，完全可能出现从没进过候选池的股票。没有 stock_id 就整只丢掉，
    正是这个功能最不该发生的事——漏掉一只涨停股比多一行存根严重得多。
    """
    with patch(_PATCH, return_value=([_lu(code="920123", name="没见过的票", market=0)], [], [])):
        sync_limit_up_details(db, TODAY)

    stock = db.query(Stock).filter(Stock.code == "920123").one()
    assert stock.name == "没见过的票"      # name 是 NOT NULL，必须在 flush 前给上
    row = db.query(LimitUpDailyDetail).one()
    assert row.stock_id == stock.id and row.stock_code == "920123"


def test_missing_fields_stay_null_rather_than_becoming_zero(db):
    """封单额为0（无人排队）和"东财没给封单额"是两回事，页面要能区分。"""
    _seed(db, "002821", "凯莱英")
    bare = LimitUpDetail(code="002821", name="凯莱英")
    with patch(_PATCH, return_value=([bare], [], [])):
        sync_limit_up_details(db, TODAY)

    row = db.query(LimitUpDailyDetail).one()
    for f in ("seal_amount", "first_limit_time", "last_limit_time",
              "board_count", "broken_times", "price", "limit_reason"):
        assert getattr(row, f) is None, f


def test_fetch_warnings_are_passed_through(db):
    _seed(db, "002821", "凯莱英")
    with patch(_PATCH, return_value=([_lu()], [], ["炸板池拉取失败（TimeoutException），封板率本次无法计算"])):
        lu, bb, warnings = sync_limit_up_details(db, TODAY)
    assert lu == 1 and bb == 0
    assert any("炸板池" in w for w in warnings)
