"""
大市值概览：市值资格、跨入跨出、分组统计、20 日成交额倍数、数据源完整性。

最要紧的几条纪律：
  · 缺市值是「判不了」，不是「不在池里」；缺涨幅不当 0% 算
  · 两天都有完整快照、两天都有有效市值才比较；没返回的公司不算跌出门槛
  · 拿不全的批次一行都不写；盘中数据不写
"""
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.large_cap import LargeCapMarketDaily, LargeCapStockBoards, LargeCapSyncBatch
from app.models.sector import Sector
from app.services import large_cap_service as svc
from app.services import large_cap_sync_service as sync
from app.services.large_cap_service import Obs


def _o(code, cap, pct=1.0, **kw):
    return Obs(code=code, name=kw.pop("name", f"股{code}"), total_cap=cap, pct=pct, **kw)


# ── 解析与单位 ──────────────────────────────────────────────────────────────

def _em(code, cap_yi, pct=1.0, d=20261106, m=1, name=None, industry="银行Ⅱ", **kw):
    it = {"f12": code, "f13": m, "f14": name or f"股{code}", "f2": 10.0, "f3": pct,
          "f6": 5e8, "f8": 0.34, "f10": 0.65,
          "f20": cap_yi * 1e8 if cap_yi is not None else "-",
          "f21": cap_yi * 0.8e8 if cap_yi is not None else "-",
          "f24": 11.04, "f100": industry, "f109": 4.38, "f110": 15.04, "f160": 8.84, "f297": d}
    it.update(kw)
    return it


class TestParse:
    def test_元换亿_字段映射(self):
        r = sync.parse_em_item(_em("600519", 18163.69))
        assert r["total_market_cap"] == pytest.approx(18163.69)
        assert r["float_market_cap"] == pytest.approx(18163.69 * 0.8)
        assert r["amount"] == pytest.approx(5.0), "f6 是元，存亿元"
        # 2026-11-08 对茅台实测：f8=0.34 是换手率（=日K f61），f10=0.65 是量比
        assert r["turnover_rate"] == 0.34 and r["volume_ratio"] == 0.65
        assert (r["pct_change_5d"], r["pct_change_10d"], r["pct_change_20d"], r["pct_change_60d"]) \
            == (4.38, 8.84, 15.04, 11.04)
        assert r["trade_date"] == date(2026, 11, 6) and r["market"] == "SH"

    def test_横杠是None不是0(self):
        r = sync.parse_em_item(_em("601059", None, pct="-", f2="-", f6="-", f8="-", f10="-", f100="-"))
        assert r["total_market_cap"] is None and r["pct_change"] is None
        assert r["amount"] is None and r["turnover_rate"] is None and r["industry"] is None

    def test_负涨幅和零涨幅都保留(self):
        assert sync.parse_em_item(_em("1", 100, pct=-3.2))["pct_change"] == -3.2
        assert sync.parse_em_item(_em("1", 100, pct=0))["pct_change"] == 0.0

    def test_ST识别(self):
        assert sync.parse_em_item(_em("1", 100, name="*ST大药"))["is_st"] is True
        assert sync.parse_em_item(_em("1", 100, name="贵州茅台"))["is_st"] is False

    def test_腾讯单位(self):
        f = [""] * 60
        f[1], f[2], f[3], f[30], f[32] = "工商银行", "601398", "8.44", "20261106161452", "1.08"
        f[37], f[38], f[44], f[45], f[49] = "404642", "0.18", "22755.27", "30080.69", "0.87"
        r = sync.parse_tencent_line('v_sh601398="' + "~".join(f) + '"')
        assert r["total_market_cap"] == 30080.69, "腾讯市值本来就是亿元"
        assert r["amount"] == pytest.approx(40.4642), "腾讯成交额是万元"
        assert r["pct_change_20d"] is None and r["industry"] is None, "腾讯不给区间涨幅和行业"
        assert r["trade_date"] == date(2026, 11, 6) and r["market"] == "SH"


class TestYoungStockReturns:
    """粤芯半导 10-13 上市，到 11-06 只有 19 根日 K，东财给 20D = 60D = 128.14%（上市以来）。"""
    CAL = [date(2026, 10, 13) + timedelta(days=i) for i in range(19)]

    def _row(self, listing):
        r = sync.parse_em_item(_em("301660", 789, f110=128.14, f24=128.14, f26=listing))
        return r

    def test_不满N加1根就置空(self):
        r = self._row(20261013)
        n = sync.mask_young_returns([r], self.CAL[-1], self.CAL)
        assert n == 1
        assert r["pct_change_20d"] is None and r["pct_change_60d"] is None
        assert r["pct_change_5d"] == 4.38 and r["pct_change_10d"] == 8.84, "19 根够算 5 日和 10 日"

    def test_老股票不动(self):
        r = self._row(20010827)
        assert sync.mask_young_returns([r], self.CAL[-1], self.CAL) == 0
        assert r["pct_change_60d"] == 128.14

    def test_日历没刷到这天_不能把新股数成0天(self):
        """本机缓存止于 09-07 时，10-13 上市的被数成 0 个交易日，连 5 日涨跌幅都被清掉。"""
        r = self._row(20261013)
        stale = [date(2026, 9, 1) + timedelta(days=i) for i in range(7)]
        sync.mask_young_returns([r], date(2026, 11, 6), stale)
        assert r["pct_change_5d"] == 4.38 and r["pct_change_10d"] == 8.84
        assert r["pct_change_20d"] is None and r["pct_change_60d"] is None

    def test_没有日历时保守置空(self):
        r = self._row(20261013)
        sync.mask_young_returns([r], date(2026, 11, 6), [])
        assert r["pct_change_60d"] is None and r["pct_change_20d"] is None


# ── 市值资格 ────────────────────────────────────────────────────────────────

class TestMembership:
    @pytest.mark.parametrize("cap,expected", [(999.99, False), (1000, True), (1000.01, True)])
    def test_门槛边界(self, cap, expected):
        assert svc.membership(_o("1", cap), 1000, False) is expected

    def test_缺市值是判不了(self):
        assert svc.membership(_o("1", None), 1000, False) is None

    def test_ST默认排除(self):
        st = _o("1", 2000, is_st=True)
        assert svc.membership(st, 1000, False) is False
        assert svc.membership(st, 1000, True) is True

    def test_元到亿的边界(self):
        r = sync.parse_em_item(_em("1", 1000))
        assert svc.membership(_o("1", r["total_market_cap"]), 1000, False) is True


# ── 跨入跨出 ────────────────────────────────────────────────────────────────

class TestEvents:
    def test_跨入(self):
        ev = svc.compute_events({"A": _o("A", 1030, pct=6.19)}, {"A": _o("A", 970)}, 1000, False)
        assert [e["code"] for e in ev["entered"]] == ["A"] and not ev["exited"]
        assert ev["entered"][0]["cap_price_mismatch"] is False

    def test_跨出的票今天不在池里也要列出(self):
        ev = svc.compute_events({"A": _o("A", 970, pct=-5.83)}, {"A": _o("A", 1030)}, 1000, False)
        assert [e["code"] for e in ev["exited"]] == ["A"]
        assert ev["exited"][0]["prev_cap"] == 1030 and ev["exited"][0]["cap"] == 970

    def test_昨日缺失今日1200是首次观测(self):
        ev = svc.compute_events({"A": _o("A", 1200)}, {}, 1000, False)
        assert not ev["entered"] and [e["code"] for e in ev["first_observed"]] == ["A"]

    def test_昨日市值未知也是首次观测(self):
        ev = svc.compute_events({"A": _o("A", 1200)}, {"A": _o("A", None)}, 1000, False)
        assert not ev["entered"] and len(ev["first_observed"]) == 1

    def test_今日没返回不算跨出(self):
        ev = svc.compute_events({}, {"A": _o("A", 1030)}, 1000, False)
        assert not ev["exited"] and [e["code"] for e in ev["missing_today"]] == ["A"]

    def test_今日市值未知不算跨出(self):
        ev = svc.compute_events({"A": _o("A", None)}, {"A": _o("A", 1030)}, 1000, False)
        assert not ev["exited"] and len(ev["missing_today"]) == 1

    def test_前一日整批没有就不生成任何事件(self):
        assert svc.compute_events({"A": _o("A", 1200)}, None, 1000, False) is None

    def test_改门槛不产生虚假跨入(self):
        """两天都 900 亿：门槛从 1000 改到 800，它们进了展示池，但不是今天跨过 800。"""
        today = {"A": _o("A", 900), "B": _o("B", 905)}
        prev = {"A": _o("A", 895), "B": _o("B", 900)}
        ev = svc.compute_events(today, prev, 800, False)
        assert not ev["entered"] and not ev["exited"]

    def test_市值变化和股价不一致要标出来(self):
        ev = svc.compute_events({"A": _o("A", 1100, pct=2.0)}, {"A": _o("A", 990)}, 1000, False)
        assert ev["entered"][0]["cap_price_mismatch"] is True, "市值 +11% 股价只 +2%：可能股本变动"

    def test_ST变化单列不算市值事件(self):
        ev = svc.compute_events({"A": _o("A", 1500, is_st=True)}, {"A": _o("A", 1500)}, 1000, False)
        assert not ev["exited"] and ev["st_change"][0]["change"] == "戴帽移出"


# ── 统计 ────────────────────────────────────────────────────────────────────

class TestStats:
    def test_涨跌平家数_中位数_缺涨幅不当0(self):
        ms = [_o("A", 2000, 3.0), _o("B", 1500, 0.0), _o("C", 1200, -1.0), _o("D", 1100, None)]
        st = svc.group_stats(ms, {}, {}, 2.0, 2.0)
        assert (st["up"], st["flat"], st["down"]) == (1, 1, 1)
        assert st["median_pct"] == 0.0 and st["n_pct"] == 3, "D 没涨幅，不能当 0% 拉低中位数"
        assert st["count"] == 4 and st["total_cap"] == 5800

    def test_市值加权用给定权重_缺的不参与(self):
        ms = [_o("A", 3000, 2.0), _o("B", 1000, -2.0), _o("C", 1000, 5.0)]
        st = svc.group_stats(ms, {"A": 3000, "B": 1000}, {}, 2.0, 2.0)
        assert st["weighted_pct"] == pytest.approx((3000 * 2 - 1000 * 2) / 4000)
        assert st["n_weighted"] == 2, "C 没有前一日市值，不参与加权"

    def test_成交额求和单位亿(self):
        st = svc.group_stats([_o("A", 2000, amount=12.5), _o("B", 1500, amount=None)], {}, {}, 2, 2)
        assert st["amount"] == 12.5 and st["n_amount"] == 1

    def test_放量按量比与按倍数分开数(self):
        ms = [_o("A", 2000, volume_ratio=2.5), _o("B", 1500, volume_ratio=1.0)]
        st = svc.group_stats(ms, {}, {}, 2.0, 2.0)
        assert st["surge_volume_ratio"] == 1
        assert st["surge_multiple"] is None, "没有 20 日历史时倍数是未知，不是 0 家"


class TestAmountMultiple:
    def test_正好20天(self):
        hist = [{"A": 10.0} for _ in range(20)]
        avg, mult = svc.amount_multiples({"A": 25.0}, hist)["A"]
        assert avg == 10.0 and mult == 2.5

    def test_不足20天返回None_不拿短窗口冒充(self):
        hist = [{"A": 10.0} for _ in range(19)]
        assert svc.amount_multiples({"A": 25.0}, hist)["A"] == (None, None)

    def test_某天缺额或为0就是None(self):
        hist = [{"A": 10.0} for _ in range(19)] + [{"A": 0.0}]
        assert svc.amount_multiples({"A": 25.0}, hist)["A"] == (None, None)
        hist = [{"A": 10.0} for _ in range(19)] + [{}]
        assert svc.amount_multiples({"A": 25.0}, hist)["A"] == (None, None)


class TestClassification:
    def test_二级映射一级靠F10排位(self):
        cache = {"601398": [["BK1283", "银行", 1], ["BK0475", "银行Ⅱ", 2], ["BK1611", "国有大型银行Ⅲ", 3]],
                 "600519": [["BK0438", "食品饮料", 1], ["BK1277", "白酒Ⅱ", 2]]}
        m = svc.l2_to_l1(cache, {"601398": "银行Ⅱ", "600519": "白酒Ⅱ"})
        assert m == {"银行Ⅱ": "银行", "白酒Ⅱ": "食品饮料"}

    def test_对不上的不参与映射(self):
        cache = {"X": [["BK1", "电子", 1], ["BK2", "半导体", 2]]}
        assert svc.l2_to_l1(cache, {"X": "元件"}) == {}, "f100 不在 F10 里就别猜"


# ── 数据源完整性 ────────────────────────────────────────────────────────────

ONE_MARKET = [("m:1+t:2", 1)]


def _pages(items, total=None, fail_on=None):
    total = len(items) if total is None else total

    def get(fs, pn):
        if fail_on == pn:
            raise sync.PageError(f"第 {pn} 页失败")
        chunk = items[(pn - 1) * sync.PAGE_SIZE: pn * sync.PAGE_SIZE]
        return {"total": total, "diff": chunk}
    return get


def _items(n, start_cap=3000.0):
    return [_em(f"{600000 + i}", start_cap - i) for i in range(n)]


class TestCompleteness:
    def test_翻完全部页才算完整(self):
        res = sync.collect_eastmoney(_pages(_items(250)), ONE_MARKET, pause=lambda: None)
        v = sync.judge(res)
        assert res.pages_ok == res.pages_expected == 3 and v.complete

    def test_中间一页失败整批作废(self):
        res = sync.collect_eastmoney(_pages(_items(250), fail_on=2), ONE_MARKET, pause=lambda: None)
        v = sync.judge(res)
        assert not v.complete and res.fetch_ok is False

    def test_条数和源头总数不符(self):
        res = sync.collect_eastmoney(_pages(_items(150), total=180), ONE_MARKET, pause=lambda: None)
        assert not sync.judge(res).complete

    def test_有效市值太少不算完整(self):
        items = _items(10) + [_em(f"{700000 + i}", None) for i in range(5)]
        res = sync.collect_eastmoney(_pages(items), ONE_MARKET, pause=lambda: None)
        v = sync.judge(res)
        assert not v.complete and any("有效总市值" in r for r in v.reasons)

    def test_总数比上次少太多(self):
        res = sync.collect_eastmoney(_pages(_items(100)), ONE_MARKET, pause=lambda: None)
        assert not sync.judge(res, prev_expected=200).complete


def _today():
    return date.today()


def _fake_em(items, d):
    def f():
        for it in items:
            it["f297"] = int(d.strftime("%Y%m%d"))
        return sync.collect_eastmoney(_pages(items), ONE_MARKET, pause=lambda: None)
    return f


def _after_close(d):
    return datetime.combine(d, datetime.min.time()) + timedelta(hours=16)


class TestSyncWrites:
    def test_完整且收盘才写_重复同步幂等(self, db):
        d = _today()
        for _ in range(2):
            out = sync.sync_snapshot(db, fetch_em=_fake_em(_items(120), d), source="eastmoney",
                                     market_now=_after_close(d), log=lambda m: None)
            assert out["applied"] is True
        assert db.query(LargeCapMarketDaily).filter_by(trade_date=d).count() == 120
        assert db.query(LargeCapSyncBatch).filter_by(applied=True).count() == 2

    def test_盘中不写(self, db):
        d = _today()
        noon = datetime.combine(d, datetime.min.time()) + timedelta(hours=11)
        out = sync.sync_snapshot(db, fetch_em=_fake_em(_items(50), d), source="eastmoney",
                                 market_now=noon, log=lambda m: None)
        assert out["status"] == "intraday" and not out["applied"]
        assert db.query(LargeCapMarketDaily).count() == 0
        assert db.query(LargeCapSyncBatch).one().status == "intraday"

    def test_不完整一行都不写_也不冲掉旧的(self, db):
        d = _today()
        sync.sync_snapshot(db, fetch_em=_fake_em(_items(120), d), source="eastmoney",
                           market_now=_after_close(d), log=lambda m: None)

        def broken():
            return sync.collect_eastmoney(_pages(_items(120), fail_on=2), ONE_MARKET, pause=lambda: None)
        out = sync.sync_snapshot(db, fetch_em=broken, source="eastmoney",
                                 market_now=_after_close(d), log=lambda m: None)
        assert not out["applied"]
        assert db.query(LargeCapMarketDaily).filter_by(trade_date=d).count() == 120, "上一份有效数据留着"

    def test_东财失败且没有可沿用名单_备用也不硬上(self, db):
        def broken():
            return sync.collect_eastmoney(_pages(_items(50), fail_on=1), ONE_MARKET, pause=lambda: None)
        out = sync.sync_snapshot(db, fetch_em=broken, market_now=_after_close(_today()), log=lambda m: None)
        assert not out["applied"] and db.query(LargeCapMarketDaily).count() == 0
        assert [a["source"] for a in out["attempts"]] == [sync.SOURCE_EM, sync.SOURCE_TENCENT]

    def test_腾讯备用沿用名单和行业_区间涨幅留空(self, db):
        d0, d1 = _today() - timedelta(days=1), _today()
        sync.sync_snapshot(db, fetch_em=_fake_em(_items(3), d0), source="eastmoney",
                           market_now=_after_close(d1), log=lambda m: None)

        def tx(universe):
            res = sync.FetchResult(source=sync.SOURCE_TENCENT, expected=len(universe),
                                   pages_expected=1, pages_ok=1, fetch_ok=True)
            for code, m in universe:
                res.rows[code] = {**sync.parse_em_item(_em(code, 2000)), "pct_change_20d": None,
                                  "industry": None, "trade_date": d1}
            return res

        def broken():
            return sync.collect_eastmoney(_pages(_items(3), fail_on=1), ONE_MARKET, pause=lambda: None)
        out = sync.sync_snapshot(db, fetch_em=broken, fetch_tx=tx, market_now=_after_close(d1),
                                 log=lambda m: None)
        assert out["applied"] and out["trade_date"] == d1
        rows = db.query(LargeCapMarketDaily).filter_by(trade_date=d1).all()
        assert {r.source for r in rows} == {sync.SOURCE_TENCENT}
        assert {r.industry for r in rows} == {"银行Ⅱ"}, "行业沿用前一份东财快照"
        assert all(r.pct_change_20d is None for r in rows)

    def test_备用源不覆盖同一天的东财快照(self, db):
        d = _today()
        sync.sync_snapshot(db, fetch_em=_fake_em(_items(3), d), source="eastmoney",
                           market_now=_after_close(d), log=lambda m: None)

        def tx(universe):
            res = sync.FetchResult(source=sync.SOURCE_TENCENT, expected=len(universe),
                                   pages_expected=1, pages_ok=1, fetch_ok=True)
            for code, _m in universe:
                res.rows[code] = {**sync.parse_em_item(_em(code, 2000)), "trade_date": d}
            return res
        out = sync.sync_snapshot(db, fetch_tx=tx, source="tencent", market_now=_after_close(d),
                                 log=lambda m: None)
        assert out["status"] == "kept_existing"
        assert {r.source for r in db.query(LargeCapMarketDaily).all()} == {sync.SOURCE_EM}


class TestClassificationCache:
    def test_拉空不覆盖旧分类_失败不阻断(self, db):
        db.add(LargeCapStockBoards(stock_code="A", status="ok", boards=json.dumps([["BK1", "x", 1]]),
                                   fetched_at=datetime(2020, 1, 1)))
        db.commit()

        def fetch(code):
            if code == "B":
                raise RuntimeError("被掐")
            return []
        st = sync.refresh_classification(db, [("A", 2000), ("B", 1500), ("C", 100)], floor_yi=500,
                                         fetch=fetch, pause=lambda: None, log=lambda m: None)
        a = db.query(LargeCapStockBoards).filter_by(stock_code="A").one()
        assert a.status == "ok" and json.loads(a.boards) == [["BK1", "x", 1]]
        assert st["targets"] == 2, "C 在分类下限以下"
        assert db.query(LargeCapStockBoards).filter_by(stock_code="B").one().status == "error"

    def test_连续失败就停手(self, db):
        """生产 11-08：F10 全部读超时，不停的话 435 只 × 10 秒。"""
        calls = []

        def fetch(code):
            calls.append(code)
            raise TimeoutError("read timed out")
        codes = [(f"{600000 + i}", 3000 - i) for i in range(40)]
        st = sync.refresh_classification(db, codes, floor_yi=500, fetch=fetch,
                                         pause=lambda: None, log=lambda m: None)
        assert len(calls) == 5 and st["aborted"] is True
        assert st["remaining_missing"] == 35

    def test_中间成功一只就重新计数(self, db):
        seq = iter([RuntimeError, RuntimeError, RuntimeError, RuntimeError, "ok"] + [RuntimeError] * 3)

        def fetch(code):
            r = next(seq)
            if r == "ok":
                return [["BK1", "x", 1]]
            raise r("x")
        st = sync.refresh_classification(db, [(f"{600000 + i}", 3000 - i) for i in range(8)],
                                         floor_yi=500, fetch=fetch, pause=lambda: None, log=lambda m: None)
        assert st["tried"] == 8 and st["aborted"] is False and st["ok"] == 1


# ── 组装（库） ──────────────────────────────────────────────────────────────

D1, D2, D3 = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
NOW = datetime(2026, 11, 6, 18, 0)


def _seed(db, d, rows, source=sync.SOURCE_EM):
    b = LargeCapSyncBatch(trade_date=d, source=source, started_at=NOW, is_complete=True,
                          is_settled=True, applied=True, status="ok")
    db.add(b)
    db.flush()
    for code, cap, pct, industry in rows:
        db.add(LargeCapMarketDaily(trade_date=d, stock_code=code, stock_name=f"股{code}", market="SH",
                                   total_market_cap=cap, pct_change=pct, pct_change_20d=pct,
                                   amount=10.0, industry=industry, is_st=False, source=source,
                                   batch_id=b.id))
    db.commit()


@pytest.fixture
def cal(db):
    from app.services.trading_calendar import _write_cache
    _write_cache(db, [D1, D2, D3])


class TestOverview:
    def test_没有任何快照(self, db, cal):
        ov = svc.build_overview(db, now=NOW)
        assert ov["status"] == "no_data" and "summary" not in ov

    def test_跨入跨出首次观测_加权用前一日市值(self, db, cal):
        _seed(db, D2, [("A", 970, 0, "银行Ⅱ"), ("B", 1030, 0, "银行Ⅱ"), ("C", 2000, 0, "白酒Ⅱ")])
        _seed(db, D3, [("A", 1030, 6.19, "银行Ⅱ"), ("B", 970, -5.83, "银行Ⅱ"),
                       ("C", 2100, 5.0, "白酒Ⅱ"), ("N", 1500, 10.0, None)])
        ov = svc.build_overview(db, now=NOW)
        ev = ov["events"]
        assert [e["code"] for e in ev["entered"]] == ["A"]
        assert [e["code"] for e in ev["exited"]] == ["B"]
        assert [e["code"] for e in ev["first_observed"]] == ["N"]
        assert ov["weight_basis"] == "prev_cap"
        assert ov["summary"]["count"] == 3 and ov["summary"]["n_weighted"] == 2
        l2 = {g["key"]: g for g in ov["groups"]["industry_l2"]}
        assert l2["银行Ⅱ"]["entered"] == 1 and l2["银行Ⅱ"]["exited"] == 1
        assert l2["未分类"]["count"] == 1, "没有行业的票进「未分类」，不能静默丢掉"
        assert sum(g["count"] for g in ov["groups"]["industry_l2"]) == ov["summary"]["count"]

    def test_前一交易日没有快照不拿更早的冒充(self, db, cal):
        _seed(db, D1, [("A", 970, 0, "银行Ⅱ")])
        _seed(db, D3, [("A", 1030, 6.19, "银行Ⅱ")])
        ov = svc.build_overview(db, now=NOW)
        assert ov["events"]["available"] is False and ov["events"]["entered"] == []
        assert ov["prev_date"] == D2 and ov["weight_basis"] == "current_cap"

    def test_日历没覆盖到这天_不猜前一交易日(self, db):
        """本机缓存止于 09-07 时，11-06 的「前一天」被当成 09-07——跨两个月比市值。"""
        from app.services.trading_calendar import _write_cache
        _write_cache(db, [D1, D2])
        _seed(db, D2, [("A", 970, 0, "银行Ⅱ")])
        _seed(db, D3, [("A", 1030, 6.19, "银行Ⅱ")])
        ov = svc.build_overview(db, now=NOW)
        assert ov["prev_date"] is None and ov["events"]["available"] is False
        assert any("没覆盖到" in w for w in ov["warnings"])

    def test_请求不存在的日期(self, db, cal):
        _seed(db, D3, [("A", 1030, 1.0, "银行Ⅱ")])
        with pytest.raises(svc.NoSuchDate):
            svc.build_overview(db, trade_date=D2, now=NOW)

    def test_门槛覆盖按原始快照重算(self, db, cal):
        _seed(db, D2, [("A", 900, 0, "银行Ⅱ")])
        _seed(db, D3, [("A", 905, 1.0, "银行Ⅱ")])
        assert svc.build_overview(db, now=NOW)["summary"]["count"] == 0
        ov = svc.build_overview(db, threshold=800, now=NOW)
        assert ov["summary"]["count"] == 1 and ov["events"]["entered"] == []

    def test_概念多重归属_同一概念不重复计(self, db, cal):
        db.add_all([Sector(code="BK1", name="CPO", sector_type="concept", is_watched=True),
                    Sector(code="BK2", name="光通信", sector_type="concept", is_watched=False),
                    Sector(code="BK9", name="银行", sector_type="industry")])
        db.add(LargeCapStockBoards(stock_code="A", status="ok", boards=json.dumps(
            [["BK9", "银行", 1], ["BK1", "CPO", 9], ["BK2", "光通信", 10], ["BK1", "CPO", 11]])))
        _seed(db, D3, [("A", 2000, 1.0, "银行Ⅱ"), ("B", 1500, 2.0, "银行Ⅱ")])
        ov = svc.build_overview(db, now=NOW)
        concepts = {g["key"]: g for g in ov["groups"]["concept"]}
        assert concepts["BK1"]["count"] == 1 and concepts["BK2"]["count"] == 1
        assert concepts["BK1"]["watched"] is True and concepts["BK1"]["name"] == "CPO"
        a = next(s for s in ov["stocks"] if s["code"] == "A")
        assert a["concepts"] == ["BK1", "BK2"], "行业板块不算概念；重复的 BK 只算一次"
        assert ov["classification"]["with_f10"] == 1

    def test_过期标记(self, db, cal):
        _seed(db, D2, [("A", 1030, 1.0, "银行Ⅱ")])
        ov = svc.build_overview(db, now=datetime(2026, 11, 6, 17, 0))
        assert ov["is_stale"] is True and ov["expected_latest"] == D3
        ov = svc.build_overview(db, now=datetime(2026, 11, 6, 15, 40))
        assert ov["is_stale"] is False and ov["pending_today"] is True


class TestConfig:
    def test_默认与校验(self, db):
        assert svc.get_config(db)["threshold_yi"] == 1000
        assert svc.set_config(db, {"threshold_yi": 800})["threshold_yi"] == 800
        with pytest.raises(ValueError):
            svc.set_config(db, {"threshold_yi": -1})
        with pytest.raises(ValueError):
            svc.set_config(db, {"history_days": 7})
        assert svc.set_config(db, {}, reset=True)["custom_keys"] == []


class TestApi:
    @pytest.fixture
    def client(self, db):
        from app.database import get_db
        from app.routers import large_cap
        app = FastAPI()
        app.include_router(large_cap.router)
        app.dependency_overrides[get_db] = lambda: db
        return TestClient(app)

    def test_没有数据不是零值(self, client):
        r = client.get("/large-cap/overview")
        assert r.status_code == 200 and r.json()["status"] == "no_data"
        assert "summary" not in r.json()

    def test_不存在的日期是404(self, client, db, cal):
        _seed(db, D3, [("A", 1030, 1.0, "银行Ⅱ")])
        assert client.get("/large-cap/overview", params={"date": "2026-11-05"}).status_code == 404

    def test_改配置要登录(self, client):
        assert client.put("/large-cap/config", json={"threshold_yi": 800}).status_code in (401, 403)


class TestSchedulerIsolation:
    def test_大市值失败不影响日更(self, monkeypatch, tmp_path):
        from app import scheduler
        from app.services import daily_update_runner

        def boom(*a, **k):
            raise RuntimeError("子进程起不来")
        monkeypatch.setattr(daily_update_runner, "run_script_subprocess", boom)
        log = tmp_path / "u.log"
        scheduler._sync_large_cap(str(log), now=datetime(2026, 11, 6, 15, 45), wait=True)
        assert "不影响日更" in log.read_text()

    def test_另起线程_不拖着日更(self, monkeypatch, tmp_path):
        import threading
        from app import scheduler
        from app.services import daily_update_runner
        release = threading.Event()
        monkeypatch.setattr(daily_update_runner, "run_script_subprocess",
                            lambda *a, **k: release.wait(5) and 0)
        scheduler._sync_large_cap(str(tmp_path / "u.log"), now=datetime(2026, 11, 6, 15, 45))
        assert any(t.name == "large-cap-sync" for t in threading.enumerate()), "日更不等它"
        release.set()

    def test_盘前那次不跑(self, monkeypatch, tmp_path):
        from app import scheduler
        from app.services import daily_update_runner
        called = []
        monkeypatch.setattr(daily_update_runner, "run_script_subprocess",
                            lambda *a, **k: called.append(a) or 0)
        scheduler._sync_large_cap(str(tmp_path / "u.log"), now=datetime(2026, 11, 6, 9, 26))
        assert called == []
