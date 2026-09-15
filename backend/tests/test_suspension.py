"""
停牌兼容（2026-09-15）。龙版传媒 605577 09-09~09-11 停牌引出的一串问题：

  · 收盘后行情兜底：停牌股的现价是昨收、成交量 0，被写成三根「涨跌 0」的假 bar
  · 连板计数：停牌夹在连板中间时，按市场日历判相邻会把一段连板切成两段
  · 龙头周期：停牌日被当成交易日计入「断板后第几天」「停留几天」
  · 交易日历：fuyao 漏过 09-04，所有跨过那天的连板被切断
"""
from datetime import date, timedelta
from types import SimpleNamespace

from app.models.leader_cycle import LeaderCycleSnapshot
from app.models.market_index import IndexDailySnapshot
from app.models.stock import Stock, StockDailySnapshot, StockSuspensionDay
from app.services import data_audit_service as audit
from app.services.eastmoney_fetcher import (
    KLineBar, StockQuote, kline_bar_from_quote, max_board_in_window,
)
from app.services.leader_cycle_service import identify_leader_cycle
from app.services.suspension_service import (
    convert_zero_volume_rows, load_suspensions_by_code, record_suspensions,
    stock_calendar, suspended_days_from_rows,
)
from app.services.trading_calendar import _write_cache, get_trading_days

CAL = [date(2026, 8, 31) + timedelta(days=i) for i in range(12)]   # 当作连续的交易日
D9 = date(2026, 9, 9)


def _quote(**kw):
    base = dict(code="605577", name="", price=18.67, pct_change=0.0, open=18.67, high=18.67,
                low=18.67, prev_close=18.67, volume=0.0, amount=0.0, turnover_rate=None,
                trade_date=D9)
    base.update(kw)
    return StockQuote(**base)


def _bar(d, lu):
    return KLineBar(date=d, open_price=10.0, close_price=10.0, high_price=10.0, low_price=10.0,
                    pct_change=10.0 if lu else 1.0, turnover_rate=None, is_limit_up=lu,
                    is_limit_down=False, is_broken_board=False, is_one_word_limit_up=False,
                    is_one_word_limit_down=False)


# ── 不再写假 bar ─────────────────────────────────────────────────────────────

def test_停牌股的行情_零成交不补当日bar():
    assert kline_bar_from_quote(_quote(), "605577", False, D9) is None
    bar = kline_bar_from_quote(_quote(volume=1e6, price=19.0, pct_change=1.77), "605577", False, D9)
    assert bar is not None and bar.close_price == 19.0


def test_行情兜底_零成交的记成停牌候选_不补bar(monkeypatch):
    import scripts.daily_update as du
    monkeypatch.setattr(du, "fetch_stock_quotes_batch", lambda cms: {
        "605577": _quote(),
        "600001": _quote(code="600001", volume=5e6, price=10.5, prev_close=10.0, pct_change=5.0),
    })
    infos = [SimpleNamespace(code="605577", market=1, is_st=False),
             SimpleNamespace(code="600001", market=1, is_st=False)]
    km = {"605577": [], "600001": []}
    suspended = set()
    rep, rej = du._repair_today_bar_from_quotes(
        infos, km, D9, SimpleNamespace(info=lambda *a, **k: None), suspended=suspended)
    assert (rep, rej) == (1, 0)
    assert suspended == {"605577"}
    assert km["605577"] == [] and km["600001"][-1].date == D9


# ── 停牌日从哪来、怎么存 ─────────────────────────────────────────────────────

def test_权威日K源停牌期间没有行():
    m = CAL[:8]
    out = suspended_days_from_rows(
        {"A": [m[0], m[1], m[4], m[5], m[6], m[7]], "B": [m[2], m[3]], "C": []}, m)
    assert out["A"] == [m[2], m[3]]
    assert out["B"] == m[4:], "最后一根之后缺的也是停牌（至今没复牌）"
    assert "C" not in out and m[0] not in out["B"], "第一根之前的不算"


def test_记停牌幂等_按代码读回_换算个股日历(db):
    s = Stock(code="605577", name="龙版传媒")
    db.add(s)
    db.flush()
    assert record_suspensions(db, [(s.id, CAL[2]), (s.id, CAL[3])], "quote") == 2
    assert record_suspensions(db, [(s.id, CAL[3]), (s.id, CAL[4])], "dump") == 1
    db.commit()
    got = load_suspensions_by_code(db, ["605577"])
    assert got == {"605577": {CAL[2], CAL[3], CAL[4]}}
    assert stock_calendar(CAL[:6], got["605577"]) == [CAL[0], CAL[1], CAL[5]]
    assert stock_calendar(None, got["605577"]) is None
    assert stock_calendar(CAL[:3], None) == CAL[:3]
    assert stock_calendar(CAL[:6], got["605577"], traded=[CAL[3]]) == [CAL[0], CAL[1], CAL[3], CAL[5]], \
        "那天有成交（手上有它的 K 线），停牌记录一定是错的——不能把它从日历里抠掉"


# ── 连板和龙头周期按个股日历算 ───────────────────────────────────────────────

def test_停牌夹在连板中间_按个股日历连板接着数():
    bars = [_bar(CAL[0], True), _bar(CAL[1], True), _bar(CAL[4], True), _bar(CAL[5], True),
            _bar(CAL[6], False)]                                        # CAL[2]、CAL[3] 停牌
    assert max_board_in_window(bars, 60, calendar=CAL)[0] == 2, "按市场日历：停牌那两天当缺口"
    assert max_board_in_window(bars, 60, calendar=stock_calendar(CAL, [CAL[2], CAL[3]]))[0] == 4


def test_龙头周期_停牌不切断连板_也不算数据缺口():
    bars = [_bar(CAL[0], True), _bar(CAL[1], True), _bar(CAL[4], True), _bar(CAL[5], True),
            _bar(CAL[6], False), _bar(CAL[7], False)]
    assert identify_leader_cycle(bars, trading_days=CAL[:8]) is None, "不告诉它停牌：两段 2 板"
    c = identify_leader_cycle(bars, trading_days=CAL[:8], suspended_days=[CAL[2], CAL[3]])
    assert (c.peak_board_count, c.cycle_start_date, c.break_date) == (4, CAL[0], CAL[6])
    assert (c.suspended_days, c.missing_days, c.peak_board_confident) == (2, 0, True)
    assert c.days_since_break == 1


def test_状态机_停留天数按个股日历_停牌日不算():
    from tests.test_leader_cycle_state_machine import CAL as SM_CAL, D0, Row
    from app.services.leader_cycle_state_service import BROKEN, replay_price_lifecycle
    brk = D0 + timedelta(days=4)
    rows = [Row(3, 12.0, break_date=None, days_since_break=None),
            Row(4, 11.0, ma5=11.5, break_date=brk, days_since_break=0),
            Row(8, 10.5, ma5=11.0, break_date=brk, days_since_break=1)]   # 第 5~7 天停牌，没有行
    as_of = D0 + timedelta(days=8)
    naive = replay_price_lifecycle(rows, as_of, trading_days=SM_CAL)
    own = replay_price_lifecycle(rows, as_of, trading_days=stock_calendar(
        SM_CAL, [D0 + timedelta(days=i) for i in (5, 6, 7)]))
    assert naive.state == own.state == BROKEN
    assert (naive.days_in_state, own.days_in_state) == (4, 1), "复牌那天是断板后第 1 个交易日"


# ── 交易日历 ─────────────────────────────────────────────────────────────────

def test_交易日历漏掉的交易日_用上证日线补上(db):
    days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 7)]
    _write_cache(db, days)
    for d in days + [date(2026, 9, 4), date(2026, 9, 8)]:
        db.add(IndexDailySnapshot(index_code="000001", date=d, close=3000.0))
    db.commit()
    got = get_trading_days(db)
    assert date(2026, 9, 4) in got, "fuyao 漏掉的 09-04 补回来"
    assert got[-1] == date(2026, 9, 7), "只补日历范围里的，不往后延伸（要不要去拉新日历照旧由缓存决定）"


# ── 已有的假行：数据体检转成停牌记录 ──────────────────────────────────────────

def test_体检_零成交假行转成停牌记录_同日龙头周期快照一起删(db):
    s = Stock(code="605577", name="龙版传媒")
    db.add(s)
    db.flush()
    db.add_all([
        StockDailySnapshot(stock_id=s.id, date=date(2026, 9, 8), close_price=18.67,
                           pct_change=9.12, volume=66879600.0),
        StockDailySnapshot(stock_id=s.id, date=D9, close_price=18.67, pct_change=0.0, volume=0.0),
        # 加列之前写的老行，量是「不知道」，不算
        StockDailySnapshot(stock_id=s.id, date=date(2026, 9, 10), close_price=18.67,
                           pct_change=0.0, volume=None),
        LeaderCycleSnapshot(stock_id=s.id, stock_code="605577", date=D9),
    ])
    db.commit()
    chk = audit._BY_ID["suspension_rows"]
    c = chk.fn(SimpleNamespace(db=db), chk)
    assert c["status"] == audit.GAP and c["counts"] == {"rows": 1, "stocks": 1}
    assert c["items"][0]["missing_dates"] == ["2026-09-09"]

    assert convert_zero_volume_rows(db)["deleted"] == 0, "试跑不写库"
    r = convert_zero_volume_rows(db, apply=True)
    assert (r["recorded"], r["deleted"], r["cycle_rows_deleted"]) == (1, 1, 1)
    assert db.query(StockDailySnapshot).filter_by(stock_id=s.id).count() == 2
    assert db.query(StockSuspensionDay).filter_by(stock_id=s.id, date=D9,
                                                  source="zero_volume_row").count() == 1
    assert chk.fn(SimpleNamespace(db=db), chk)["status"] == audit.OK
