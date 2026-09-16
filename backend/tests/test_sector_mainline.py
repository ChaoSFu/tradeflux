"""
板块趋势 · 主升板块雷达（sector_mainline_v1）。

用 SQLite 内存库真的造板块指数、上证、成分股快照，走完整的「事实 → 闸门 → 状态」。
需求里点名的 10 个场景各有一个用例，另外盯三条口径纪律：
  · 盘中 / 盘前的快照不会让状态基准日前移
  · 那天没有成分股快照是「不知道」，不是 0
  · 列表接口是轻量的：查询条数跟板块数无关，不带历史序列
"""
import json
from datetime import date, datetime, timedelta
from itertools import count

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.database import get_db
from app.models.market_index import IndexDailySnapshot, SectorIndexDaily
from app.models.sector import Sector, StockSectorRelation
from app.models.stock import Stock, StockDailySnapshot
from app.routers import sector_trend
from app.services import sector_mainline_service as svc
from app.services.eastmoney_fetcher import SH_TZ
from app.services.relative_strength_service import rs_vs_benchmark

D0 = date(2026, 9, 11)                       # 周五，状态基准日
NOW = datetime(2026, 9, 11, 16, 0, tzinfo=SH_TZ)
N = 40


def weekdays(end: date, n: int):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


DAYS = weekdays(D0, N)
_codes = count(100001)


def rising(n=N, step=0.005, base=1000.0):
    return [base * (1 + step) ** i for i in range(n)]


def with_tail(closes, steps):
    out = list(closes[:len(closes) - len(steps)])
    for s in steps:
        out.append(out[-1] * (1 + s))
    return out


def add_sector(db, code, closes, members=0, amounts=None, days=None):
    days = (days or DAYS)[-len(closes):]
    s = Sector(code=code, name=f"板块{code}", is_watched=True)
    db.add(s)
    db.flush()
    for i, (d, c) in enumerate(zip(days, closes)):
        db.add(SectorIndexDaily(sector_code=code, date=d, close=c,
                                amount=1e9 if amounts is None else amounts[i]))
    stocks = [Stock(code=f"{next(_codes):06d}", name=f"{code}成分{i}") for i in range(members)]
    db.add_all(stocks)
    db.flush()
    db.add_all([StockSectorRelation(stock_id=st.id, sector_id=s.id) for st in stocks])
    db.flush()
    return s, stocks


def add_bench(db, days=None, close=3000.0, skip=()):
    for d in days or DAYS:
        if d not in skip:
            db.add(IndexDailySnapshot(index_code="000001", date=d, close=close))
    db.flush()


def add_eco(db, stocks, lu, height=None, broken=None, ld=None, days=None, settled=True):
    """按天造成分股快照：前 lu 只涨停（第一只的连板数 = height），接着 broken 只炸板、ld 只跌停。"""
    days = days or DAYS[-len(lu):]
    height = height or [1 if x else 0 for x in lu]
    broken = broken or [0] * len(lu)
    ld = ld or [0] * len(lu)
    for k, d in enumerate(days):
        assert lu[k] or not height[k], "没有涨停就不会有连板高度"
        for i, st in enumerate(stocks):
            is_lu = i < lu[k]
            is_b = lu[k] <= i < lu[k] + broken[k]
            is_ld = lu[k] + broken[k] <= i < lu[k] + broken[k] + ld[k]
            db.add(StockDailySnapshot(
                stock_id=st.id, date=d, is_settled=settled, close_price=10.0,
                is_limit_up=is_lu, is_broken_board=is_b, is_limit_down=is_ld,
                board_count=(height[k] if i == 0 else 1) if is_lu else 0,
                pct_change=10.0 if is_lu else (-10.0 if is_ld else 0.5)))
    db.flush()


def add_fillers(db, n=4):
    """陪跑板块：缓慢走低，跑输上证——让相对强度的名次真的在一群板块里排。"""
    for k in range(n):
        add_sector(db, f"BK9{k:03d}", [1000.0 * 0.999 ** i for i in range(N)])


def state(db, code, as_of=D0, now=NOW):
    r = svc.get_sector_mainline_state(db, as_of=as_of, now=now)
    return r, next(s for s in r["sectors"] if s["code"] == code)


# 标准主升的涨停序列（最近 15 个交易日）：逐日扩散，高度 2→4 板
MAIN_LU = [0, 0, 0, 0, 0, 0, 0, 1, 2, 2, 3, 3, 4, 4, 5]
MAIN_H = [0, 0, 0, 0, 0, 0, 0, 1, 2, 2, 3, 3, 2, 3, 4]


def main_rise(db, code="BK0001", closes=None, lu=None, height=None, amounts=None, **eco):
    s, st = add_sector(db, code, closes or rising(), members=20, amounts=amounts)
    if lu is None:
        lu, height = MAIN_LU, height or MAIN_H
    add_eco(db, st, lu, height, **eco)
    return s, st


# ── 1 ────────────────────────────────────────────────────────────────────────

def test_1_标准主升(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db)
    r, it = state(db, "BK0001")
    assert r["state_date"] == "2026-09-11"
    assert r["version"] == "sector_mainline_v1"
    assert it["state"] == svc.MAIN_RISE, it["state_reason"]
    assert all(it["gates"][g]["status"] == svc.PASS for g in ("trend", "rs", "ecology"))
    f = it["facts"]
    assert f["lu_series"][-3:] == [4, 4, 5] and f["lu_trend"] == svc.EXPANDING
    assert f["height_3d"] == 4 and f["rs10_rank"] == 1
    assert r["counts"][svc.MAIN_RISE] == 1
    assert r["sectors"][0]["code"] == "BK0001", "默认按状态优先级排，主升排最前"


# ── 2 ────────────────────────────────────────────────────────────────────────

def test_2_单日涨停爆发不算主升_也不算点火(db):
    """
    第一版记点火。2026-09-13 生产分布 + 回放：单日爆发 18 次，次日进主升 0 次，
    用户定点火只留「三道闸第一次全过」。
    """
    add_bench(db)
    add_fillers(db)
    main_rise(db, lu=[0] * 14 + [6], height=[0] * 14 + [1])
    _, it = state(db, "BK0001")
    assert it["facts"]["lu_trend"] == svc.SPIKE
    assert it["gates"]["ecology"]["status"] == svc.WARN
    assert it["state"] == svc.NONE, it["state_reason"]
    assert it["state_reason"].startswith("三道闸过了两道") and "单日爆发" in it["state_reason"]


def test_2b_三道闸第一次全过也只是点火(db):
    """滞回：近 3 天里要有 2 天全过。只有今天过 → 点火。"""
    add_bench(db)
    add_fillers(db)
    main_rise(db, lu=[0] * 12 + [1, 2, 3], height=[0] * 12 + [1, 1, 2])
    _, it = state(db, "BK0001")
    assert all(it["gates"][g]["status"] == svc.PASS for g in ("trend", "rs", "ecology"))
    assert it["state"] == svc.IGNITION
    assert "第一次" in it["state_reason"]


# ── 3 ────────────────────────────────────────────────────────────────────────

FADE_LU = [0, 0, 0, 1, 2, 3, 3, 4, 5, 4, 5, 4, 1, 0, 1]
FADE_H = [0, 0, 0, 1, 2, 2, 3, 3, 4, 3, 4, 5, 1, 0, 1]


def test_3_K线还强但涨停和高度在退_主升转分歧(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db, lu=FADE_LU, height=FADE_H)
    _, it = state(db, "BK0001")
    assert it["gates"]["trend"]["status"] == svc.PASS
    assert it["facts"]["lu_trend"] == svc.CONTRACTING
    assert it["state"] == svc.DIVERGENCE, it["state_reason"]
    trail = [x["state"] for x in it["trail"]]
    assert svc.MAIN_RISE in trail, f"分歧之前得先在主升里：{trail}"


def test_3b_没进过主升_K线好但生态在退也记分歧(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db, lu=[0] * 9 + [3, 3, 3, 0, 1, 0], height=[0] * 9 + [1, 1, 1, 0, 1, 0])
    _, it = state(db, "BK0001")
    assert not any(x["state"] in svc.MAINLINE_FAMILY for x in it["trail"][:-1])
    assert it["state"] == svc.DIVERGENCE
    assert it["state_reason"].startswith("K 线还强")


def test_3c_分歧不续命_最后一次主升过去5天就不再挂分歧(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db, lu=[1, 2, 3, 3, 4, 4, 5, 5, 0, 0, 0, 0, 0, 0, 0],
              height=[1, 2, 2, 3, 3, 3, 4, 4, 0, 0, 0, 0, 0, 0, 0])
    _, it = state(db, "BK0001")
    trail = [x["state"] for x in it["trail"]]
    assert trail[:-1] == [svc.DIVERGENCE] * 4, trail
    assert it["gates"]["trend"]["status"] == svc.PASS
    assert it["state"] == svc.NONE, "最后一次主升已经是 6 天前，K 线还在也不再算分歧"


# ── 4 ────────────────────────────────────────────────────────────────────────

def test_4_加速(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db, closes=with_tail(rising(step=0.003), [0.016] * 6))
    _, it = state(db, "BK0001")
    f = it["facts"]
    assert svc.HOT_DEV20 <= f["dev20"] < svc.EXTREME_DEV20 and svc.ACCEL_R5 <= f["r5"] < svc.EXTREME_R5
    assert it["state"] == svc.ACCELERATION, it["state_reason"]
    assert it["gates"]["risk"]["status"] == svc.WARN, "偏热要提示，但不是见顶"


# ── 5 ────────────────────────────────────────────────────────────────────────

def test_5_高潮要极端乖离加见顶迹象_光涨得猛不算(db):
    add_bench(db)
    add_fillers(db)
    steep = with_tail(rising(step=0.003), [0.03] * 6)
    main_rise(db, "BK0001", closes=steep, broken=[0] * 14 + [6])   # 当天涨停 5、炸板 6：封板率 5/11
    main_rise(db, "BK0002", closes=steep)
    _, climax = state(db, "BK0001")
    _, accel = state(db, "BK0002")
    assert climax["facts"]["r5"] >= svc.EXTREME_R5
    assert climax["gates"]["risk"]["status"] == svc.FAIL and "封板率" in climax["gates"]["risk"]["reason"]
    assert climax["state"] == svc.CLIMAX
    assert accel["gates"]["risk"]["status"] == svc.WARN
    assert accel["state"] == svc.ACCELERATION, "没有见顶迹象的极端乖离还是加速"


# ── 6 ────────────────────────────────────────────────────────────────────────

def test_6_主升后跌破MA20_转弱(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db, closes=with_tail(rising(), [-0.04, -0.04]))
    _, it = state(db, "BK0001")
    trail = [x["state"] for x in it["trail"]]
    assert it["gates"]["trend"]["status"] == svc.FAIL
    assert trail[-3:] == [svc.MAIN_RISE, svc.DIVERGENCE, svc.WEAKENING], trail


def test_6b_没在主线里过_跌破MA20就是无(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db, closes=[1000.0 * 0.995 ** i for i in range(N)], lu=[0] * 15)
    _, it = state(db, "BK0001")
    assert it["state"] == svc.NONE


# ── 需求原文里的 Case 2 / 5 / 6（跟上面同名场景的另一种形态）──────────────────

def test_2c_趋势还没确认的单日涨停爆发_也是无(db):
    """需求原文 Case 2 要求记点火；按 09-13 生产分布撤掉了（见 test_2）。不判主升这条仍然成立。"""
    add_bench(db)
    add_fillers(db)
    main_rise(db, closes=with_tail([1000.0 * 0.999 ** i for i in range(N)], [0.04]),
              lu=[0] * 14 + [6], height=[0] * 14 + [1])
    _, it = state(db, "BK0001")
    assert it["gates"]["trend"]["status"] == svc.WARN, "站上 MA20，但均线没排好、MA20 没向上"
    assert it["facts"]["lu_trend"] == svc.SPIKE
    assert it["state"] == svc.NONE, it["state_reason"]


def test_5b_高潮_涨停成交偏离同时极端(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db, closes=with_tail(rising(step=0.003), [0.03] * 6),
              amounts=[1e9] * (N - 5) + [2e9] * 5)
    _, it = state(db, "BK0001")
    f = it["facts"]
    assert f["amount_ratio"] >= svc.AMOUNT_SURGE and f["lu_3d"] >= svc.EUPHORIA_LU_3D
    assert f["seal_rate"] == 1.0, "没有炸板，也没有别的见顶迹象"
    assert it["state"] == svc.CLIMAX
    assert "情绪高潮" in it["gates"]["risk"]["reason"]


def test_6c_跌破MA10_5日跑输_涨停收缩同时出现_转弱不是分歧(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db, closes=with_tail(rising(), [-0.015, -0.015]), lu=FADE_LU, height=FADE_H)
    _, it = state(db, "BK0001")
    f = it["facts"]
    assert it["gates"]["trend"]["status"] == svc.WARN, "还在 MA20 上方，趋势闸没 FAIL"
    assert f["rs5"] < 0 < f["rs10"] and f["lu_trend"] == svc.CONTRACTING
    assert it["state"] == svc.WEAKENING, it["state_reason"]
    assert svc.MAIN_RISE in [x["state"] for x in it["trail"]]


# ── 7 ────────────────────────────────────────────────────────────────────────

def test_7_历史不足是未知(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db, closes=rising(15))
    _, it = state(db, "BK0001")
    assert it["gates"]["trend"]["status"] == svc.UNKNOWN
    assert it["state"] == svc.UNKNOWN
    assert "只有 15 根" in it["state_reason"]


# ── 8 ────────────────────────────────────────────────────────────────────────

def test_8_缺成交额_只跳过放量那条_状态照常(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db, "BK0001", amounts=[1e9] * (N - 3) + [None] * 3)
    main_rise(db, "BK0002")
    _, gap = state(db, "BK0001")
    _, full = state(db, "BK0002")
    assert gap["facts"]["amount_ratio"] is None
    assert any("缺板块成交额" in e for e in gap["evidence"])
    assert gap["state"] == svc.MAIN_RISE
    assert full["facts"]["amount_ratio"] == 1.0


def test_8b_近6日跌停序列_跟涨停同一个口径_没快照的天是None(db):
    add_bench(db)
    add_fillers(db)
    main_rise(db, ld=[0] * 12 + [1, 0, 2])
    db.query(StockDailySnapshot).filter(StockDailySnapshot.date == DAYS[-5]).delete()
    db.flush()
    _, it = state(db, "BK0001")
    f = it["facts"]
    assert f["ld_series"] == [0, None, 0, 1, 0, 2]
    assert f["ld_3d"] == 3 and f["ld"] == 2


# ── 9 ────────────────────────────────────────────────────────────────────────

def test_9_缺基准_相对强度不知道_跌破MA20的照样判(db):
    add_bench(db, skip={D0})
    add_fillers(db)
    main_rise(db, "BK0001")
    main_rise(db, "BK0002", closes=[1000.0 * 0.995 ** i for i in range(N)], lu=[0] * 15)
    r, it = state(db, "BK0001")
    _, down = state(db, "BK0002")
    assert r["state_date"] == "2026-09-11", "基准日由板块指数定，上证缺一天不该让整页往回退"
    assert any("上证 2026-09-11 没有收盘" in n for n in r["notes"])
    assert it["gates"]["rs"]["status"] == svc.UNKNOWN
    assert it["gates"]["trend"]["status"] == svc.PASS
    assert it["state"] == svc.UNKNOWN
    assert down["state"] == svc.NONE, "跌破 MA20 不需要基准也知道"


# ── 10 ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(sector_trend.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _count_queries(db, fn):
    stmts = []

    def listen(conn, cursor, statement, params, context, executemany):
        stmts.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", listen)
    try:
        resp = fn()
    finally:
        event.remove(engine, "before_cursor_execute", listen)
    return resp, len(stmts)


def test_10_列表接口是轻量的_查询条数跟板块数无关(db, client):
    add_bench(db)
    main_rise(db, "BK0001")
    add_fillers(db, 1)
    db.commit()
    resp, few = _count_queries(db, lambda: client.get("/sector-trend", params={"date": "2026-09-11"}))
    assert resp.status_code == 200
    add_fillers_more = [add_sector(db, f"BK8{k:03d}", rising(), members=5) for k in range(6)]
    assert add_fillers_more
    db.commit()
    resp, many = _count_queries(db, lambda: client.get("/sector-trend", params={"date": "2026-09-11"}))
    body = resp.json()
    assert len(body["sectors"]) == 8
    assert many == few, f"不许 N+1：2 个板块 {few} 条查询，8 个板块 {many} 条"
    for it in body["sectors"]:
        assert "bars" not in it and "history" not in it
        assert len(it["trail"]) <= svc.TRAIL_SHOWN
        longest = max(len(v) for v in it["facts"].values() if isinstance(v, list))
        assert longest <= svc.ECO_DAYS, "列表里只带最近 6 天的涨停数，不带历史序列"


def test_10b_详情接口带60根指数和30天生态(db, client):
    add_bench(db)
    main_rise(db, "BK0001")
    add_fillers(db)
    db.commit()
    body = client.get("/sector-trend/BK0001", params={"date": "2026-09-11"}).json()
    assert len(body["bars"]) == N and body["bars"][-1]["date"] == "2026-09-11"
    assert body["bars"][-1]["ma20"] is not None and body["bars"][0]["ma5"] is None
    assert len(body["ecology"]) == svc.ECO_CHART_DAYS and body["ecology"][-1]["lu"] == 5
    assert len(body["history"]) == svc.TRAIL_DAYS
    assert body["sector"]["state"] == svc.MAIN_RISE
    assert client.get("/sector-trend/BK7777", params={"date": "2026-09-11"}).status_code == 404


# ── 口径纪律 ─────────────────────────────────────────────────────────────────

def test_盘中和盘前的快照不会让状态基准日前移(db):
    add_bench(db)
    add_fillers(db)
    _, st = main_rise(db)
    d1 = date(2026, 9, 14)
    for s in db.query(Sector).all():
        db.add(SectorIndexDaily(sector_code=s.code, date=d1, close=2000.0, amount=1e9))
    add_eco(db, st, [9], [1], days=[d1], settled=False)      # 盘前 09:26 那一跑写的
    r = svc.get_sector_mainline_state(db, now=datetime(2026, 9, 14, 16, 0, tzinfo=SH_TZ))
    assert r["state_date"] == "2026-09-11"
    assert any("0/20 行是收盘终值" in n for n in r["notes"])
    r = svc.get_sector_mainline_state(db, now=datetime(2026, 9, 14, 11, 0, tzinfo=SH_TZ))
    assert r["state_date"] == "2026-09-11"
    assert any("还没收盘" in n for n in r["notes"])


def test_少数行没结算_照算_写进证据(db):
    add_bench(db)
    add_fillers(db)
    _, st = main_rise(db)
    snap = db.query(StockDailySnapshot).filter_by(stock_id=st[-1].id, date=D0).one()
    snap.is_settled = False
    db.flush()
    _, it = state(db, "BK0001")
    assert it["state"] == svc.MAIN_RISE
    assert any("1 只成分股当天的快照不是收盘终值" in e for e in it["evidence"])


def test_那天没有成分股快照是不知道_不是0(db):
    add_bench(db)
    add_fillers(db)
    _, st = main_rise(db)
    db.query(StockDailySnapshot).filter(StockDailySnapshot.date == DAYS[-2]).delete()
    db.flush()
    _, it = state(db, "BK0001")
    assert it["facts"]["lu_series"][-2] is None
    assert it["facts"]["lu_3d"] is None
    assert it["gates"]["ecology"]["status"] == svc.UNKNOWN
    assert it["state"] == svc.UNKNOWN


# ── 纯函数 ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lu, expected", [
    ([0, 0, 0, 0, 0, 6], svc.SPIKE),
    ([4, 5, 4, 1, 0, 1], svc.CONTRACTING),
    ([1, 1, 1, 2, 2, 3], svc.EXPANDING),
    ([2, 2, 2, 2, 2, 2], svc.STABLE),
    ([0, 0, None, 1, 1, 1], svc.UNKNOWN),
])
def test_涨停趋势分类(lu, expected):
    assert svc.classify_lu_trend(lu) == expected


def test_板块相对上证_复合收益_缺锚点给None():
    a, b = date(2026, 9, 1), date(2026, 9, 11)
    assert rs_vs_benchmark({a: 100.0, b: 130.0}, {a: 100.0, b: 110.0}, a, b) == 20.0
    assert rs_vs_benchmark({b: 130.0}, {a: 100.0, b: 110.0}, a, b) is None


# ── 数据补充：sync_boards 顺手写开高低量额、导入只补空字段 ────────────────────

def test_sync_boards_收盘后写开高低量额_盘中不写_空值不盖(db):
    from scripts.sync_boards import _upsert_sector_index_bar
    board = {"f2": 4795.46, "f3": 1.72, "f17": 4651.83, "f15": 4815.92, "f16": 4567.19,
             "f5": 11770178, "f6": 52931828514.0}
    assert _upsert_sector_index_bar(db, board, "BK0890", D0, settled=True)
    assert not _upsert_sector_index_bar(db, board, "BK0891", D0, settled=False)
    db.flush()
    row = db.query(SectorIndexDaily).filter_by(sector_code="BK0890").one()
    assert (row.open, row.high, row.low, row.close) == (4651.83, 4815.92, 4567.19, 4795.46)
    assert (row.volume, row.amount) == (11770178, 52931828514.0)
    assert db.query(SectorIndexDaily).filter_by(sector_code="BK0891").count() == 0
    dash = {"f2": 4800.0, "f3": "-", "f17": "-", "f15": "-", "f16": "-", "f5": "-", "f6": "-"}
    assert _upsert_sector_index_bar(db, dash, "BK0890", D0, settled=True)
    db.flush()
    assert row.close == 4800.0 and row.open == 4651.83 and row.pct_change is None


def test_导入只补空字段_收盘对不上一个都不动(db, tmp_path):
    from scripts.import_sector_klines import import_file
    db.add(SectorIndexDaily(sector_code="BK0001", date=D0, close=100.0, pct_change=1.0))
    db.add(SectorIndexDaily(sector_code="BK0002", date=D0, close=100.0))
    db.commit()
    bar = {"date": "2026-09-11", "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0,
           "volume": 5, "amount": 6, "pct_change": 2.0}
    path = tmp_path / "k.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in [
        {"code": "BK0001", "rows": [bar, {**bar, "date": "2026-09-10", "close": 99.0}]},
        {"code": "BK0002", "rows": [{**bar, "close": 90.0}]},
    ]) + "\n", encoding="utf-8")
    r = import_file(db, str(path), dry_run=True)
    assert (r["added"], r["filled"], r["mismatch"]) == (1, 1, 1)
    assert db.query(SectorIndexDaily).filter_by(sector_code="BK0001", date=D0).one().open is None
    r = import_file(db, str(path))
    assert (r["added"], r["filled"], r["mismatch"]) == (1, 1, 1)
    row = db.query(SectorIndexDaily).filter_by(sector_code="BK0001", date=D0).one()
    assert (row.open, row.high, row.amount) == (99.0, 101.0, 6)
    assert row.pct_change == 1.0, "已有的值不覆盖"
    assert db.query(SectorIndexDaily).filter_by(sector_code="BK0002", date=D0).one().open is None
    r = import_file(db, str(path))
    assert (r["added"], r["filled"], r["skipped_exist"]) == (0, 0, 2)
