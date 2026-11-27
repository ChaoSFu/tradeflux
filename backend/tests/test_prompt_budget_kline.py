"""
2026-11-27：被封的主要是取 K 线的接口；prompt 是批量接口，一次任务 20 次以内（用户 2026-11-26 定）。

  ① 选股 prompt 每次任务的调用上限：满了这一问不发、抛 PromptError 的子类（调用方照「没拿到」走兜底）
  ② fuyao dump 没接上的股票：只差当日那根的不再逐只拉 K 线；真缺历史的按日期问 prompt；补不上的才逐只拉
  ③ 涨停雷达刷新算分：库里历史 + prompt 问当天那根，历史不够的才逐只拉 K 线（有上限）
外部接口一律换成假的，不碰网络。
"""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.models.stock import Stock, StockDailySnapshot
from app.services import prompt_api as P
from app.services.eastmoney_fetcher import build_kline_bar

D3 = date(2026, 11, 25)


def _weekdays(a: date, b: date):
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


CAL = _weekdays(date(2026, 8, 1), D3)


def dated_item(code, d, *, o=10.0, h=10.5, lo=9.8, c=10.2, chg=2.0):
    """按日期问回来的一行，列名照 2026-11-26 实测：开高低收、涨跌幅、量额都带那一天。"""
    t = "{" + d.isoformat() + "}"
    return {"SECURITY_CODE": code, "SECURITY_SHORT_NAME": f"股{code}", "MARKET_SHORT_NAME": "SH",
            f"OPEN_PRICE<140>{t}": f"{o:.2f}", f"PEAK_PRICE<140>{t}": f"{h:.2f}",
            f"BOTTOM_PRICE<140>{t}": f"{lo:.2f}", f"NEWEST_PRICE<140>{t}": f"{c:.2f}",
            f"CHG{t}": f"{chg:.2f}", f"VOLUME{t}": "1200000", f"TRADING_VOLUMES{t}": "1.2亿",
            # 此刻的、不带日期的列也会一起回来，解析时不能拿它们
            "NEWEST_PRICE": "99.00", "CHG": "-5.00", "PEAK_PRICE<140>": "99.00"}


def bar(d, close=10.0):
    return build_kline_bar(dt=d, open_p=close, close_p=close, high_p=close, low_p=close, pct=0.0,
                           turnover=None, prev_close=close, volume=1e6, amount=1e7)


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, m):
        self.lines.append(m)

    warning = info


class _Resp:
    status_code, text, content = 200, "{}", b"{}"

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": {"result": {"total": 0, "dataList": []}}}


@pytest.fixture(autouse=True)
def _no_task():
    P.finish_task()
    yield
    P.finish_task()


# ═══ ① 每次任务的调用上限 ═════════════════════════════════════════════════════

class TestTaskBudget:
    def test_满了就不发_没开任务不限(self, monkeypatch):
        sent = []
        monkeypatch.setattr(P.httpx, "post", lambda *a, **k: sent.append(1) or _Resp())
        P.start_task("日更", limit=2)
        P.search("强势池")
        P.search("涨跌停")
        with pytest.raises(P.PromptBudgetExceeded):
            P.search("股票代码是600001或600002；流通市值；最新价")
        assert len(sent) == 2, "超额那一问不能发出去"
        t = P.task_status()
        assert t["used"] == 2 and t["refused"] == ["按代码问 2 只（流通市值；最新价）"] and P.task_left() == 0
        assert P.finish_task()["used"] == 2
        before = P.calls_total()
        for _ in range(3):
            P.search("网站上的按钮")
        assert len(sent) == 5 and P.calls_total() == before + 3 and P.task_left() is None

    def test_超额是PromptError_调用方照没拿到走兜底(self, monkeypatch):
        """所有调用方本来就接 PromptError；超额不需要另写一条路。"""
        from app.services.eastmoney_fetcher import fetch_turnover_top_stocks
        monkeypatch.setattr(P.httpx, "post", lambda *a, **k: _Resp())
        P.start_task("日更", limit=0)
        assert issubclass(P.PromptBudgetExceeded, P.PromptError)
        assert fetch_turnover_top_stocks("成交额排序前60") == []


class TestDatedBar:
    def test_按日期问的开高低收都从带那天的列取(self):
        r = P.parse_row_on(dated_item("600001", D3, o=10, h=11, lo=9.5, c=10.5, chg=5.0), D3)
        assert (r["open"], r["high"], r["low"], r["price"], r["pct_change"]) == (10, 11, 9.5, 10.5, 5.0)
        assert r["volume"] == 1.2e6 and r["amount"] == 1.2e8 and r["data_date"] == D3

    def test_问法每个字段都带日期(self):
        f = P.bar_fields_on(D3)
        for w in ("开盘价", "最高价", "最低价", "收盘价", "涨跌幅", "成交量", "成交额"):
            assert f"2026年11月25日{w}" in f


# ═══ ② dump 没接上：先 prompt，补不上的才逐只拉 K 线 ════════════════════════════

class TestFillGapsFromPrompt:
    @pytest.fixture
    def du(self):
        from scripts import daily_update as du
        return du

    def _setup(self, monkeypatch, rows_for=None, fail=False):
        calls = []

        def fake(codes, fields=None, on=None):
            calls.append((list(codes), on))
            if fail:
                raise P.PromptError("接口挂了")
            ok = rows_for(on) if rows_for else set(codes)
            return {c: P.parse_row_on(dated_item(c, on), on) for c in codes if c in ok}
        monkeypatch.setattr(P, "fetch_codes", fake)
        return calls

    def _infos(self, *codes):
        return [SimpleNamespace(code=c, is_st=False, market=1) for c in codes]

    def test_只差当日那根的不问_缺两天的按日期问_缺太多的走K线(self, du, monkeypatch):
        calls = self._setup(monkeypatch)
        hist = {"A": [bar(date(2026, 11, 24))],          # 历史到上一交易日：只差当日那根
                "B": [bar(date(2026, 11, 20))],          # 缺 11-23、11-24
                "C": [bar(date(2026, 11, 10))]}          # 缺 10 天：多半是停牌，一个 K 线请求就拿全
        out = {}
        rest, only_today, filled = du._fill_history_gaps_from_prompt(
            self._infos("A", "B", "C"), hist, out, D3, CAL, reserve=0, log=_Log())
        assert [i.code for i in rest] == ["C"]
        assert only_today == {"A", "B"}
        assert calls == [(["B"], date(2026, 11, 23)), (["B"], date(2026, 11, 24))]
        assert [b.date for b in out["B"]] == [date(2026, 11, 23), date(2026, 11, 24)] == \
               [b.date for b in filled["B"]]
        assert out["B"][-1].close_price == 10.2, "取带那天的收盘价，不是此刻的 99"
        assert "A" not in out

    def test_一百只一问(self, du, monkeypatch):
        calls = self._setup(monkeypatch)
        codes = [f"6{i:05d}" for i in range(150)]
        hist = {c: [bar(date(2026, 11, 21 - 1))] for c in codes}     # 都缺 11-23、11-24
        rest, _, filled = du._fill_history_gaps_from_prompt(
            self._infos(*codes), hist, {}, D3, CAL, reserve=0, log=_Log())
        assert not rest and len(filled) == 150
        assert [len(c) for c, _ in calls] == [100, 50, 100, 50]

    def test_要给后面必需的查询留够次数(self, du, monkeypatch):
        calls = self._setup(monkeypatch)
        monkeypatch.setattr(P, "task_left", lambda: 4)
        log = _Log()
        rest, only_today, _ = du._fill_history_gaps_from_prompt(
            self._infos("B"), {"B": [bar(date(2026, 11, 20))]}, {}, D3, CAL, reserve=4, log=log)
        assert calls == [] and [i.code for i in rest] == ["B"] and only_today == set()
        assert "没问完" in log.lines[-1]

    def test_没日历分不清缺哪天_全部走K线(self, du, monkeypatch):
        calls = self._setup(monkeypatch)
        rest, only_today, _ = du._fill_history_gaps_from_prompt(
            self._infos("A"), {"A": [bar(date(2026, 11, 24))]}, {}, D3, None, reserve=0, log=_Log())
        assert calls == [] and [i.code for i in rest] == ["A"] and not only_today

    def test_接口失败或只补了一部分_交给K线_补上的先放着(self, du, monkeypatch):
        self._setup(monkeypatch, rows_for=lambda d: {"B"} if d == date(2026, 11, 23) else set())
        out = {}
        rest, only_today, filled = du._fill_history_gaps_from_prompt(
            self._infos("B"), {"B": [bar(date(2026, 11, 20))]}, out, D3, CAL, reserve=0, log=_Log())
        assert [i.code for i in rest] == ["B"] and not filled and "B" not in only_today
        assert [b.date for b in out["B"]] == [date(2026, 11, 23)]

        calls = self._setup(monkeypatch, fail=True)
        rest, _, _ = du._fill_history_gaps_from_prompt(
            self._infos("B"), {"B": [bar(date(2026, 11, 20))]}, {}, D3, CAL, reserve=0, log=_Log())
        assert [i.code for i in rest] == ["B"] and len(calls) == 1, "失败一次就不再接着问"


# ═══ ③ 涨停雷达刷新算分 ═══════════════════════════════════════════════════════

class TestRadarBars:
    def _seed(self, db, code, n_hist):
        st = Stock(code=code, name=f"股{code}", market="SH", is_st=False)
        db.add(st)
        db.flush()
        days = [d for d in CAL if d < D3][-n_hist:] if n_hist else []
        for d in days:
            db.add(StockDailySnapshot(stock_id=st.id, date=d, close_price=10.0, pct_change=0.0,
                                      is_settled=True))
        db.commit()
        return st

    def test_历史够的不拉K线_当天那根问prompt(self, db, monkeypatch):
        from app.services import limit_up_detail_service as svc
        a = self._seed(db, "600001", 61)
        b = self._seed(db, "600002", 10)          # 历史不够：逐只拉
        asked = []

        def fake_codes(codes, fields=None, on=None):
            asked.append(list(codes))
            rows = {}
            for c in codes:
                r = P.parse_row_on(dated_item(c, D3, c=11.0, chg=10.0), D3)
                rows[c] = r
            return rows
        monkeypatch.setattr(P, "fetch_codes", fake_codes)
        kl = []

        def fake_kl(infos, days=65, max_workers=5, delay_between=0.1, require_date=None):
            kl.append(([i.code for i in infos], max_workers))
            return {i.code: [bar(d) for d in CAL[-40:-1]] for i in infos}
        monkeypatch.setattr(svc, "fetch_klines_batch", fake_kl)

        stats = {}
        out = svc._radar_bars(db, [a, b], D3, stats)
        assert asked == [["600001", "600002"]], "当天那根一问全部"
        assert kl == [(["600002"], svc._RADAR_KLINE_WORKERS)], "只有历史不够的才逐只拉，低并发"
        assert out["600001"][-1].date == D3 and out["600001"][-1].close_price == 11.0
        assert out["600001"][-1].is_limit_up, "当天那根照常判涨停"
        assert len(out["600001"]) == 62, "61 根库里历史 + 当天那根"
        assert out["600002"][-1].date == D3
        assert stats["kline_stocks"] == 1 and stats["kline_skipped"] == 0 and stats["today_from_prompt"] == 2

    def test_库里历史断档的也逐只拉_不把当天那根接在几周前后面(self, db, monkeypatch):
        from app.services import limit_up_detail_service as svc
        a = self._seed(db, "600001", 61)
        old = Stock(code="600009", name="掉出池子", market="SH", is_st=False)
        db.add(old)
        db.flush()
        for d in [d for d in CAL if d <= date(2026, 9, 15)][-61:]:
            db.add(StockDailySnapshot(stock_id=old.id, date=d, close_price=10.0, pct_change=0.0, is_settled=True))
        db.commit()
        monkeypatch.setattr(P, "fetch_codes", lambda codes, fields=None, on=None: {})
        kl = []
        monkeypatch.setattr(svc, "fetch_klines_batch",
                            lambda infos, **k: kl.append([i.code for i in infos]) or {})
        svc._radar_bars(db, [a, old], D3, {})
        assert kl == [["600009"]]

    def test_逐只K线有上限_prompt失败退回库里那天的快照(self, db, monkeypatch):
        from app.services import limit_up_detail_service as svc
        monkeypatch.setattr(svc, "_RADAR_KLINE_CAP", 1)
        a = self._seed(db, "600001", 61)
        db.add(StockDailySnapshot(stock_id=a.id, date=D3, close_price=10.5, pct_change=5.0, is_settled=False))
        b, c = self._seed(db, "600002", 0), self._seed(db, "600003", 0)
        db.commit()

        def boom(codes, fields=None, on=None):
            raise P.PromptError("挂了")
        monkeypatch.setattr(P, "fetch_codes", boom)
        monkeypatch.setattr(svc, "fetch_klines_batch",
                            lambda infos, **k: {i.code: [bar(CAL[-2])] for i in infos})
        stats = {}
        out = svc._radar_bars(db, [a, b, c], D3, stats)
        assert out["600001"][-1].date == D3 and out["600001"][-1].close_price == 10.5
        assert stats["kline_stocks"] == 1 and stats["kline_skipped"] == 1 and "prompt_error" in stats
        assert out["600003"] == []
