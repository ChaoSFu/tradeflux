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
