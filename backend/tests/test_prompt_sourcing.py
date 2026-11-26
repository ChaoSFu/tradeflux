"""
2026-11-25 两件事的回归：
  ① 日更目标日不再被「K 线源没给今天那根」带回前一天（那天 11-25 跌停实际 45 家、系统显示 2 家）；
     只给当前值的数据源落库前核对日期。
  ② 取数改走选股 prompt、不翻全市场：大市值 ≥300 亿名单、人气核心池区间涨幅前列、个股当日事实按需取；
     选股请求跨进程节流。
外部接口一律换成假的，不碰网络。
"""
import json
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.app_config import AppConfig
from app.models.large_cap import LargeCapMarketDaily, LargeCapSyncBatch
from app.models.stock_day_fact import StockDayFact
from app.services import large_cap_service as svc
from app.services import large_cap_sync_service as sync
from app.services import popular_core_service as pc
from app.services import prompt_api as P
from app.services import stock_day_facts as sdf
from app.services.eastmoney_fetcher import SH_TZ, CoreRecallDetail, KLineBar

D1, D2, D3 = date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25)
CAL = [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20), D1, D2]


def item(code, *, d=D3, name=None, price=10.0, chg=1.0, cap_yi=500.0, market="SH", **kw):
    """一条选股结果，字段名照 2026-11-25 实测的样子。"""
    it = {"SECURITY_CODE": code, "SECURITY_SHORT_NAME": name or f"股{code}", "MARKET_SHORT_NAME": market,
          "NEWEST_PRICE": f"{price:.2f}", "CHG": f"{chg:.2f}", "PCHG": f"{price - price / (1 + chg / 100):.2f}",
          f"OPEN_PRICE<140>{{{d.isoformat()}}}": f"{price * 0.99:.2f}", "PEAK_PRICE<140>": f"{price * 1.01:.2f}",
          "BOTTOM_PRICE<140>": f"{price * 0.98:.2f}", "VOLUME": "466.74万", "TRADING_VOLUMES": "68.42亿",
          "TOAL_MARKET_VALUE<140>": f"{cap_yi}亿", "CIRCULATION_MARKET_VALUE<140>": f"{cap_yi * 0.8}亿",
          "QRR": "0.97", "TURNOVER_RATE": "0.37",
          f"INTERVAL_CHG{{2026-11-19|{d.isoformat()}|5|天}}": "2.23",
          f"INTERVAL_CHG{{2026-10-29|{d.isoformat()}|20|天}}": "10.83"}
    it.update(kw)
    return it


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, m):
        self.lines.append(m)

    warning = info


# ═══ 选股接口：解析、分页、节流 ═══════════════════════════════════════════════

class TestPromptApi:
    def test_数字和日期都从列名里来(self):
        assert P.cn_number("1.84万亿") == 1.84e12 and P.cn_number("68.42亿") == 6.842e9
        assert P.cn_number("466.74万") == 4667400.0 and P.cn_number("-") is None
        r = P.parse_row(item("600519", price=1474.84, chg=1.02, PCHG="14.84"))
        assert r["prev_close"] == 1460.0 and r["volume"] == 4667400.0 and r["amount"] == 6.842e9
        assert r["data_date"] == D3 and r["intervals"] == {5: 2.23, 20: 10.83}

    def test_分页拿不全就抛错_不当部分结果用(self, monkeypatch):
        pages = {1: [item(f"6000{i:02d}") for i in range(100)], 2: []}
        monkeypatch.setattr(P, "search", lambda kw, page_no=1, **k: {"total": 150, "dataList": pages[page_no]})
        with pytest.raises(P.PromptError):
            P.search_all("x")
        pages[2] = [item(f"6001{i:02d}") for i in range(50)]
        items, total = P.search_all("x")
        assert total == 150 and len(items) == 150

    def test_两次请求至少隔开_跨进程用文件锁(self, monkeypatch, tmp_path):
        monkeypatch.setattr(P, "MIN_INTERVAL_S", 0.2)
        monkeypatch.setattr(P, "LOCK_PATH", str(tmp_path / "prompt.lock"))
        stamps = []

        class Resp:
            status_code, text, content = 200, "{}", b"{}"

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": {"result": {"total": 0, "dataList": []}}}

        def post(*a, **k):
            stamps.append(time.time())
            return Resp()
        monkeypatch.setattr(P.httpx, "post", post)
        P.search("a")
        P.search("b")
        assert stamps[1] - stamps[0] >= 0.19


# ═══ ① 日更目标日 ══════════════════════════════════════════════════════════

class TestOpenSession:
    def _f(self):
        from scripts.daily_update import _is_open_session
        return _is_open_session

    def test_11月25日那次_收盘后K线源只到前一天_也不改目标日(self):
        f = self._f()
        assert f(D3, datetime(2026, 11, 25, 15, 3, 17, tzinfo=SH_TZ), True, CAL) is True

    def test_周末_市场时钟停在周五_要修正(self):
        f = self._f()
        sat = date(2026, 11, 28)
        assert f(sat, datetime(2026, 11, 27, 16, 14, tzinfo=SH_TZ), True, CAL) is False

    def test_开盘前_还没到0915_要修正(self):
        f = self._f()
        assert f(D3, datetime(2026, 11, 25, 9, 10), True, CAL) is False

    def test_市场时间探测失败_只信日历明确说是交易日(self):
        f = self._f()
        assert f(D3, datetime(2026, 11, 25, 10, 0), False, CAL) is False, "日历没覆盖到今天，本机时钟说不准"
        assert f(D3, datetime(2026, 11, 25, 10, 0), False, CAL + [D3]) is True

    def test_日历说休市_一律修正(self):
        f = self._f()
        holiday = date(2026, 11, 21)          # 日历覆盖到 11-24，11-21 不在里面 = 不是交易日
        assert f(holiday, datetime(2026, 11, 21, 10, 0), True, CAL) is False


class TestRepairFromPrompt:
    def _bars(self, d):
        from app.services.eastmoney_fetcher import build_kline_bar
        return [build_kline_bar(dt=d, open_p=10, close_p=10, high_p=10, low_p=10, pct=0.0, turnover=None,
                                prev_close=10, volume=1e6, amount=1e7)]

    def test_日期对上的补上_对不上的拒绝(self, monkeypatch):
        from scripts import daily_update as du
        rows = {"600001": P.parse_row(item("600001", price=11.0, chg=10.0)),
                "600002": P.parse_row(item("600002", d=D2))}
        monkeypatch.setattr(P, "fetch_codes", lambda codes, fields=P.DAY_FIELDS: rows)
        infos = [SimpleNamespace(code="600001", is_st=False, market=1),
                 SimpleNamespace(code="600002", is_st=False, market=1)]
        kmap = {"600001": self._bars(D2), "600002": self._bars(D2)}
        rep, rej = du._repair_today_bar_from_prompt(infos, kmap, D3, _Log())
        assert (rep, rej) == (1, 1)
        assert kmap["600001"][-1].date == D3 and kmap["600001"][-1].close_price == 11.0
        assert kmap["600002"][-1].date == D2

    def test_选股接口挂了_交给行情兜底(self, monkeypatch):
        from scripts import daily_update as du

        def boom(*a, **k):
            raise P.PromptError("502")
        monkeypatch.setattr(P, "fetch_codes", boom)
        log = _Log()
        assert du._repair_today_bar_from_prompt([SimpleNamespace(code="600001", is_st=False, market=1)],
                                                {}, D3, log) == (0, 0)
        assert "改走行情兜底" in log.lines[0]


class TestCurrentOnlySources:
    def test_召回名单数据日期对不上_不写(self, db, monkeypatch):
        from app.services import limit_up_detail_service as lus
        monkeypatch.setattr(lus, "fetch_core_recall_details",
                            lambda: {"600001": CoreRecallDetail(code="600001", name="甲", data_date=D3)})
        assert lus.sync_core_recall(db, D2) == 0
        assert db.query(AppConfig).filter(AppConfig.key == lus.core_recall_key(D2)).first() is None
        assert lus.sync_core_recall(db, D3) == 1


# ═══ ② 个股当日事实：按需取 ═══════════════════════════════════════════════════

@pytest.fixture
def current(monkeypatch):
    """把「此刻」定在 11-25 收盘后，并把按需取数打开。"""
    monkeypatch.setattr(sdf, "might_be_current", lambda d: True)
    monkeypatch.setattr(sdf, "current_day", lambda db, market_now=None: (D3, True, datetime(2026, 11, 25, 15, 30)))


class TestStockDayFacts:
    def test_历史日期不问(self, db, monkeypatch):
        monkeypatch.setattr(P, "fetch_codes", lambda *a, **k: pytest.fail("历史日期不该问"))
        assert sdf.facts(db, D2, ["600001"]) == {}

    def test_当天按代码问_存下来_再用不再问(self, db, monkeypatch, current):
        calls = []

        def fake(codes, fields=P.DAY_FIELDS):
            calls.append(list(codes))
            return {c: P.parse_row(item(c)) for c in codes}
        monkeypatch.setattr(P, "fetch_codes", fake)
        out = sdf.facts(db, D3, ["600001", "600002"])
        assert set(out) == {"600001", "600002"} and out["600001"].pct_change_20d == 10.83
        assert out["600001"].amount == 68.42 and out["600001"].source == sdf.SOURCE_PROMPT
        sdf.facts(db, D3, ["600001", "600002"])
        assert calls == [["600001", "600002"]] and db.query(StockDayFact).count() == 2

    def test_接口给的不是这天_不存(self, db, monkeypatch, current):
        monkeypatch.setattr(P, "fetch_codes", lambda codes, fields=P.DAY_FIELDS: {c: P.parse_row(item(c, d=D2)) for c in codes})
        assert sdf.facts(db, D3, ["600001"]) == {} and db.query(StockDayFact).count() == 0

    def test_大市值快照里有的不问(self, db, monkeypatch, current):
        db.add(LargeCapMarketDaily(trade_date=D3, stock_code="600001", stock_name="大", total_market_cap=1000,
                                   pct_change_20d=5.0, source=sync.SOURCE_PROMPT))
        db.commit()
        asked = []
        monkeypatch.setattr(P, "fetch_codes", lambda codes, fields=P.DAY_FIELDS: asked.extend(codes) or {})
        out = sdf.facts(db, D3, ["600001", "600002"])
        assert out["600001"].source == sdf.SOURCE_LARGE_CAP and asked == ["600002"]


class TestGainTops:
    def _fake_search_all(self, monkeypatch):
        def search_all(kw, page_size=P.PAGE_SIZE, max_pages=P.MAX_PAGES, **k):
            w = int(kw.split("近")[1].split("个")[0])
            rows = []
            for i in range(40):
                code = f"83{i:04d}" if i < 2 else f"60{i:04d}"     # 前两只是北交所
                it = item(code)
                it.pop("INTERVAL_CHG{2026-11-19|2026-11-25|5|天}")
                it.pop("INTERVAL_CHG{2026-10-29|2026-11-25|20|天}")
                it[f"INTERVAL_CHG{{2026-10-01|2026-11-25|{w}|天}}"] = f"{200 - i:.2f}"
                rows.append(it)
            return rows, 40
        monkeypatch.setattr(P, "search_all", search_all)

    def test_去掉北交所留前30_存下来(self, db, monkeypatch, current):
        self._fake_search_all(monkeypatch)
        r = sdf.sync_gain_tops(db, D3)
        assert r["ok"] and r["counts"] == {"5": 30, "10": 30, "20": 30, "60": 30}
        tops = sdf.gain_tops(db, D3)
        assert "830000" not in tops[20] and tops[20]["600002"] == 198.0
        assert sdf.sync_gain_tops(db, D3)["reason"] == "已有"

    def test_盘中不问(self, db, monkeypatch):
        monkeypatch.setattr(sdf, "might_be_current", lambda d: True)
        monkeypatch.setattr(sdf, "current_day", lambda db, market_now=None: (D3, False, datetime(2026, 11, 25, 10)))
        monkeypatch.setattr(P, "search_all", lambda *a, **k: pytest.fail("盘中不该问"))
        assert not sdf.sync_gain_tops(db, D3)["ok"]

    def test_人气核心池用前列排名次(self, db, monkeypatch, current):
        self._fake_search_all(monkeypatch)
        monkeypatch.setattr(P, "fetch_codes", lambda codes, fields=P.DAY_FIELDS: {c: P.parse_row(item(c)) for c in codes})
        r = pc.compute(db, D3, CAL + [D3])
        assert r["sources"]["gain_20"]["status"] == "ok" and r["sources"]["gain_20"]["n"] == 20
        top = r["items"]["600002"]
        assert top["rank_20d"] == 1 and top["stock_name"] == "股600002"
        assert not any("当日事实" in m for m in (top["data_status"] or {}).get("missing", [])), "成员事实按代码问到了"
        assert top["pct_change_20d"] == 10.83


# ═══ ② 大市值：选股 prompt 名单 ══════════════════════════════════════════════

def _batch(db, d, source, floor=None, rows=(), concepts=None):
    b = LargeCapSyncBatch(trade_date=d, source=source, started_at=datetime(2026, 11, 25, 15), is_complete=True,
                          is_settled=True, applied=True, status="ok", universe_floor_yi=floor)
    db.add(b)
    db.flush()
    for code, cap, industry, *rest in rows:
        db.add(LargeCapMarketDaily(trade_date=d, stock_code=code, stock_name=f"股{code}", market="SH",
                                   total_market_cap=cap, pct_change=1.0, amount=10.0, industry=industry,
                                   is_st=False, source=source, batch_id=b.id,
                                   concepts=json.dumps(rest[0], ensure_ascii=False) if rest and rest[0] else None))
    db.commit()
    return b


class TestLargeCapPrompt:
    def test_名单_沿用行业_新进的补问_掉出名单的补问市值(self, db, monkeypatch):
        _batch(db, date(2026, 11, 18), sync.SOURCE_EM, rows=[("688001", 900, "半导体", ["算力概念", "芯片"])])
        _batch(db, D2, sync.SOURCE_PROMPT, floor=300, rows=[("600001", 800, "银行Ⅱ"), ("600009", 320, "白酒Ⅱ"),
                                                            ("600010", 120, "白酒Ⅱ")])   # 昨天就在下限以下：不问
        listed = [item("600001", cap_yi=820), item("600002", cap_yi=650),
                  item("830001", cap_yi=400, market="BJ")]
        monkeypatch.setattr(P, "search_all", lambda kw, **k: (listed, 3))
        asked = []

        def fetch_codes(codes, fields=P.DAY_FIELDS):
            asked.append((list(codes), fields))
            if fields == sync.CLASSIFY_FIELDS:
                return {c: {**P.parse_row(item(c)), "industry": "电池",
                            "style_concepts": ["算力概念", "HS300_", "权重股"]} for c in codes}
            return {c: P.parse_row(item(c, cap_yi=280)) for c in codes}
        monkeypatch.setattr(P, "fetch_codes", fetch_codes)
        res = sync.fetch_prompt(db, 300)
        assert res.fetch_ok and set(res.rows) == {"600001", "600002"} and res.expected == 2, "北交所不算"
        assert res.rows["600001"]["industry"] == "银行Ⅱ", "行业沿用之前存的"
        assert res.rows["600002"]["industry"] == "电池" and res.rows["600002"]["concepts"] == ["算力概念"], \
            "新进的补问；概念只留东财 f103 词表里有的，风格标签去掉"
        assert set(res.extra_rows) == {"600009"} and res.extra_rows["600009"]["total_market_cap"] == 280
        assert [a[0] for a in asked] == [["600002"], ["600009"]]

    def test_写入记下下限_补问的那几只也写进去(self, db, monkeypatch):
        res = sync.FetchResult(source=sync.SOURCE_PROMPT, expected=1, pages_expected=1, pages_ok=1, fetch_ok=True,
                               floor_yi=300)
        res.rows["600001"] = sync.parse_prompt_item(item("600001", cap_yi=820))
        res.extra_rows["600009"] = sync.parse_prompt_item_from_row(P.parse_row(item("600009", cap_yi=280)))
        out = sync.sync_snapshot(db, fetch_pr=lambda db_, f: res, market_now=datetime(2026, 11, 25, 15, 30),
                                 calendar=CAL + [D3], log=lambda m: None)
        assert out["applied"] and out["trade_date"] == D3
        b = db.query(LargeCapSyncBatch).filter_by(applied=True).one()
        assert b.source == sync.SOURCE_PROMPT and b.universe_floor_yi == 300
        assert db.query(LargeCapMarketDaily).filter_by(trade_date=D3).count() == 2

    def test_腾讯备用名单只取有市值的_600849那种占位代码不进(self, db):
        _batch(db, date(2026, 11, 18), sync.SOURCE_EM,
               rows=[("600001", 900, "银行Ⅱ"), ("600849", None, None), ("600002", 50, "银行Ⅱ")])
        uni, carry, d, floor = sync._tencent_universe(db)
        assert [c for c, _ in uni] == ["600001"] and d == date(2026, 11, 18) and floor == 360.0


@pytest.fixture
def cal3(db):
    from app.services.trading_calendar import _write_cache
    _write_cache(db, [D1, D2, D3])


class TestLargeCapOverviewFloor:
    NOW = datetime(2026, 11, 25, 20, 0)

    def test_昨天不在300亿名单里_今天过门槛_算跨入(self, db, cal3):
        _batch(db, D2, sync.SOURCE_PROMPT, floor=300, rows=[("600001", 1030, "银行Ⅱ")])
        _batch(db, D3, sync.SOURCE_PROMPT, floor=300, rows=[("600001", 970, "银行Ⅱ"), ("600002", 1010, "银行Ⅱ")])
        ov = svc.build_overview(db, now=self.NOW)
        ev = ov["events"]
        assert [e["code"] for e in ev["exited"]] == ["600001"]
        assert [e["code"] for e in ev["entered"]] == ["600002"] and ev["entered"][0]["prev_cap_below"] == 300
        assert ev["first_observed"] == [] and "≥300 亿" in ov["universe_scope"]

    def test_门槛不高于名单下限_不给跨入跨出(self, db, cal3):
        _batch(db, D2, sync.SOURCE_PROMPT, floor=300, rows=[("600001", 400, "银行Ⅱ")])
        _batch(db, D3, sync.SOURCE_PROMPT, floor=300, rows=[("600001", 410, "银行Ⅱ")])
        ov = svc.build_overview(db, threshold=250, now=self.NOW)
        assert ov["events"]["available"] is False and any("名单下限" in w for w in ov["warnings"])

    def test_连缺几天时不说成上一交易日(self, db, cal3):
        _batch(db, D1, sync.SOURCE_PROMPT, floor=300, rows=[("600001", 1030, "银行Ⅱ")])
        ov = svc.build_overview(db, now=datetime(2026, 11, 25, 15, 10))
        note = next(n for n in ov["notes"] if "今天的收盘快照还没同步" in n)
        assert "最近一份成功同步的 2026-11-23" in note and "上一交易日 2026-11-23" not in note


# ═══ 历史补数：按日期问 ═══════════════════════════════════════════════════════

def dated_item(code, d, *, cap_yi=500.0, price=10.0, chg=1.0, w20=5.0, name=None):
    """按「某天」问回来的一条：带那天的列 + 此刻的不带日期的列（故意给不一样的值，解析时不能用）。"""
    ds = d.isoformat()
    return {"SECURITY_CODE": code, "SECURITY_SHORT_NAME": name or f"股{code}", "MARKET_SHORT_NAME": "SH",
            "NEWEST_PRICE": "99.99", "CHG": "9.99", "TOAL_MARKET_VALUE<140>": "9999亿", "TRADING_VOLUMES": "1亿",
            f"TOAL_MARKET_VALUE<140>{{{ds}}}": f"{cap_yi}亿", f"CIRCULATION_MARKET_VALUE<140>{{{ds}}}": f"{cap_yi * 0.8}亿",
            f"NEWEST_PRICE<140>{{{ds}}}": f"{price:.2f}", f"CHG{{{ds}}}": f"{chg:.2f}",
            f"TRADING_VOLUMES{{{ds}}}": "12.5亿", f"TURNOVER_RATE{{{ds}}}": "1.20", f"QRR{{{ds}}}": "0.90",
            f"VOLUME{{{ds}}}": "100万", f"INTERVAL_CHG{{2026-10-26|{ds}|20|天}}": f"{w20:.2f}",
            f"INTERVAL_CHG{{2026-11-14|{ds}|5|天}}": "1.10", "LISTING_DATE": "2010-01-01"}


class TestBackfill:
    def test_只认带那一天的列(self):
        r = P.parse_row_on(dated_item("600001", D1, cap_yi=820, price=12.3, chg=2.5), D1)
        assert r["price"] == 12.3 and r["pct_change"] == 2.5 and r["total_market_cap"] == 820e8
        assert r["amount"] == 12.5e8 and r["intervals"] == {20: 5.0, 5: 1.1} and r["data_date"] == D1
        assert P.parse_row_on(dated_item("600001", D1), D2)["data_date"] is None, "问的是 11-23，不能当 11-24 用"

    def _fakes(self, monkeypatch, lists, asked):
        def search_all(kw, **k):
            asked.append(kw)
            for d, rows in lists.items():
                if P.date_phrase(d) in kw:
                    if "涨幅排名前" in kw:
                        w = int(kw.split("近")[1].split("个")[0])
                        return [dated_item(f"60{i:04d}", d, w20=100 - i) for i in range(40)], 40
                    return rows, len(rows)
            return [], 0

        def fetch_codes(codes, fields=None, on=None):
            asked.append(("codes", tuple(codes), on))
            if fields == sync.CLASSIFY_FIELDS:
                return {c: {"industry": "电池", "style_concepts": None} for c in codes}
            return {c: P.parse_row_on(dated_item(c, on, cap_yi=250), on) for c in codes} if on else {}
        monkeypatch.setattr(P, "search_all", search_all)
        monkeypatch.setattr(P, "fetch_codes", fetch_codes)

    def test_逐日补_有全市场快照的跳过_掉出名单的按那天问(self, db, monkeypatch, cal3):
        from scripts import large_cap_backfill as bf
        _batch(db, D1, sync.SOURCE_EM, rows=[("600001", 800, "银行Ⅱ"), ("600009", 320, "白酒Ⅱ")])
        lists = {D2: [dated_item("600001", D2, cap_yi=1020, w20=8.0)],
                 D3: [dated_item("600001", D3, cap_yi=990), dated_item("600002", D3, cap_yi=700)]}
        asked = []
        self._fakes(monkeypatch, lists, asked)
        res = bf.run(db, [D1, D2, D3], CAL + [D3], apply=True, pause=0, log=lambda m: None)
        assert [r["status"] for r in res] == ["skipped", "ok", "ok"]
        b2 = db.query(LargeCapSyncBatch).filter_by(trade_date=D2, applied=True).one()
        assert b2.source == sync.SOURCE_PROMPT and b2.universe_floor_yi == 300 and "历史补数" in b2.warnings
        r2 = db.query(LargeCapMarketDaily).filter_by(trade_date=D2, stock_code="600001").one()
        assert r2.total_market_cap == 1020 and r2.pct_change_20d == 8.0 and r2.price == 10.0, "用的是那天的列"
        gone = db.query(LargeCapMarketDaily).filter_by(trade_date=D2, stock_code="600009").one()
        assert gone.total_market_cap == 250, "11-23 在名单、11-24 不在：按 11-24 问到的市值"
        assert ("codes", ("600009",), D2) in asked
        assert sdf.gain_tops(db, D2)[20]["600000"] == 100.0 and sdf.gain_tops(db, D3) is not None
        ov = svc.build_overview(db, trade_date=D3, now=datetime(2026, 11, 26, 20))
        assert [e["code"] for e in ov["events"]["exited"]] == ["600001"]

    def test_试跑不写库_也不补问掉出名单的(self, db, monkeypatch):
        from scripts import large_cap_backfill as bf
        _batch(db, D1, sync.SOURCE_EM, rows=[("600009", 320, "白酒Ⅱ")])
        asked = []
        self._fakes(monkeypatch, {D2: [dated_item("600001", D2)]}, asked)
        res = bf.run(db, [D2], CAL, apply=False, pause=0, log=lambda m: None)
        assert res[0]["status"] == "dry_ok" and db.query(LargeCapSyncBatch).filter_by(trade_date=D2).count() == 0
        assert not any(isinstance(a, tuple) and a[1] == ("600009",) for a in asked)

    def test_成员事实按那天补_大市值快照里有的不问(self, db, monkeypatch):
        db.add(LargeCapMarketDaily(trade_date=D2, stock_code="600001", total_market_cap=900, source=sync.SOURCE_PROMPT))
        db.commit()
        asked = []
        self._fakes(monkeypatch, {}, asked)
        got = sdf.backfill_facts(db, D2, ["600001", "600002"])
        assert set(got) == {"600002"} and asked == [("codes", ("600002",), D2)]
        row = db.query(StockDayFact).filter_by(trade_date=D2, stock_code="600002").one()
        assert row.settled and row.total_market_cap == 250


# ═══ 修「只给当前值」的存档：成交额前列、召回名单 ════════════════════════════════

class TestRepairCurrentOnly:
    def test_问法加日期(self):
        assert P.dated_keyword("成交额排序前80；成交额大于20亿；非ST", D2) == \
            "2026年11月24日成交额排序前80；2026年11月24日成交额大于20亿；非ST"
        assert P.dated_keyword("非ST；近10个交易日涨停天数大于等于2", D2) == "非ST；截至2026年11月24日近10个交易日涨停天数大于等于2"

    def test_召回问法拆成分支(self):
        from app.services.limit_up_detail_service import _recall_branches
        from app.services.eastmoney_fetcher import CORE_RECALL_KEYWORD
        fixed, br = _recall_branches(CORE_RECALL_KEYWORD)
        assert fixed == ["非ST", "非退市股票"] and len(br) == 4 and br[3] == "近60个交易日最高连板数大于等于3"

    def test_成交额前列按那天的成交额排名次_覆盖写回(self, db, monkeypatch):
        from app.models.turnover_pool import TurnoverPoolDaily
        from app.services import turnover_service as ts
        db.add(TurnoverPoolDaily(date=D2, stock_code="600999", stock_name="错的", rank=1, amount=1e9, pct_change=0,
                                 turnover_rate=0, market="SH"))
        db.commit()
        ds = D2.isoformat()

        def search_all(kw, **k):
            assert kw.startswith("2026年11月24日成交额排序前") and "2026年11月24日涨跌幅" in kw
            return [{"SECURITY_CODE": c, "SECURITY_SHORT_NAME": c, "MARKET_SHORT_NAME": "SH",
                     f"TRADING_VOLUMES{{{ds}}}": a, f"CHG{{{ds}}}": "1.5", f"TURNOVER_RATE{{{ds}}}": "2.0"}
                    for c, a in (("600001", "30亿"), ("600002", "50亿"))], 2
        monkeypatch.setattr(P, "search_all", search_all)
        r = ts.restore_turnover_pool(db, D2, apply=False)
        assert r["fetched"] == 2 and r["same"] == 0 and not r["applied"]
        assert db.query(TurnoverPoolDaily).filter_by(date=D2).count() == 1, "试跑不写"
        ts.restore_turnover_pool(db, D2, apply=True)
        rows = db.query(TurnoverPoolDaily).filter_by(date=D2).order_by(TurnoverPoolDaily.rank).all()
        assert [(x.stock_code, x.rank, x.amount) for x in rows] == [("600002", 1, 5e9), ("600001", 2, 3e9)]

    def test_成交额没给那天的列_不认(self, monkeypatch, db):
        from app.services import turnover_service as ts
        monkeypatch.setattr(P, "search_all", lambda kw, **k: ([{"SECURITY_CODE": "600001", "TRADING_VOLUMES": "30亿"}], 1))
        with pytest.raises(P.PromptError):
            ts.fetch_turnover_on(db, D2)

    def _recall_fakes(self, monkeypatch, end=D2):
        e = end.isoformat()
        lu = {"600001": {10: 3, 20: 4, 60: 6}, "600002": {20: 3, 60: 3}}      # 600002 近 10 日没涨停

        def search_all(kw, **k):
            if kw.startswith("非ST"):                    # 四个分支
                return ([{"SECURITY_CODE": "600001"}] if "近10个" in kw else
                        [{"SECURITY_CODE": "600002"}] if "近20个" in kw else []), 1
            codes = kw.split("；")[0].removeprefix("股票代码是").split("或")
            if "涨停天数大于等于1" in kw:
                n = int(kw.split("近")[1].split("个")[0])
                return [{"SECURITY_CODE": c, f"DURATION_LIMIT_UP{{2026-10-01|{e}|{n}|天}}": str(lu[c][n])}
                        for c in codes if n in lu[c]], 0
            return [{"SECURITY_CODE": c, "SECURITY_SHORT_NAME": f"股{c}", "区间最高连板[2026-08-26至2026-11-25]": "3.00",
                     f"INTERVAL_CHG{{2026-11-11|{e}|10|天}}": "5.0", f"INTERVAL_CHG{{2026-10-28|{e}|20|天}}": "9.0",
                     f"INTERVAL_CHG{{2026-08-25|{e}|60|天}}": "30.0", f"CHG{{{e}}}": "1.20",
                     f"TURNOVER_RATE{{{e}}}": "3.0", f"NEWEST_PRICE<140>{{{e}}}": "10.00"} for c in codes], len(codes)
        monkeypatch.setattr(P, "search_all", search_all)

    def test_召回名单重建_涨停天数不在结果里就是0(self, monkeypatch):
        from app.services.limit_up_detail_service import fetch_core_recall_on
        self._recall_fakes(monkeypatch)
        det, missing = fetch_core_recall_on(D2)
        assert set(det) == {"600001", "600002"} and missing == []
        a, b = det["600001"], det["600002"]
        assert (a.limit_up_days_10d, a.limit_up_days_20d, a.limit_up_days_60d) == (3, 4, 6)
        assert (b.limit_up_days_10d, b.limit_up_days_20d) == (0, 3), "近 10 日「大于等于1」里没有它 = 0"
        assert a.pct_change == 1.2 and a.interval_chg_20d == 9.0 and a.max_board_60d == 3 and a.data_date == D2

    def test_召回名单数据不是那天的_整批不认(self, monkeypatch):
        from app.services.limit_up_detail_service import fetch_core_recall_on
        self._recall_fakes(monkeypatch, end=D3)
        with pytest.raises(P.PromptError):
            fetch_core_recall_on(D2)

    def test_脚本试跑不写_写回覆盖(self, db, monkeypatch):
        from app.services import limit_up_detail_service as lus
        from scripts import repair_current_only as rc
        db.add(AppConfig(key=lus.core_recall_key(D2), value=json.dumps({"600999": {"n": "错的", "chg": 0}})))
        db.commit()
        self._recall_fakes(monkeypatch)
        assert rc.run(db, D2, apply=False, what=("recall",), log=lambda m: None)
        assert "600999" in db.query(AppConfig).filter_by(key=lus.core_recall_key(D2)).one().value
        assert rc.run(db, D2, apply=True, what=("recall",), log=lambda m: None)
        stored = json.loads(db.query(AppConfig).filter_by(key=lus.core_recall_key(D2)).one().value)
        assert set(stored) == {"600001", "600002"} and stored["600002"]["lu10"] == 0


# ═══ 2026-11-26：盘前误报、召回原因、流通市值走 prompt ════════════════════════════

class TestPreOpenNoise:
    def test_涨跌停接口三种情况分开(self):
        from scripts.daily_update import _limit_api_verdict as v
        assert v(96, False, False) == "ok"
        assert v(0, False, True) == "pre_open_zero", "盘前如实 0 只，不算降级"
        assert v(0, True, True) == "failed" and v(0, True, False) == "failed"
        assert v(0, False, False) == "empty_after_open"

    def test_请求失败能抛出来_如实0只不抛(self, monkeypatch):
        from app.services.eastmoney_fetcher import fetch_limit_move_codes
        monkeypatch.setattr(P, "search", lambda *a, **k: {"total": 0, "dataList": []})
        assert fetch_limit_move_codes(with_detail=True, raise_on_error=True) == {}

        def boom(*a, **k):
            raise P.PromptError("502")
        monkeypatch.setattr(P, "search", boom)
        with pytest.raises(P.PromptError):
            fetch_limit_move_codes(with_detail=True, raise_on_error=True)
        assert fetch_limit_move_codes(with_detail=True) == {}, "老调用方式照旧：失败返回空"

    def test_召回0只写清原因(self, db, monkeypatch):
        from app.services import limit_up_detail_service as lus
        monkeypatch.setattr(lus, "fetch_core_recall_details", lambda: {})
        n, why = lus.sync_core_recall_ex(db, D2)
        assert n == 0 and "保留之前那份" in why
        monkeypatch.setattr(lus, "fetch_core_recall_details",
                            lambda: {"600001": CoreRecallDetail(code="600001", name="甲", data_date=D3)})
        n, why = lus.sync_core_recall_ex(db, D2)
        assert n == 0 and "2026-11-25" in why and "不是 2026-11-24" in why
        assert lus.sync_core_recall(db, D3) == 1

    def test_流通市值走选股接口_问最新价不问收盘价(self, monkeypatch):
        from app.services.eastmoney_fetcher import fetch_float_market_caps_prompt
        asked = []

        def fetch_codes(codes, fields=None, on=None):
            asked.append(fields)
            return {"600001": P.parse_row(item("600001", price=12.0, cap_yi=100.0))}
        monkeypatch.setattr(P, "fetch_codes", fetch_codes)
        caps, bad = fetch_float_market_caps_prompt(["600001"])
        assert bad == 0 and caps["600001"] == (80e8, 12.0) and asked == ["流通市值；最新价"]

        def boom(*a, **k):
            raise P.PromptError("x")
        monkeypatch.setattr(P, "fetch_codes", boom)
        assert fetch_float_market_caps_prompt(["600001"]) == ({}, 1)
