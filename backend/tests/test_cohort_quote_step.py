"""
日更第 4.15 步：昨日群体里今天不在候选池的票，补抓当日行情（2026-09-11）。

只存给市场效应用，不写快照。行情日期不是今天的一律拒掉。
"""
import importlib.util
from datetime import date, datetime
from pathlib import Path

from app.models.market_effect import CohortOutcomeQuote
from app.models.stock import Stock, StockDailySnapshot
from app.services import market_effect_service as mes
from app.services.eastmoney_fetcher import SH_TZ, StockQuote

_SPEC = importlib.util.spec_from_file_location(
    "_du_quotes", Path(__file__).resolve().parents[1] / "scripts" / "daily_update.py")
du = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(du)

P, T = date(2026, 9, 10), date(2026, 9, 11)
AFTER_CLOSE = datetime(2026, 9, 11, 15, 30, tzinfo=SH_TZ)


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass


def _q(code, pct, d=T, price=9.5):
    return StockQuote(code=code, name=code, price=price, pct_change=pct, prev_close=10.0,
                      open=10.0, high=10.0, low=price, turnover_rate=None, trade_date=d)


def _seed(db):
    a = Stock(code="600001", name="留在池子里", market="SH")
    b = Stock(code="600002", name="掉出池子", market="SH")
    db.add_all([a, b]); db.flush()
    for st in (a, b):
        db.add(StockDailySnapshot(stock_id=st.id, date=P, is_limit_up=True, board_count=1,
                                  pct_change=10.0, close_price=10.0, is_settled=True))
    db.add(StockDailySnapshot(stock_id=a.id, date=T, is_limit_up=True, board_count=2,
                              pct_change=10.0, close_price=11.0, is_settled=True))
    db.flush()
    return a, b


def test_只补不在候选池的那几只_不写快照(db, monkeypatch):
    a, b = _seed(db)
    asked = []
    monkeypatch.setattr(du, "fetch_stock_quotes_batch",
                        lambda pairs: asked.append(list(pairs)) or {"600002": _q("600002", -5.0)})
    assert du._fetch_cohort_outcome_quotes(db, T, AFTER_CLOSE, _Log()) == (1, 1, 0)
    assert asked == [[("600002", 1)]], "留在池子里的那只今天已有快照，不该再问"
    q = db.query(CohortOutcomeQuote).one()
    assert (q.stock_id, q.pct_change, q.is_settled) == (b.id, -5.0, True)
    assert db.query(StockDailySnapshot).filter(StockDailySnapshot.date == T).count() == 1, \
        "不写快照：写了板块统计这类今天全市场的数字都会变"

    r = mes.compute_cohort_outcome(db, P, T, "limit_up")
    assert (r["valid_count"], r["quote_count"], r["median_pct_change"]) == (2, 1, 2.5)


def test_行情日期不是今天就拒掉(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(du, "fetch_stock_quotes_batch",
                        lambda pairs: {"600002": _q("600002", 3.0, d=P)})
    assert du._fetch_cohort_outcome_quotes(db, T, AFTER_CLOSE, _Log()) == (1, 0, 1)
    assert db.query(CohortOutcomeQuote).count() == 0, "拿昨天的行情冒充今天，比缺着更糟"


def test_都在池子里就不发请求(db, monkeypatch):
    a, b = _seed(db)
    db.add(StockDailySnapshot(stock_id=b.id, date=T, pct_change=1.0, close_price=10.1, is_settled=True))
    db.flush()
    asked = []
    monkeypatch.setattr(du, "fetch_stock_quotes_batch", lambda pairs: asked.append(1) or {})
    assert du._fetch_cohort_outcome_quotes(db, T, AFTER_CLOSE, _Log()) == (0, 0, 0)
    assert asked == []


def test_行情整体失败不抛(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(du, "fetch_stock_quotes_batch",
                        lambda pairs: (_ for _ in ()).throw(RuntimeError("腾讯挂了")))
    assert du._fetch_cohort_outcome_quotes(db, T, AFTER_CLOSE, _Log()) == (1, 0, 0)
