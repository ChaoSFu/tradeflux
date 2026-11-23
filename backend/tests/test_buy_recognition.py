"""
买入识别（buy_recog_v2）的回归场景。

前半部分直接喂事实测纯函数（buy_recognition_rules）；后半部分 **端到端**：库里种好股票 / 板块 / 日快照，
把所有外部源（跌停池、行情、分时、板块分时、涨跌分布、成交额、涨停池）换成一个可控的「盘中世界」，
从 live_recognition 一路跑到卡片要的 JSON——验收评审列的每个场景。
"""
import json
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.app_config import AppConfig
from app.models.sector import Sector, StockSectorRelation
from app.models.stock import Stock, StockDailySnapshot
from app.services import buy_recognition_rules as R
from app.services import buy_recognition_service as S
from app.services import market_recap_live_service as L
from app.services import pre_trade_rules as PR
from app.services.eastmoney_fetcher import MinuteBar, StockQuote
from app.services.limit_up_detail_fetcher import LimitDownDetail
from app.services.windvane_service import _aligned_same

D = date(2026, 11, 23)
CAL = [date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20), D]
TZ = timezone(timedelta(hours=8))


def t(hm: str, d: date = D) -> datetime:
    h, m = hm.split(":")[:2]
    return datetime(d.year, d.month, d.day, int(h), int(m))


def tmins(n: int, start: str = "09:31", d: date = D) -> list:
    """从 start 起 n 根分钟的结束时刻，跳过午休。"""
    out, x = [], t(start, d)
    while len(out) < n:
        if not (t("11:30", d) < x <= t("13:00", d)):
            out.append(x)
        x += timedelta(minutes=1)
    return out


def minutes(start: str, prices) -> list:
    return list(zip(tmins(len(prices), start), prices))


# ═══ ① 跌停家数（纯函数） ════════════════════════════════════════════════════

def ld_rows(n: int, **kw) -> list:
    return [{"code": f"6000{i:02d}", "name": f"跌{i}", "seal_amount": 1e7 * (i + 1),
             "last_seal_time": "09:35:00", **kw} for i in range(n)]


def ld_item(rows, verify=None, *, pool_ok=True, pool_error=None, tc="rows", updown=None, complete=True,
            market_time=t("10:30")):
    verify = {r["code"]: "sealed" for r in rows} if verify is None else verify
    return R.limit_down_item(pool_ok=pool_ok, pool_error=pool_error, pool_tc=len(rows) if tc == "rows" else tc,
                             rows=rows, verify=verify, updown_ld=updown, updown_complete=complete,
                             market_time=market_time, source_meta={"source": "东财跌停池（非 ST、沪深）"})


class TestLimitDownRules:
    @pytest.mark.parametrize("n,status,label,stop", [
        (10, R.PASS, "≤10 家", False), (11, R.BLOCK, "不开新仓", False), (20, R.BLOCK, "不开新仓", False),
        (21, R.BLOCK, "停手 · 空仓", True)])
    def test_10_11_20_21_的边界(self, n, status, label, stop):
        x = ld_item(ld_rows(n))
        assert (x["status"], x["label"], x["count"], x["stop_all"]) == (status, label, n, stop)

    def test_9只确认加3只旧报价_上界12跨档_数据不足(self):
        rows = ld_rows(12)
        verify = {r["code"]: ("stale" if i >= 9 else "sealed") for i, r in enumerate(rows)}
        x = ld_item(rows, verify)
        assert x["status"] == R.INSUFFICIENT and x["count_range"] == [9, 12]
        assert "过期或没核上" in x["reason"]

    def test_下界已经超过10_不用等全核完就拦截(self):
        rows = ld_rows(30)
        verify = {r["code"]: ("missing" if i >= 12 else "sealed") for i, r in enumerate(rows)}
        x = ld_item(rows, verify)
        assert (x["status"], x["label"], x["count"]) == (R.BLOCK, "不开新仓", 12) and "可能更多" in x["reason"]
        y = ld_item(ld_rows(25), pool_ok=True, tc=None)                    # 没给总数也一样：下界 25 已经 >20
        assert (y["status"], y["label"], y["stop_all"]) == (R.BLOCK, "停手 · 空仓", True)

    def test_真0家_与_缺总数的空池(self):
        zero = ld_item([], tc=0, updown=3)
        assert (zero["status"], zero["count"]) == (R.PASS, 0)
        unknown = ld_item([], tc=None)
        assert unknown["status"] == R.INSUFFICIENT and "没给总数" in unknown["reason"]
        assert ld_item([], tc=0, updown=25)["status"] == R.INSUFFICIENT, "池子 0、涨跌分布 25：分不清是不是漏了"

    def test_缺页_差的那几行算进上界(self):
        assert ld_item(ld_rows(8), tc=9)["status"] == R.PASS, "自报 9 只、拿到 8 行：上界 9 仍 ≤10"
        x = ld_item(ld_rows(8), tc=14)
        assert x["status"] == R.INSUFFICIENT and x["count_range"] == [8, 14] and "缺页" in x["reason"]

    def test_池子坏了_涨跌分布只有沪深都返回才能当上界(self):
        """替换 v1 的「跌停池失败 + 涨跌分布 7 家 → 通过」：单市场返回的 7 家只是一部分。"""
        partial = ld_item([], pool_ok=False, pool_error="超时", updown=7, complete=False)
        assert partial["status"] == R.INSUFFICIENT and "只作对照" in partial["reason"]
        full = ld_item([], pool_ok=False, pool_error="超时", updown=7, complete=True)
        assert full["status"] == R.PASS and full["facts"]["upper_source"].startswith("东财涨跌分布")

    def test_返回旧交易日的名单_上界未知(self):
        rows = ld_rows(6)
        x = ld_item(rows, {r["code"]: "stale" for r in rows})
        assert x["status"] == R.INSUFFICIENT and "上一交易日" in x["reason"]
        y = ld_item(ld_rows(6, last_seal_time="15:00:00"), market_time=t("09:31"))
        assert y["status"] == R.INSUFFICIENT and "旧交易日" in y["reason"]

    def test_池里开了板的不计(self):
        rows = ld_rows(12)
        x = ld_item(rows, {r["code"]: ("left" if i < 3 else "sealed") for i, r in enumerate(rows)})
        assert x["status"] == R.PASS and x["count"] == 9 and "3 只" in x["reason"]


# ═══ ② 最大空头（纯函数） ════════════════════════════════════════════════════

PC, LDP = 10.0, 9.0          # 昨收 10，跌停价 9
STOPPED_PX = [9.0] * 5 + [9.05 + 0.03 * i for i in range(10)] + [9.25, 9.2, 9.18, 9.2, 9.24] + [9.3] * 40
RESEALED_PX = [9.0] * 10 + [9.2] * 12 + [9.0] * 38


def _bear(px, now):
    return {"code": "600900", "name": "空头", "basis": "x",
            "judge": R.judge_bear(minutes("09:31", px), prev_close=PC, ld_price=LDP, price_now=now)}


class TestBearRules:
    def test_封死_回封_刚开板(self):
        assert R.judge_bear(minutes("09:31", [9.0] * 30), prev_close=PC, ld_price=LDP, price_now=9.0)["state"] == R.SEALED
        assert _bear(RESEALED_PX, 9.0)["judge"]["state"] == R.RESEALED
        j = R.judge_bear(minutes("09:31", [9.0] * 10 + [9.1, 9.2, 9.3]), prev_close=PC, ld_price=LDP, price_now=9.3)
        assert j["state"] == R.TESTING and "才走完" in j["evidence"]

    def test_离开跌停_反弹_回踩守住_才算止跌_回到跌停附近撤销(self):
        assert _bear(STOPPED_PX, 9.3)["judge"]["state"] == R.STOPPED
        later = STOPPED_PX + [9.1, 9.05, 9.0]
        assert _bear(later, 9.0)["judge"]["state"] == R.RESEALED, "止跌之后又回封：每次重算，自动撤销"

    def test_两项独立证据_封单锚三种状态(self):
        ok3 = R.bear_3d_item(_bear(STOPPED_PX, 9.3))
        assert ok3["status"] == R.PASS and ok3["key"] == "bear_3d"
        assert R.bear_seal_item([], anchor=R.SEAL_UNVERIFIED, pool_trusted_now=False)["label"] == "封单锚待核"
        assert R.bear_seal_item([], anchor=R.SEAL_NONE, pool_trusted_now=True)["status"] == R.PASS
        assert R.bear_seal_item([], anchor=R.SEAL_UNRECOVERABLE, pool_trusted_now=True, error="x")["status"] == R.INSUFFICIENT
        locked = [_bear(STOPPED_PX, 9.3)]
        assert R.bear_seal_item(locked, anchor=R.SEAL_LOCKED, pool_trusted_now=True)["status"] == R.PASS
        assert R.bear_seal_item(locked, anchor=R.SEAL_LOCKED, pool_trusted_now=False)["status"] == R.INSUFFICIENT, \
            "锁定的都止跌了，但这次跌停池不可信：可能冒出更重的封单"
        rs = R.bear_seal_item([_bear(RESEALED_PX, 9.0)], anchor=R.SEAL_LOCKED, pool_trusted_now=True)
        assert rs["status"] == R.BLOCK and rs["label"] == "开板后回封"


# ═══ 时效、同期比、并发合并 ═══════════════════════════════════════════════════

class TestFreshness:
    def test_按交易分钟算_午休不算(self):
        assert R.trade_lag_min(t("11:30"), t("13:00") + timedelta(seconds=40)) < 1
        assert R.trade_lag_min(t("11:30"), t("13:10")) == 10
        assert R.trade_lag_min(t("11:30"), t("15:00")) == 120
        assert R.trade_lag_min(t("15:00", date(2026, 11, 20)), t("10:00")) == float("inf")
        assert R.minute_staleness(minutes("09:31", [1.0] * 120), t("15:00")).startswith("分时停在 11:30")
        assert R.minute_staleness(minutes("09:31", [1.0] * 60), t("10:32")) is None

    def test_同期比_两市都要有_截到共同时刻_按结束时刻对齐(self):
        def mk(today: dict, yday: dict, done=None):
            return {"by": {"2026-11-20": yday, "2026-11-23": today},
                    "done": {"2026-11-23": set(done if done is not None else today)}}
        sh = mk({"09:35": 10, "09:40": 12}, {"09:35": 9, "09:40": 11, "09:45": 30})
        sz_lag = mk({"09:35": 20}, {"09:35": 18, "09:40": 22})
        out = _aligned_same({"sh": sh, "sz": sz_lag}, ("sh", "sz"))
        assert (out["same_as_of"], out["same_amount"], out["prev_same"]) == ("09:35", 30, 27), "深市慢一根：两边都截到 09:35"
        miss = _aligned_same({"sh": sh}, ("sh", "sz"))
        assert miss["same_amount"] is None and "缺" in miss["same_error"]
        gap = _aligned_same({"sh": mk({"09:35": 10, "09:40": 12}, {"09:35": 9}), "sz": mk({"09:35": 1, "09:40": 1}, {"09:35": 1, "09:40": 1})},
                            ("sh", "sz"))
        assert gap["same_amount"] is None and "对不齐" in gap["same_error"], "昨天缺 09:40 那根，不能按根数错位"

    def test_同键并发合并_只打一次外部源(self):
        L.clear_cache()
        calls = Counter()

        def slow():
            calls["n"] += 1
            time.sleep(0.2)
            return {"v": 1}
        got = []
        ths = [threading.Thread(target=lambda: got.append(L._cached(("k",), 30, slow))) for _ in range(5)]
        [x.start() for x in ths]
        [x.join() for x in ths]
        assert calls["n"] == 1 and got == [{"v": 1}] * 5


# ═══ ③ 大盘 / ④ 高标（纯函数） ══════════════════════════════════════════════

def _rebound_index():
    return minutes("09:31", [100 - 0.1 * i for i in range(10)] + [99.2 + 0.05 * i for i in range(20)])


class TestIndexAndHighmark:
    def test_指数低点守住反弹_权重托举_缩量(self):
        j = R.judge_index(_rebound_index(), prev_close=100, price_now=100.2)
        assert j["ok"] is True and j["low_at"] == "09:40"
        idx = [{"code": c, "name": n, "judge": j} for c, n in (("000001", "上证指数"), ("399001", "深证成指"))]
        x = R.index_item(idx, volume={"ratio": 1.1, "same_as_of": "10:30"}, breadth={"up": 1200, "down": 3800},
                         phase_ok=True, phase_note="")
        assert (x["status"], x["label"]) == (R.NOT_MET, "权重托举")
        y = R.index_item(idx, volume={"ratio": 0.85, "same_as_of": "10:30"}, breadth={"up": 3000, "down": 2000},
                         phase_ok=True, phase_note="")
        assert y["label"] == "缩量"
        z = R.index_item(idx, volume=None, volume_error="缺深市的分钟成交额，同期比不算", breadth={"up": 3000, "down": 2000},
                         phase_ok=True, phase_note="")
        assert z["status"] == R.INSUFFICIENT and "缺深市" in z["reason"]

    def test_高位跌停先拦截_覆盖不够也拦(self):
        st = {"sample": 10, "quoted": 3, "limit_down": 1, "median_pct": 2.0, "red_ratio": 0.8, "limit_down_names": ["某高标"]}
        x = R.highmark_item(st, prev=None, worst_median=None, phase_ok=True, phase_note="", scope="")
        assert x["status"] == R.BLOCK, "已核实的高位跌停优先拦截，不因为覆盖不够降成数据不足"
        thin = {**st, "limit_down": 0, "limit_down_names": []}
        y = R.highmark_item(thin, prev=None, worst_median=None, phase_ok=True, phase_note="", scope="")
        assert y["status"] == R.INSUFFICIENT and "清零" in y["reason"]

    def test_转正要隔一次观测_同一批票没再走弱(self):
        st = {"sample": 10, "quoted": 10, "limit_down": 0, "median_pct": 1.2, "red_ratio": 0.7, "limit_down_names": []}
        assert R.highmark_item(st, prev=None, worst_median=None, phase_ok=True, phase_note="", scope="")["label"] == "转正待确认"
        ok = R.highmark_item(st, prev={"at": "10:01:00", "median_pct": 0.9, "median_now": 1.1, "common": 9},
                             worst_median=-1, phase_ok=True, phase_note="", scope="")
        assert ok["status"] == R.PASS and "同一批 9 只" in ok["reason"]
        worse = R.highmark_item(st, prev={"at": "10:01:00", "median_pct": 1.8, "median_now": 1.0, "common": 9},
                                worst_median=-1, phase_ok=True, phase_note="", scope="")
        assert worse["label"] == "又在走弱", "全样本中位 1.2 看着没走弱，同一批票其实从 1.8 掉到 1.0"


# ═══ 主线 / 带队（纯函数） ══════════════════════════════════════════════════

def _br(up=40, down=10, strong=5, median=2.5, members=50, quoted=50):
    return {"members": members, "quoted": quoted, "up": up, "down": down, "up_ratio": up / (up + down),
            "median_pct": median, "strong": strong, "strong_names": []}


IDX_PX = [100 - 0.05 * i for i in range(10)] + [99.55 + 0.01 * i for i in range(50)]          # 09:40 见底，慢慢回到 ~100.04
SEC_PX = ([100 - 0.1 * i for i in range(5)] + [99.6 + 0.25 * i for i in range(20)]           # 09:35 见底，冲到 104.35
          + [104.3, 104.0, 103.7, 104.0, 104.4, 104.8, 105.0])                                 # 回踩 103.7，再上攻
SEC_PX = SEC_PX + [105.0] * (60 - len(SEC_PX))


class TestMainlineRules:
    def test_同一轮_确认走强(self):
        s = R.sector_series(minutes("09:31", SEC_PX), 100, minutes("09:31", IDX_PX), 100)
        assert s["round_start"] == "09:36" and s["lead_ok"] and s["rs_held"] and s["restrength"]
        st, _, reason = R.mainline_judge(origin="continuation", br=_br(), series=s, series_error=None, idx_pct_now=0.0)
        assert st == R.CONFIRMED and "这一轮 09:36 起" in reason

    def test_高开低走_对昨收跑赢但这一轮没跑赢(self):
        sec = [103.0] * 5 + [102.9 - 0.02 * i for i in range(55)]
        s = R.sector_series(minutes("09:31", sec), 100, minutes("09:31", IDX_PX), 100)
        assert s["rs_now"] > 1, "对昨收看，板块比上证多涨 1 个多点"
        st, label, _ = R.mainline_judge(origin="continuation", br=_br(), series=s, series_error=None, idx_pct_now=0.0)
        assert (st, label) == (R.NOT_MET, "没跑赢")

    def test_没有板块分时_只留候选(self):
        st, label, reason = R.mainline_judge(origin="continuation", br=_br(median=3.0), series=None,
                                             series_error="分时停在 10:01，过期了", idx_pct_now=0.5)
        assert st == R.CANDIDATE and "过期" in reason

    def test_重叠概念归并只影响展示_代表板块自己的活跃单独留着(self):
        same = {"a", "b", "c", "d"}
        groups = R.merge_groups([
            {"sid": 1, "code": "BK1", "name": "算力", "type": "concept", "members_n": 80, "active": set(same), "origin": "new"},
            {"sid": 3, "code": "BK3", "name": "数据中心", "type": "concept", "members_n": 60, "active": same | {"e"}, "origin": "new"},
        ])
        assert len(groups) == 1 and groups[0]["name"] == "数据中心" and groups[0]["own_active"] == same | {"e"}


def _c(code, name, touch=None, pct5=None, support=True, weak=False, **kw):
    return {"code": code, "name": name, "touch_at": touch, "pct5_at": pct5, "lead_at": touch or pct5,
            "lead_basis": "首封" if touch else "分时第一次 +5%", "support": support, "support_txt": "承接",
            "weak": weak, "weak_txt": "回落没守住" if weak else None, **kw}


def _peers(*xs):
    return [{"code": c, "name": n, "at": a} for c, n, a in xs]


class TestLeaderRules:
    def test_带队确认_跟进限同一轮(self):
        lj = R.leader_judge([_c("1", "甲", "09:41")], _peers(("1", "甲", "09:41:00"), ("2", "乙", "09:52:00"),
                                                             ("3", "丙", "10:05:00")), round_start="09:35")
        assert lj["status"] == R.CONFIRMED and [f["name"] for f in lj["followers"]] == ["乙", "丙"]
        old_round = R.leader_judge([_c("1", "甲", "09:41")], _peers(("2", "乙", "09:52:00"), ("3", "丙", "10:05:00")),
                                   round_start="10:00")
        assert old_round["status"] == R.PENDING and "这一轮从 10:00 起" in old_round["reason"]

    def test_同一分钟只标同向(self):
        lj = R.leader_judge([_c("1", "甲", "09:45")], _peers(("2", "乙", "09:45:30"), ("3", "丙", "10:00:00")))
        assert lj["status"] == "SAME"

    def test_启动事件不可比_不硬排先后(self):
        # 丙没触板，分时 09:38 就 +5%；甲 09:41 首次触板，甲自己的 +5% 时刻没取到 → 比不了
        lj = R.leader_judge([_c("1", "甲", "09:41"), _c("3", "丙", pct5="09:38")],
                            _peers(("2", "乙", "09:52:00"), ("4", "丁", "10:05:00")))
        assert lj["status"] == R.PENDING and "依据不可比" in lj["reason"]
        # 取到了甲的 +5% 时刻（09:36），两只都按 +5% 比：甲更早
        ok = R.leader_judge([_c("1", "甲", "09:41", pct5="09:36"), _c("3", "丙", pct5="09:38")],
                            _peers(("2", "乙", "09:52:00"), ("4", "丁", "10:05:00")))
        assert ok["status"] == R.CONFIRMED
        # 带队本身没触板：跟进按触板算，依据不可比
        nt = R.leader_judge([_c("3", "丙", pct5="09:38")], _peers(("2", "乙", "09:52:00"), ("4", "丁", "10:05:00")))
        assert nt["status"] == R.PENDING and "没触板" in nt["reason"]

    def test_最早的走弱了_评接力者(self):
        lj = R.leader_judge([_c("1", "甲", "09:41", support=False, weak=True), _c("2", "乙", "09:52")],
                            _peers(("1", "甲", "09:41:00"), ("2", "乙", "09:52:00"), ("3", "丙", "10:01:00"),
                                   ("4", "丁", "10:08:00")))
        assert lj["status"] == R.CONFIRMED and lj["leader"]["code"] == "2" and lj["relay_from"][0]["code"] == "1"
        assert "看接力" in lj["reason"]

    def test_封住没开过只说明此刻封着(self):
        ok, txt = R.support_of(limit="sealed", broken_times=0, pts=None, prev_close=None)
        assert ok is True and "没经过回落检验" in txt
        assert R.seal_state("sealed", 2, True) == "炸板 2 次后回封" and R.seal_state("broken", 1, True) == "触板后开板"

    def test_首屏和按钮指向同一条主线(self):
        pend = {"sector": {"id": 1, "code": "A", "name": "甲板块"}, "status": R.CONFIRMED,
                "leader": {"status": R.PENDING, "leader": {"code": "x"}}}
        conf = {"sector": {"id": 2, "code": "B", "name": "乙板块"}, "status": R.CONFIRMED,
                "leader": {"status": R.CONFIRMED, "leader": {"code": "600100", "name": "带队"}}}
        entry, _, focus = R.pick_entry(R.PASS, [pend, conf])
        assert focus == 1 and entry["sector_id"] == 2 and entry["code"] == "600100"
        none, why, _ = R.pick_entry(R.PASS, [conf], phase="lunch")
        assert none is None and "午间休市" in why


# ═══ 端到端：盘中世界 ═══════════════════════════════════════════════════════

def _q(code, price, prev=10.0, at="10:30:00", high=None, d=D):
    h, m, s = (int(x) for x in at.split(":"))
    return StockQuote(code=code, name="", price=price, pct_change=round((price / prev - 1) * 100, 2), open=prev,
                      high=high if high is not None else max(price, prev), low=min(price, prev), prev_close=prev,
                      volume=1e6, amount=1e8, turnover_rate=None, trade_date=d,
                      trade_dt=datetime(d.year, d.month, d.day, h, m, s, tzinfo=TZ))


class World:
    """一个盘中时刻：所有外部源都从这里出，计数每一路被打了几次。库里种好两个关注板块和近 3 日日快照。"""

    def __init__(self, db, mp, now="10:30:00"):
        self.db, self.now = db, now
        self.calls: Counter = Counter()
        self.quoted: set = set()
        self.quotes: dict = {}
        self.minutes: dict = {}         # code -> (昨收, [价…]) 从 09:31 起
        self.board: dict = {}           # 板块代码 -> (昨收, [价…])
        self.pool = ([], 0)             # (行, tc)，或一个异常
        self.breadth = {"up": 3200, "down": 1500, "limit_down": 3, "markets_ok": ["000002", "399002", "899050"]}
        self.amount = {"date": D.isoformat(), "amount": 1.2e12, "same_amount": 6.0e11, "prev_same": 5.0e11,
                       "same_as_of": "10:30", "prev_date": "2026-11-20", "source": "测试"}
        self.pools = {"sealed": [], "broken": []}
        self.hm = []
        self.idx_prices = IDX_PX
        self.idx_at = None
        self._seed()
        self._patch(mp)

    # ── 库 ──
    def stock(self, code, name, is_st=False, mk="SH"):
        s = Stock(code=code, name=name, market=mk, is_st=is_st, float_shares=1e8)
        self.db.add(s)
        self.db.flush()
        return s

    def sector(self, code, name, codes, n=None):
        sec = Sector(code=code, name=name, sector_type="concept", stock_count=n or len(codes), is_watched=True)
        self.db.add(sec)
        self.db.flush()
        for c in codes:
            sid = self.db.query(Stock.id).filter(Stock.code == c).scalar()
            self.db.add(StockSectorRelation(stock_id=sid, sector_id=sec.id))
        return sec

    def _seed(self):
        db = self.db
        self.ids = {}
        for c, n in [("600900", "空头股"), ("603976", "封单王"), ("600801", "小跌一"), ("600802", "小跌二")]:
            self.ids[c] = self.stock(c, n).id
        for d in CAL[1:4]:                                          # 近 3 个已结算交易日：空头股每天 -9%
            for c, p in (("600900", -9.0), ("603976", -3.0), ("600801", -1.0), ("600802", -1.0)):
                db.add(StockDailySnapshot(stock_id=self.ids[c], date=d, pct_change=p))
        # 板块 A「机器人」：带队 600100，跟进 600101/600102/600103，另有 4 只涨 ≥3% 的
        a_codes = ["600100", "600101", "600102", "600103", "600104", "600105", "600106", "600107", "600108", "600109"]
        for c in a_codes:
            self.stock(c, f"机{c[-2:]}")
        self.sec_a = self.sector("BK1001", "机器人", a_codes)
        # 高标样本 6 只
        for i in range(6):
            self.stock(f"60050{i}", f"高{i}")
        db.commit()
        self.hm = [{"code": f"60050{i}", "name": f"高{i}", "state": "STREAKING"} for i in range(6)]
        # —— 默认：数据齐全、可以进入的正例 ——
        for c in ("000001", "399001", "399006", "000688"):
            pass
        self.set_pool([("600801", 3e7), ("600802", 2e7)])           # 两只小封单还封着
        self.seed_lock("603976", 1.3e8, "09:31:05")                  # 早盘锁过的封单王，已开板止跌
        self.minutes["603976"] = (10.0, STOPPED_PX)
        self.quotes["603976"] = _q("603976", 9.3)
        self.minutes["600900"] = (10.0, STOPPED_PX)
        self.quotes["600900"] = _q("600900", 9.3)
        for i in range(6):
            self.quotes[f"60050{i}"] = _q(f"60050{i}", 10.3 + 0.05 * i)
        self.seed_hm("10:28:00", {f"60050{i}": 2.5 + 0.5 * i for i in range(6)})
        self.board["BK1001"] = (100.0, SEC_PX)
        self.pools = {"sealed": [self.zt("600100", "09:41:00"), self.zt("600101", "09:52:00"), self.zt("600102", "10:05:00")],
                      "broken": [{"code": "600103", "name": "机03", "first_time": "10:10:00", "broken_times": 1}]}
        for c in ("600100", "600101", "600102"):
            self.quotes[c] = _q(c, 11.0, high=11.0)
        self.quotes["600103"] = _q("600103", 10.7, high=11.0)
        for c, px in zip(a_codes[4:], (10.4, 10.35, 10.5, 10.1, 10.05, 9.95)):
            self.quotes[c] = _q(c, px)

    def zt(self, code, first, broken=0):
        return {"code": code, "name": f"机{code[-2:]}", "first_time": first, "broken_times": broken, "board": 1}

    def set_pool(self, rows, tc="rows"):
        """rows = [(代码, 封单额)]；都按此刻封跌停给行情。"""
        out = []
        for c, amt in rows:
            if c not in self.ids:
                self.ids[c] = self.stock(c, f"跌{c[-3:]}").id
                self.db.commit()
            out.append(LimitDownDetail(code=c, name=f"跌{c[-3:]}", market=1, price=9.0, pct_change=-10.0,
                                       seal_amount=amt, last_seal_time=t("09:35").time()))
            self.quotes[c] = _q(c, 9.0)
            self.minutes.setdefault(c, (10.0, [9.0] * 60))
        self.pool = (out, len(out) if tc == "rows" else tc)

    def seed_lock(self, code, amt, at):
        self._day(lambda st: st["seal_locks"].append({"code": code, "name": "封单王", "market": 1, "seal_amount": amt,
                                                      "locked_at": at, "basis": f"{at} 首个可信跌停池快照里封单最大"})
                  or st.update(first_trusted_at=at))

    def seed_hm(self, at, pcts):
        import statistics
        self._day(lambda st: st["hm"].append({"ts": f"{D}T{at}", "at": at, "median_pct": statistics.median(pcts.values()),
                                              "pcts": pcts}))

    def _day(self, fn):
        st, _ = S._load_day(self.db, D)
        fn(st)
        row = self.db.query(AppConfig).filter(AppConfig.key == S.DAY_KEY.format(D)).first()
        if row is None:
            self.db.add(AppConfig(key=S.DAY_KEY.format(D), value=json.dumps(st)))
        else:
            row.value = json.dumps(st)
        self.db.commit()

    # ── 外部源 ──
    def _bars(self, prices, d=D):
        return [MinuteBar(dt=x, close=p) for x, p in zip(tmins(len(prices), d=d), prices)]

    def _patch(self, mp):
        w = self
        L.clear_cache()
        mp.setattr("app.services.trading_calendar.get_trading_days", lambda *a, **k: CAL)

        def idx_quotes():
            w.calls["index"] += 1
            return {c: _q(c, 100.04, prev=100.0, at=w.now) for c, _m, _n in L.INDEXES}
        mp.setattr(L, "_index_quotes", idx_quotes)
        mp.setattr(L, "_cores", lambda db, ref, cal: {"items": []})

        def limit_pools(d):
            w.calls["pools"] += 1
            return w.pools
        mp.setattr(L, "_limit_pools", limit_pools)
        mp.setattr(L, "_breadth", lambda: w.calls.update(["breadth"]) or w.breadth)
        mp.setattr(L, "_amount", lambda: w.calls.update(["amount"]) or w.amount)

        def quotes(pairs):
            w.calls["quotes"] += 1
            w.quoted.update(c for c, _m in pairs)
            return {c: w.quotes[c] for c, _m in pairs if c in w.quotes}
        mp.setattr(L, "_quotes", quotes)

        def dt_pool(d, timeout=8):
            w.calls["dtpool"] += 1
            if isinstance(w.pool, Exception):
                raise w.pool
            return w.pool
        mp.setattr(S, "fetch_limit_down_pool", dt_pool)

        def tencent(code, market):
            w.calls["minutes"] += 1
            if code in ("000001", "399001", "399006", "000688"):
                px = w.idx_prices if w.idx_at is None else w.idx_prices[:w.idx_at]
                return {D: (100.0, w._bars(px))}
            prev, px = w.minutes.get(code, (10.0, [10.4] * 60))
            return {D: (prev, w._bars(px))}
        mp.setattr(S, "fetch_minute_days_tencent", tencent)

        def board(code):
            w.calls["board"] += 1
            prev, px = w.board.get(code, (100.0, [100.0] * 60))
            return prev, w._bars(px)
        mp.setattr(S, "fetch_board_minutes", board)
        mp.setattr(S, "highmark_sample", lambda db, ref: w.hm)
        mp.setattr(S, "prev_mainlines", lambda db, ref: {"state_date": "2026-11-20", "sectors": []})

    def run(self, now=None):
        self.now = now or self.now
        h, m, s = (int(x) for x in self.now.split(":"))
        return S.live_recognition(self.db, local_now=datetime(D.year, D.month, D.day, h, m, s))


def items(out):
    return {x["key"]: x for x in out["market"]["items"]}


@pytest.fixture
def world(db, monkeypatch):
    return World(db, monkeypatch)


class TestEndToEnd:
    def test_正例_数据齐全_进入买入检查(self, world):
        out = world.run()
        it = items(out)
        assert {k: v["status"] for k, v in it.items()} == {k: R.PASS for k in it}, {k: v["reason"] for k, v in it.items()}
        assert out["market"]["status"] == R.PASS
        top = out["mainlines"][out["focus"]]
        assert top["sector"]["name"] == "机器人" and top["status"] == R.CONFIRMED
        assert top["leader"]["status"] == R.CONFIRMED and top["leader"]["leader"]["code"] == "600100"
        assert out["entry"] == {"code": "600100", "name": top["leader"]["leader"]["name"], "sector_id": world.sec_a.id,
                                "sector_name": "机器人"}, "首屏的板块、带队股和按钮是同一个结果"
        assert it["bear_seal"]["targets"][0]["code"] == "603976", "早盘锁过的封单王，开板离池后照样跟踪"

    @pytest.mark.parametrize("n,label", [(10, "≤10 家"), (11, "不开新仓"), (20, "不开新仓"), (21, "停手 · 空仓")])
    def test_跌停10_11_20_21家(self, world, n, label):
        world.set_pool([(f"6011{i:02d}", 1e7) for i in range(n)])
        out = world.run()
        ld = items(out)["limit_down"]
        assert ld["label"] == label and ld["count"] == n
        assert out["entry"] is None or n <= 10
        if n == 21:
            assert out["mainlines"] == [] and "收起" in out["mainline_note"]
            assert world.calls["board"] == 0 and not (world.quoted & {"600104", "600105", "600106"}), \
                "停手 · 空仓：不取成分股行情和板块分时"
        elif n > 10:
            assert out["mainlines"], "不开新仓照样算主线，盯着用"
            assert out["hard_block"] and "没补分时" in out["mainlines"][0]["leader_note"]

    def test_真0家_与缺总数的空池(self, world, db, monkeypatch):
        world.pool = ([], 0)
        out = world.run()
        assert items(out)["limit_down"]["count"] == 0
        assert items(out)["bear_seal"]["status"] == R.PASS, "锁过的封单王已止跌、此刻可信快照里没有新的"
        db.query(AppConfig).delete()                   # 换成今天没锁过的状态
        db.commit()
        world.pool = ([], None)
        # 沪深两市涨跌分布都返回时它能当上界；这里只返回了沪市，空池又没总数 → 未知
        world.breadth = {**world.breadth, "markets_ok": ["000002"]}
        L.clear_cache()
        out2 = world.run()
        it = items(out2)
        assert it["limit_down"]["status"] == R.INSUFFICIENT and "没给总数" in it["limit_down"]["reason"]
        assert it["bear_seal"]["label"] == "封单锚待核"
        assert out2["market"]["status"] != R.PASS and out2["entry"] is None

    def test_跌停池失败_单市场涨跌分布7家不放行(self, world):
        world.pool = TimeoutError("东财跌停池超时")
        world.breadth = {**world.breadth, "limit_down": 7, "markets_ok": ["000002"]}
        out = world.run()
        ld = items(out)["limit_down"]
        assert ld["status"] == R.INSUFFICIENT and "只作对照" in ld["reason"]
        assert out["entry"] is None

    def test_9只确认加3只旧报价(self, world):
        world.set_pool([(f"6012{i:02d}", 1e7) for i in range(12)])
        for i in range(9, 12):
            world.quotes[f"6012{i:02d}"] = _q(f"6012{i:02d}", 9.0, at="10:20:00")      # 10 分钟没更新
        out = world.run()
        ld = items(out)["limit_down"]
        assert ld["status"] == R.INSUFFICIENT and ld["count_range"] == [9, 12]
        assert sum(1 for n in ld["names"] if n["verified"] == "stale") == 3

    def test_封单锚缺失_不能只凭近3日领跌股放行(self, world, db):
        db.query(AppConfig).delete()
        db.commit()
        world.seed_hm("10:28:00", {f"60050{i}": 2.5 + 0.5 * i for i in range(6)})
        world.pool = (world.pool[0], None)            # 池子不可信（没给总数）：锁不了
        out = world.run()
        it = items(out)
        assert it["bear_3d"]["status"] == R.PASS and it["bear_seal"]["label"] == "封单锚待核"
        assert out["market"]["status"] != R.PASS and out["entry"] is None
        # 存的当日状态读不回来：同样待核
        db.query(AppConfig).delete()
        db.add(AppConfig(key=S.DAY_KEY.format(D), value="{坏了"))
        db.commit()
        L.clear_cache()
        assert items(world.run("10:31:00"))["bear_seal"]["anchor"] == R.SEAL_UNRECOVERABLE

    def test_锁定后开板又回封_拦截(self, world):
        world.minutes["603976"] = (10.0, RESEALED_PX)
        world.quotes["603976"] = _q("603976", 9.0)
        out = world.run()
        bs = items(out)["bear_seal"]
        assert (bs["status"], bs["label"]) == (R.BLOCK, "开板后回封") and out["entry"] is None

    def test_封单锚跨刷新_跨重启是同一份(self, world, db):
        db.query(AppConfig).delete()
        db.commit()
        world.set_pool([("603976", 1.3e8), ("600801", 3e7)])
        world.quotes["603976"] = _q("603976", 9.0)
        world.run("10:00:00")
        L.clear_cache()                                # 相当于服务重启：进程内缓存全没了
        world.pool = ([], 0)                           # 封单王开板离池了
        world.quotes["603976"] = _q("603976", 9.3)
        out = world.run("10:30:00")
        bs = items(out)["bear_seal"]
        assert bs["anchor"] == R.SEAL_LOCKED and bs["targets"][0]["code"] == "603976"

    def test_收盘用上午行情_不放行_收盘不给入口(self, world):
        world.idx_prices = IDX_PX + [100.04] * 60      # 指数分时只到 11:30
        out = world.run("15:00:05")
        idx = items(out)["index"]
        assert idx["status"] == R.INSUFFICIENT and "11:30" in idx["reason"]
        assert out["entry"] is None and out["phase"] == "closed"

    def test_收盘时数据都新鲜_也不给入口(self, world):
        n = len(tmins(240))
        world.idx_prices = [100 - 0.05 * i for i in range(10)] + [99.55 + 0.002 * i for i in range(n - 10)]
        for c in ("603976", "600900"):
            world.minutes[c] = (10.0, STOPPED_PX + [9.3] * (n - len(STOPPED_PX)))
        world.board["BK1001"] = (100.0, SEC_PX + [105.0] * (n - len(SEC_PX)))
        for c, q in list(world.quotes.items()):
            world.quotes[c] = _q(c, q.price, prev=q.prev_close, at="15:00:00", high=q.high)
        world.amount = {**world.amount, "same_as_of": "15:00"}
        out = world.run("15:00:05")
        assert out["entry"] is None and "已收盘" in out["entry_reason"]

    def test_同一行情重复刷新_只算一次也只记一次观测(self, world, db):
        a = world.run()
        quotes_before = world.calls["quotes"]
        b = world.run()
        assert world.calls["quotes"] == quotes_before, "同一行情时间：整份结果复用，不重打外部源"
        assert a["market"] == b["market"]
        st, _ = S._load_day(db, D)
        assert [o["at"] for o in st["hm"]] == ["10:28:00", "10:30:00"]
        L.clear_cache()
        world.run()                                     # 缓存没了、行情时间没变：也不多记一次
        st, _ = S._load_day(db, D)
        assert len(st["hm"]) == 2

    def test_缺深市的同期成交额(self, world):
        world.amount = {**world.amount, "same_amount": None, "prev_same": None, "same_as_of": None,
                        "same_error": "缺深市的分钟成交额，同期比不算"}
        out = world.run()
        idx = items(out)["index"]
        assert idx["status"] == R.INSUFFICIENT and "缺深市" in idx["reason"] and out["entry"] is None

    def test_同期成交额过期(self, world):
        world.amount = {**world.amount, "same_as_of": "10:00"}
        out = world.run()
        assert "过期" in items(out)["index"]["reason"]

    def test_跨板块借跟随_不算(self, world, db):
        # 板块 B「汽车」和机器人活跃成分重合 ≥60% 会被并进来；B 自己多两只跟进 —— 不能借给机器人
        for c in ("600201", "600202", "600299", "600298", "600297"):
            world.stock(c, f"汽{c[-2:]}", is_st=c in ("600299", "600298", "600297"))
        db.commit()
        # 机器人里换成：带队 600100，跟进只有 600101；另三只活跃的是 ST（不算跟进，但算进归并、让机器人当代表）
        for c in ("600299", "600298", "600297"):
            db.add(StockSectorRelation(stock_id=db.query(Stock.id).filter(Stock.code == c).scalar(), sector_id=world.sec_a.id))
        world.sector("BK2002", "汽车", ["600100", "600101", "600299", "600201", "600202"], n=200)
        db.commit()
        world.board["BK2002"] = (100.0, SEC_PX)
        world.pools = {"sealed": [world.zt("600100", "09:41:00"), world.zt("600101", "09:52:00"),
                                  world.zt("600299", "09:58:00"), world.zt("600298", "09:59:00"),
                                  world.zt("600297", "09:59:30"),
                                  world.zt("600201", "10:03:00"), world.zt("600202", "10:06:00")], "broken": []}
        for c in ("600299", "600298", "600297", "600201", "600202"):
            world.quotes[c] = _q(c, 11.0, high=11.0)
        world.quotes["600102"] = _q("600102", 10.5)
        world.quotes["600103"] = _q("600103", 10.5)
        out = world.run()
        robot = next(m for m in out["mainlines"] if m["sector"]["name"] == "机器人")
        assert "汽车" in robot["merged"]
        lj = robot["leader"]
        assert lj["status"] == R.PENDING and lj["followers_total"] == 1, lj["reason"]
        assert all(f["code"] in ("600101",) for f in lj["followers"])
        assert out["entry"] is None

    def test_从卡片到买入检查后市场转差_硬风控拦截(self, world, db):
        card = world.run()
        assert card["entry"] is not None
        world.set_pool([(f"6013{i:02d}", 1e7) for i in range(12)])          # 点进去之后：跌停冒到 12 家
        L.clear_cache()
        hr = S.hard_risk(db, local_now=datetime(2026, 11, 23, 10, 32))
        assert hr["status"] == R.BLOCK
        ctx = {"mode": "LIVE", "stock": {"code": "600100"}, "market": {"hard_risk": hr}}
        mod = PR.check_market(ctx)
        fails = [i for i in mod["items"] if i["level"] == "FAIL"]
        assert fails and fails[0]["key"] == "hard_limit_down" and "不开新仓" in fails[0]["text"]
        # 拿不到 / 过期 → 等，不是放行
        wait = PR.check_market({"mode": "LIVE", "stock": {"code": "600100"},
                                "market": {"hard_risk": {"status": "INSUFFICIENT", "items": [], "reason": "还没开盘"}}})
        assert any(i["key"] == "hard_risk" and i["group"] == "unmet" for i in wait["items"])
        hist = PR.check_market({"mode": "HISTORICAL", "stock": {"code": "600100"}, "market": {}})
        assert any(i["key"] == "hard_risk" and i["level"] == "INFO" for i in hist["items"])

    def test_不在交易时段不给结论(self, world):
        out = world.run("09:10:00")
        assert out["market"]["status"] == R.INSUFFICIENT and out["entry"] is None


# ═══ 切换历史日期：盘后研究不串实时结论 ════════════════════════════════════

class TestCloseMode:
    def test_历史日期只摆收盘事实_不给结论和入口(self, db):
        out = S.build_close(db, date(2026, 11, 20), [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)])
        assert out["mode"] == "close" and out["entry"] is None
        assert out["market"]["status"] != R.PASS
        it = items(out)
        assert it["bear_3d"]["label"] == S.NO_INTRADAY and it["bear_seal"]["label"] == S.NO_INTRADAY
        assert it["index"]["label"] == S.NO_INTRADAY
