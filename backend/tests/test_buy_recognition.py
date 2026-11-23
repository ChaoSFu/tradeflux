"""
买入识别（buy_recog_v1）的回归场景。规则是纯函数（buy_recognition_rules），直接喂事实测；
「切换历史日期」走盘后研究的 build_close，用空库测它不串实时结论。
"""
from datetime import date, datetime, timedelta

import pytest

from app.services import buy_recognition_rules as R
from app.services import buy_recognition_service as S

D = date(2026, 11, 23)


def t(hm: str) -> datetime:
    h, m = hm.split(":")
    return datetime(2026, 11, 23, int(h), int(m))


def minutes(start: str, prices) -> list:
    """从 start 起每分钟一个价。"""
    t0 = t(start)
    return [(t0 + timedelta(minutes=i), p) for i, p in enumerate(prices)]


def ld_rows(n: int, **kw) -> list:
    return [{"code": f"6000{i:02d}", "name": f"跌{i}", "seal_amount": 1e7 * (i + 1),
             "last_seal_time": "09:35:00", **kw} for i in range(n)]


def ld_item(rows, verify=None, *, pool_ok=True, pool_error=None, tc="rows", updown=None, updown_ok=None,
            market_time=t("10:30")):
    verify = {r["code"]: "sealed" for r in rows} if verify is None else verify
    return R.limit_down_item(phase_ok=True, phase_note="", pool_ok=pool_ok, pool_error=pool_error,
                             pool_tc=len(rows) if tc == "rows" else tc, rows=rows, verify=verify,
                             updown_ld=updown, updown_ok=updown is not None if updown_ok is None else updown_ok,
                             market_time=market_time, source_meta={"source": "东财跌停池（非 ST、沪深）"})


# ═══ ① 跌停家数 ═══════════════════════════════════════════════════════════

class TestLimitDown:
    @pytest.mark.parametrize("n,status,label", [
        (10, R.PASS, "≤10 家"), (11, R.BLOCK, "不开新仓"), (20, R.BLOCK, "不开新仓"), (21, R.BLOCK, "停手 · 空仓")])
    def test_10_11_20_21_的边界(self, n, status, label):
        x = ld_item(ld_rows(n))
        assert (x["status"], x["label"], x["count"]) == (status, label, n)

    def test_两套口径_58和61各自追得到(self):
        x = ld_item(ld_rows(58), updown=61)
        assert x["status"] == R.BLOCK and x["label"] == "停手 · 空仓" and x["count"] == 58
        assert "61" in x["reason"] and "含 ST、北交所" in x["reason"]
        assert x["meta"]["source"].startswith("东财跌停池（非 ST")
        assert x["facts"]["updown_limit_down"] == 61 and x["facts"]["sealed_now"] == 58

    def test_池里开了板的不计_行情核为准(self):
        rows = ld_rows(12)
        verify = {r["code"]: ("left" if i < 3 else "sealed") for i, r in enumerate(rows)}
        x = ld_item(rows, verify)
        assert x["status"] == R.PASS and x["count"] == 9 and "3 只" in x["reason"]

    @pytest.mark.parametrize("kw,why", [
        ({"pool_ok": False, "pool_error": "跌停池这次没等到（9 秒内没回）"}, "没等到"),
        ({"tc": 80}, "缺页"),
    ])
    def test_超时_缺页_不放行(self, kw, why):
        x = ld_item(ld_rows(5), **kw)
        assert x["status"] == R.INSUFFICIENT and why in x["reason"]

    def test_返回旧交易日_不放行(self):
        rows = ld_rows(6)
        x = ld_item(rows, {r["code"]: "stale" for r in rows})
        assert x["status"] == R.INSUFFICIENT and "上一交易日" in x["reason"]
        # 封板时间晚于此刻行情时间（开盘前拿到的是昨天 15:00 的名单）
        y = ld_item(ld_rows(6, last_seal_time="15:00:00"), market_time=t("09:31"))
        assert y["status"] == R.INSUFFICIENT and "旧交易日" in y["reason"]

    def test_空响应不放行_真0家正常(self):
        assert ld_item([], pool_ok=False, pool_error="东财跌停池空响应")["status"] == R.INSUFFICIENT
        zero = ld_item([], tc=0, updown=0)
        assert (zero["status"], zero["count"]) == (R.PASS, 0)
        # 跌停池 0、涨跌分布 25：分不清全是 ST 还是池子漏了
        assert ld_item([], tc=0, updown=25)["status"] == R.INSUFFICIENT

    def test_池子坏了但涨跌分布都不到10家_非ST一定更少(self):
        x = ld_item(ld_rows(3), pool_ok=False, pool_error="超时", updown=7)
        assert x["status"] == R.PASS and "7" in x["reason"]

    def test_没核上行情的票_计数跨档就是数据不足(self):
        rows = ld_rows(12)
        verify = {r["code"]: ("missing" if i < 4 else "sealed") for i, r in enumerate(rows)}
        x = ld_item(rows, verify)
        assert x["status"] == R.INSUFFICIENT and x["count_range"] == [8, 12]


# ═══ ② 最大空头 ═══════════════════════════════════════════════════════════

PC, LDP = 10.0, 9.0          # 昨收 10，跌停价 9


class TestBear:
    def test_封死(self):
        j = R.judge_bear(minutes("09:31", [9.0] * 30), prev_close=PC, ld_price=LDP, price_now=9.0)
        assert j["state"] == R.SEALED

    def test_短暂开板后回封(self):
        pts = minutes("09:31", [9.0] * 10 + [9.05, 9.1, 9.08] + [9.0] * 5)
        j = R.judge_bear(pts, prev_close=PC, ld_price=LDP, price_now=9.0)
        assert j["state"] == R.RESEALED

    def test_刚开板几分钟不算(self):
        pts = minutes("09:31", [9.0] * 10 + [9.1, 9.2, 9.3])
        j = R.judge_bear(pts, prev_close=PC, ld_price=LDP, price_now=9.3)
        assert j["state"] == R.TESTING and "才走完" in j["evidence"]

    def test_离开跌停_反弹_回踩守住_才算止跌(self):
        up = [9.0] * 5 + [9.05 + 0.03 * i for i in range(10)]          # 离开、反弹到 ~9.32
        pull = [9.25, 9.2, 9.18, 9.2, 9.24]                            # 回踩 9.18（高于跌停 1.8%）
        j = R.judge_bear(minutes("09:31", up + pull), prev_close=PC, ld_price=LDP, price_now=9.26)
        assert j["state"] == R.STOPPED, j
        assert j["pullback_low"] == 9.18 and j["hold_pct"] >= R.BEAR_HOLD_PCT

    def test_回踩又回到跌停附近_不算(self):
        up = [9.0] * 5 + [9.05 + 0.03 * i for i in range(10)]
        j = R.judge_bear(minutes("09:31", up + [9.2, 9.06, 9.1]), prev_close=PC, ld_price=LDP, price_now=9.12)
        assert j["state"] == R.TESTING and "没守稳" in j["evidence"]

    def test_没碰跌停的领跌股还在创新低(self):
        j = R.judge_bear(minutes("09:31", [9.8, 9.7, 9.6, 9.5, 9.45]), prev_close=PC, ld_price=LDP, price_now=9.45)
        assert j["state"] == R.NEW_LOW

    def test_跌停只有8家_最大空头开板后回封_市场一直不放行(self):
        ld = ld_item(ld_rows(8))
        assert ld["status"] == R.PASS
        target = {"code": "600099", "name": "空头", "basis": "封单最大",
                  "judge": R.judge_bear(minutes("09:31", [9.0] * 10 + [9.2] * 12 + [9.0] * 3),
                                        prev_close=PC, ld_price=LDP, price_now=9.0)}
        bear = R.bear_item([target], phase_ok=True, phase_note="")
        assert bear["status"] == R.BLOCK and bear["label"] == "开板后回封"
        status, worst = R.combine([ld, bear, _pass("index"), _pass("highmark")])
        assert status == R.BLOCK and worst["key"] == "bear"
        assert R.pick_entry(status, [_confirmed_mainline()])[0] is None


# ═══ ③ 大盘 / ④ 高标 ══════════════════════════════════════════════════════

def _pass(key):
    return R.item(key, key, R.PASS, "ok", "ok")


def _rebound_index():
    return minutes("09:31", [100 - 0.1 * i for i in range(10)] + [99.2 + 0.05 * i for i in range(20)])


class TestIndexAndHighmark:
    def test_指数低点守住反弹(self):
        j = R.judge_index(_rebound_index(), prev_close=100, price_now=100.2)
        assert j["ok"] is True and j["low_at"] == "09:40"
        k = R.judge_index(minutes("09:31", [100 - 0.1 * i for i in range(20)]), prev_close=100, price_now=98.1)
        assert k["ok"] is False

    def test_银行抬指数_题材高标没修复_市场继续等(self):
        idx = [{"code": c, "name": n, "judge": R.judge_index(_rebound_index(), prev_close=100, price_now=100.2)}
               for c, n in (("000001", "上证指数"), ("399001", "深证成指"))]
        x = R.index_item(idx, volume={"ratio": 1.1, "same_as_of": "10:30"}, breadth={"up": 1200, "down": 3800},
                         phase_ok=True, phase_note="")
        assert x["status"] == R.NOT_MET and x["label"] == "权重托举"
        hm = R.highmark_item({"sample": 12, "quoted": 12, "limit_down": 0, "median_pct": -2.3, "red_ratio": 0.2,
                              "limit_down_names": []}, prev=None, worst_median=-2.5, phase_ok=True, phase_note="",
                             scope="")
        assert hm["status"] == R.NOT_MET and hm["label"] == "负反馈未收敛"
        status, _ = R.combine([_pass("limit_down"), _pass("bear"), x, hm])
        assert status == R.NOT_MET

    def test_缩量反弹不算放量(self):
        idx = [{"code": "1", "name": "A", "judge": {"ok": True}}, {"code": "2", "name": "B", "judge": {"ok": True}}]
        x = R.index_item(idx, volume={"ratio": 0.85, "same_as_of": "10:30"}, breadth={"up": 3000, "down": 2000},
                         phase_ok=True, phase_note="")
        assert x["status"] == R.NOT_MET and x["label"] == "缩量" and "10:30" in x["reason"]

    def test_高标_转正要隔一次观测没再走弱(self):
        st = {"sample": 10, "quoted": 10, "limit_down": 0, "median_pct": 1.2, "red_ratio": 0.7, "limit_down_names": []}
        first = R.highmark_item(st, prev=None, worst_median=None, phase_ok=True, phase_note="", scope="")
        assert first["status"] == R.NOT_MET and first["label"] == "转正待确认"
        ok = R.highmark_item(st, prev={"at": "10:01:00", "median_pct": 0.9}, worst_median=-1, phase_ok=True,
                             phase_note="", scope="")
        assert ok["status"] == R.PASS
        worse = R.highmark_item({**st, "median_pct": 0.3}, prev={"at": "10:01:00", "median_pct": 1.5},
                                worst_median=-1, phase_ok=True, phase_note="", scope="")
        assert worse["label"] == "又在走弱"

    def test_高位跌停拦截_单只大涨样本不足保留待确认(self):
        st = {"sample": 10, "quoted": 10, "limit_down": 1, "median_pct": 2.0, "red_ratio": 0.8,
              "limit_down_names": ["某高标"]}
        assert R.highmark_item(st, prev=None, worst_median=None, phase_ok=True, phase_note="", scope="")["status"] == R.BLOCK
        thin = {"sample": 10, "quoted": 3, "limit_down": 0, "median_pct": 9.9, "red_ratio": 1.0, "limit_down_names": []}
        assert R.highmark_item(thin, prev=None, worst_median=None, phase_ok=True, phase_note="",
                               scope="")["status"] == R.INSUFFICIENT

    def test_仍为负但改善_修复中(self):
        st = {"sample": 10, "quoted": 10, "limit_down": 0, "median_pct": -0.8, "red_ratio": 0.4, "limit_down_names": []}
        x = R.highmark_item(st, prev=None, worst_median=-3.0, phase_ok=True, phase_note="", scope="")
        assert x["label"] == "修复中"

    def test_进程内存_上一次观测要隔60秒(self):
        s = S._DayState()
        assert s.hm_observe(D, t("10:00"), -1.0) == (None, None)
        assert s.hm_observe(D, t("10:00") + timedelta(seconds=30), 0.5)[0] is None, "30 秒内不算上一次"
        prev, worst = s.hm_observe(D, t("10:01") + timedelta(seconds=10), 0.8)
        assert prev["median_pct"] == -1.0 and worst == -1.0

    def test_进程内存_封单锁定_离池后继续跟踪_更重的加进来(self):
        s = S._DayState()
        a = {"code": "600001", "name": "甲", "seal_amount": 5e7}
        b = {"code": "600002", "name": "乙", "seal_amount": 9e7}
        assert [x["code"] for x in s.seal_targets(D, [a], "09:31:00", trusted=True)] == ["600001"]
        assert [x["code"] for x in s.seal_targets(D, [], "09:40:00", trusted=True)] == ["600001"], "甲开板离池照样跟踪"
        assert [x["code"] for x in s.seal_targets(D, [b], "10:00:00", trusted=True)] == ["600001", "600002"]
        assert s.seal_targets(D, [], "x", trusted=False)[1]["basis"].startswith("10:00:00 出现更重的封单")
        assert s.seal_targets(D + timedelta(days=1), [], "x", trusted=False) == [], "换一天清空"


# ═══ 主线 / 带队 ══════════════════════════════════════════════════════════

def _br(up=40, down=10, strong=5, median=2.5, members=50, quoted=50):
    return {"members": members, "quoted": quoted, "up": up, "down": down, "up_ratio": up / (up + down),
            "median_pct": median, "strong": strong, "strong_names": []}


def _sector_wave(pullback=True, restrength=True, rs_held=True):
    """上证：先跌后弹；板块：比上证早见底、冲高、（回踩）、（再走强）。返回 (板块点, 上证点)。"""
    idx = [100 - 0.05 * i for i in range(10)] + [99.5 + 0.02 * i for i in range(40)]
    sec = [100 - 0.1 * i for i in range(5)] + [99.5 + 0.25 * i for i in range(20)]     # 09:35 见底，冲到 ~104.25
    if pullback:
        low = 103.3 if rs_held else 99.3
        sec += [104.0, 103.7, low, 103.5]
        sec += ([104.0, 104.2, 104.3] if restrength else [103.4, 103.45, 103.4])
    sec += [sec[-1]] * (50 - len(sec))
    return minutes("09:31", sec), minutes("09:31", idx)


class TestMainline:
    def test_确认走强_三项证据(self):
        sec, idx = _sector_wave()
        s = R.sector_series(sec, 100, idx, 100)
        st, label, _ = R.mainline_judge(origin="continuation", br=_br(), series=s, series_error=None, idx_pct_now=0.3)
        assert st == R.CONFIRMED and s["lead_ok"] and s["rs_held"] and s["restrength"]

    def test_新题材只一轮脉冲_待确认(self):
        sec, idx = _sector_wave(pullback=False)
        s = R.sector_series(sec, 100, idx, 100)
        st, label, _ = R.mainline_judge(origin="new", br=_br(), series=s, series_error=None, idx_pct_now=0.3)
        assert (st, label) == (R.PENDING, "新方向待确认")

    def test_新题材仅一股_广度不够(self):
        sec, idx = _sector_wave()
        st, label, _ = R.mainline_judge(origin="new", br=_br(up=12, down=30, strong=1), series=R.sector_series(sec, 100, idx, 100),
                                        series_error=None, idx_pct_now=0.3)
        assert (st, label) == (R.NOT_MET, "广度不够")

    def test_回踩时跑输上证_不确认(self):
        sec, idx = _sector_wave(rs_held=False)
        st, _, _ = R.mainline_judge(origin="continuation", br=_br(), series=R.sector_series(sec, 100, idx, 100),
                                    series_error=None, idx_pct_now=0.3)
        assert st != R.CONFIRMED

    def test_没有板块分时_只留候选并注明(self):
        st, label, reason = R.mainline_judge(origin="continuation", br=_br(median=3.0), series=None,
                                             series_error="板块分时取数失败", idx_pct_now=0.5)
        assert st == R.CANDIDATE and "缺" in label and "板块分时" in reason

    def test_重叠概念按成员重合归并_不重复计数(self):
        same = {"a", "b", "c", "d"}
        groups = R.merge_groups([
            {"sid": 1, "code": "BK1", "name": "算力", "type": "concept", "members_n": 80, "active": set(same), "origin": "new"},
            {"sid": 2, "code": "BK2", "name": "云计算", "type": "concept", "members_n": 120, "active": set(same), "origin": "new"},
            {"sid": 3, "code": "BK3", "name": "数据中心", "type": "concept", "members_n": 60, "active": same | {"e"}, "origin": "new"},
            {"sid": 4, "code": "BK4", "name": "白酒", "type": "industry", "members_n": 20, "active": {"x", "y", "z"}, "origin": "new"},
        ])
        assert len(groups) == 2
        g = groups[0]
        assert g["name"] == "数据中心" and sorted(g["merged"]) == ["云计算", "算力"] and len(g["active"]) == 5

    def test_板块确认但个股先后缺失_带队待验证_没有入口(self):
        c = {"code": "600100", "name": "带队", "lead_at": None, "support": True, "support_txt": ""}
        lj = R.leader_judge([c], [{"code": "600101", "name": "跟1", "at": "10:01:00"}])
        assert lj["status"] == R.PENDING
        ml = _confirmed_mainline(leader=lj)
        assert R.pick_entry(R.PASS, [ml])[0] is None

    def test_同一分钟分不出先后_只标同向(self):
        c = {"code": "600100", "name": "甲", "lead_at": "09:45", "lead_basis": "首封", "support": True, "support_txt": "封住"}
        lj = R.leader_judge([c], [{"code": "600101", "name": "乙", "at": "09:45:30"},
                                  {"code": "600102", "name": "丙", "at": "10:00:00"}])
        assert lj["status"] == "SAME"

    def test_两关通过带队齐全_入口带入正确股票和板块(self):
        c = {"code": "600100", "name": "带队", "lead_at": "09:41", "lead_basis": "首封", "support": True,
             "support_txt": "封住，没给回落"}
        lj = R.leader_judge([c], [{"code": "600101", "name": "跟1", "at": "09:52:00"},
                                  {"code": "600102", "name": "跟2", "at": "10:05:00"}])
        assert lj["status"] == R.CONFIRMED and [f["name"] for f in lj["followers"]] == ["跟1", "跟2"]
        entry, why = R.pick_entry(R.PASS, [_confirmed_mainline(leader=lj, sid=321, name="算力")])
        assert entry == {"code": "600100", "name": "带队", "sector_id": 321, "sector_name": "算力"}
        assert "买入检查" in why

    def test_承接_回落守住一半(self):
        pts = minutes("09:31", [10.0, 10.3, 10.6, 10.8, 10.7, 10.6, 10.65])
        ok, txt = R.support_of(limit=None, broken_times=None, pts=pts, prev_close=10.0)
        assert ok is True
        bad, _ = R.support_of(limit=None, broken_times=None, pts=minutes("09:31", [10.0, 10.8, 10.2, 10.1]), prev_close=10.0)
        assert bad is False
        assert R.lead_time(first_time=None, pts=minutes("09:31", [10.0, 10.2, 10.4]), prev_close=10.0) == (None, "")
        assert R.lead_time(first_time=None, pts=pts, prev_close=10.0)[0] == "09:33"
        assert R.lead_time(first_time="09:41:07", pts=None, prev_close=None) == ("09:41", "首封 / 首次触板")

    def test_回踩完又创新高_照样算回踩过(self):
        # 板块：冲高、回踩、再创新高——最高点在最后，不能因此说「还没回踩」
        sec, idx = _sector_wave()
        s = R.sector_series(sec, 100, idx, 100)
        assert s["had_pullback"] and s["day_high_pct"] > s["high_pct"]
        # 空头：离开跌停、反弹、回踩守住、再创新高 → 止跌
        up = [9.0] * 5 + [9.05 + 0.03 * i for i in range(10)] + [9.2, 9.18, 9.25, 9.4, 9.5]
        assert R.judge_bear(minutes("09:31", up), prev_close=PC, ld_price=LDP, price_now=9.5)["state"] == R.STOPPED
        # 带队：回落守住后创新高 → 有承接
        ok, _ = R.support_of(limit=None, broken_times=None,
                             pts=minutes("09:31", [10.0, 10.5, 10.8, 10.55, 10.7, 10.95]), prev_close=10.0)
        assert ok is True


def _confirmed_mainline(leader=None, sid=1, name="板块"):
    return {"sector": {"id": sid, "code": "BK1", "name": name}, "status": R.CONFIRMED,
            "leader": leader or {"status": R.CONFIRMED, "leader": {"code": "600100", "name": "带队"}}}


# ═══ 切换历史日期：盘后研究不串实时结论 ════════════════════════════════════

class TestCloseMode:
    def test_历史日期只摆收盘事实_不给结论和入口(self, db):
        out = S.build_close(db, date(2026, 11, 20), [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)])
        assert out["mode"] == "close" and out["entry"] is None
        assert out["market"]["status"] != R.PASS
        items = {x["key"]: x for x in out["market"]["items"]}
        assert items["bear"]["label"] == S.NO_INTRADAY and items["index"]["label"] == S.NO_INTRADAY

    def test_盘中跟踪_不在交易时段不给结论(self, db):
        sess = {"phase": "pre_open", "data_date": date(2026, 11, 20), "ref_date": date(2026, 11, 20)}
        out = S.build_live(db, sess=sess, cal=[], market_dt=None, local_now=t("09:10"), facts={}, core_items=[],
                           pools=None, pools_err=None, breadth=None, breadth_err=None, amount=None, amount_err=None,
                           idx_quotes=None)
        assert out["market"]["status"] == R.INSUFFICIENT and out["entry"] is None
        assert "开盘后再判" in out["market"]["reason"]
