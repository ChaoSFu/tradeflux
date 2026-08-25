"""
涨停板块雷达聚合测试（2026-08-25新增）。

最重要的是 Core Recall 的 Golden Cases：这个功能最不能接受的失败模式，是一只
真正的板块核心因为"今天没涨停"从页面上消失，让用户把"老核心负反馈+低位补涨"
误读成"板块正在增强"。
"""
from datetime import date, time

from app.models.limit_up_detail import BrokenBoardDailyDetail, LimitUpDailyDetail
from app.models.sector import Sector, StockSectorRelation
from app.models.stock import Stock, StockDailySnapshot
from app.services.limit_up_radar_service import (
    build_radar, recall_core_roles, sort_sectors, sort_today_stocks,
)

TODAY = date(2026, 8, 25)


# ── 建数据的小工具 ───────────────────────────────────────────────────────────

def _sector(db, name, code=None, watched=True, phase=3):
    s = Sector(code=code or name, name=name, is_watched=watched, phase=phase)
    db.add(s); db.flush()
    return s


def _stock(db, code, name, **kw):
    s = Stock(code=code, name=name, market="SZ", **kw)
    db.add(s); db.flush()
    return s


def _relate(db, stock, sector, **kw):
    r = StockSectorRelation(stock_id=stock.id, sector_id=sector.id, **kw)
    db.add(r); db.flush()
    return r


def _limit_up(db, stock, *, board=1, first=time(9, 35), last=None, seal=1.0e8, **kw):
    d = LimitUpDailyDetail(
        stock_id=stock.id, stock_code=stock.code, stock_name=stock.name,
        trade_date=TODAY, board_count=board, first_limit_time=first,
        last_limit_time=last or first, seal_amount=seal, pct_change=10.0, **kw,
    )
    db.add(d); db.flush()
    return d


def _snap(db, stock, pct):
    s = StockDailySnapshot(stock_id=stock.id, date=TODAY, pct_change=pct, close_price=10.0)
    db.add(s); db.flush()
    return s


def _find(result, sector_name):
    return next(s for s in result["sectors"] if s["sector_name"] == sector_name)


# ── Golden Case A：长窗口核心（防"哈药股份"这类漏掉）─────────────────────────

def test_case_a_core_with_no_recent_limit_up_is_still_recalled(db):
    """
    今天不涨停、近10日0次涨停、近60日6次涨停 → 必须被补全。

    这是整个功能最重要的一条。这类股票经历过完整一轮行情后进入震荡，短窗口指标
    全是0，但它仍然可能是板块市场辨识度最高的情绪锚。只看今日涨停它消失，只看
    近10日涨停它还是消失。
    """
    sec = _sector(db, "中药")
    anchor = _stock(db, "600664", "哈药股份",
                    limit_up_days_10d=0, limit_up_days_20d=0,
                    limit_up_days_60d=6, board_count_60d=2)
    attacker = _stock(db, "002412", "汉森制药", limit_up_days_10d=1)
    _relate(db, anchor, sec); _relate(db, attacker, sec)
    _snap(db, anchor, -6.2)          # 老核心今天在跌 —— 负反馈
    _limit_up(db, attacker, board=1)

    card = _find(build_radar(db, TODAY), "中药")
    codes = [c["code"] for c in card["core_stocks"]]
    assert "600664" in codes, "近60日涨停6次的历史核心必须被召回"

    core = next(c for c in card["core_stocks"] if c["code"] == "600664")
    assert core["primary_role"] == "HISTORICAL_CORE"
    assert "近60日涨停6次" in core["core_reasons"]
    # 老核心今天的表现必须跟今日涨停梯队一起呈现，否则用户会把这个板块读成"正在增强"
    assert card["core_avg_pct_change"] == -6.2
    assert card["today_limit_up_count"] == 1


# ── Golden Case B：板块龙头标记 ──────────────────────────────────────────────

def test_case_b_sector_leader_flag_alone_recalls_the_stock(db):
    """今日不涨停、所有滚动指标都是0，但 is_leader=True → 必须被补全。"""
    sec = _sector(db, "创新药")
    leader = _stock(db, "600276", "恒瑞医药")   # 全部滚动指标为默认0
    other = _stock(db, "002821", "凯莱英")
    _relate(db, leader, sec, is_leader=True)
    _relate(db, other, sec)
    _limit_up(db, other)

    card = _find(build_radar(db, TODAY), "创新药")
    core = next(c for c in card["core_stocks"] if c["code"] == "600276")
    assert core["primary_role"] == "SECTOR_LEADER"
    assert "板块龙头" in core["core_reasons"]


# ── Golden Case C：普通首板不能被自动升格成核心 ──────────────────────────────

def test_case_c_plain_first_board_shows_as_attack_not_as_current_core(db):
    """今日首板、无历史强势 → 出现在今日涨停区，但不能被标成 CURRENT_CORE。"""
    sec = _sector(db, "光伏")
    plain = _stock(db, "300118", "东方日升")   # 滚动指标全0
    _relate(db, plain, sec)
    _limit_up(db, plain, board=1)

    card = _find(build_radar(db, TODAY), "光伏")
    assert [s["code"] for s in card["today_limit_up_stocks"]] == ["300118"]
    row = card["today_limit_up_stocks"][0]
    assert row["core_roles"] == []            # 没有任何核心角色
    assert card["core_count"] == 0
    assert card["first_board_count"] == 1 and card["continuation_count"] == 0


# ── Golden Case D：一股多板块 ────────────────────────────────────────────────

def test_case_d_stock_appears_in_every_watched_sector_by_default(db):
    """
    默认 all_watched_sectors：一只股票属于多个关注板块时可以同时出现在多张卡里。
    漏掉真正的核心，比同一只股票出现两次严重得多。
    """
    s1, s2, s3 = _sector(db, "中药"), _sector(db, "流感"), _sector(db, "创新药")
    st = _stock(db, "002412", "汉森制药", limit_up_days_10d=3)
    for sec in (s1, s2, s3):
        _relate(db, st, sec)
    st.primary_sector_id = s1.id
    _limit_up(db, st, board=2)

    res = build_radar(db, TODAY, group_mode="all_watched_sectors")
    assert {s["sector_name"] for s in res["sectors"]} == {"中药", "流感", "创新药"}
    for card in res["sectors"]:
        codes = [x["code"] for x in card["today_limit_up_stocks"]]
        assert codes.count("002412") == 1, "同一板块内部只能出现一次"


def test_case_d_primary_mode_dedupes_to_one_sector(db):
    s1, s2 = _sector(db, "中药"), _sector(db, "流感")
    st = _stock(db, "002412", "汉森制药", limit_up_days_10d=3)
    _relate(db, st, s1); _relate(db, st, s2)
    st.primary_sector_id = s1.id
    _limit_up(db, st)

    res = build_radar(db, TODAY, group_mode="primary")
    assert [s["sector_name"] for s in res["sectors"]] == ["中药"]


# ── 召回是召回，不是分类 ─────────────────────────────────────────────────────

def test_recall_collects_every_matching_reason_not_just_the_first(db):
    """一只股票可能同时满足多条召回条件，理由要全部保留供用户自己判断。"""
    st = Stock(code="000001", name="测试", limit_up_days_10d=4, limit_up_days_20d=6,
               limit_up_days_60d=9, board_count_60d=5)
    rel = StockSectorRelation(stock_id=1, sector_id=1, is_leader=True)
    r = recall_core_roles(st, rel)
    assert set(r.roles) == {"CURRENT_CORE", "RECENT_CORE", "HISTORICAL_CORE", "SECTOR_LEADER"}
    assert r.primary_role == "CURRENT_CORE"      # 近10日还在涨停 → 当前核心优先
    assert len(r.reasons) == 5                   # 10d/20d/连板/60d/龙头
    assert all(isinstance(x, str) and x for x in r.reasons)


def test_recall_thresholds_are_configurable(db):
    st = Stock(code="000001", name="测试", limit_up_days_10d=2)
    assert "CURRENT_CORE" in recall_core_roles(st, None).roles
    assert "CURRENT_CORE" not in recall_core_roles(st, None, core_10d_min=3).roles


def test_stock_with_nothing_is_not_recalled(db):
    st = Stock(code="000001", name="测试")
    r = recall_core_roles(st, None)
    assert r.roles == [] and r.primary_role is None


# ── 今日涨停股同时是核心 → 共振信号必须能被看出来 ───────────────────────────

def test_core_that_also_limits_up_today_keeps_its_role_tags(db):
    """
    最强的共振信号是"历史核心今天也涨停了"。它应该出现在今日涨停区（因为今天确实
    涨停了），但必须带着核心标签，否则这个信号会被拆散到两个区域里看不出来。
    """
    sec = _sector(db, "中药")
    st = _stock(db, "600664", "哈药股份", limit_up_days_60d=8, board_count_60d=4)
    _relate(db, st, sec, is_leader=True)
    _limit_up(db, st, board=3)

    card = _find(build_radar(db, TODAY), "中药")
    row = card["today_limit_up_stocks"][0]
    assert row["code"] == "600664"
    assert "SECTOR_LEADER" in row["core_roles"] and "HISTORICAL_CORE" in row["core_roles"]
    # 已经在今日涨停区了，不重复出现在核心锚区
    assert [c["code"] for c in card["core_stocks"]] == []


# ── 排序 ─────────────────────────────────────────────────────────────────────

def test_today_stocks_sorted_by_board_then_first_seal_then_final_seal(db):
    rows = sort_today_stocks([
        {"code": "D", "board_count": 1, "first_limit_time": time(9, 25), "last_limit_time": time(9, 25), "seal_amount": 3e8},
        {"code": "A", "board_count": 4, "first_limit_time": time(9, 35), "last_limit_time": time(9, 35), "seal_amount": 1.2e8},
        {"code": "C", "board_count": 2, "first_limit_time": time(9, 45), "last_limit_time": time(9, 45), "seal_amount": 2.1e8},
        {"code": "B", "board_count": 3, "first_limit_time": time(9, 32), "last_limit_time": time(10, 18), "seal_amount": 0.8e8},
    ])
    assert [r["code"] for r in rows] == ["A", "B", "C", "D"]   # 连板数优先于封单额


def test_same_board_count_prefers_earlier_first_seal_then_steadier_final_seal(db):
    rows = sort_today_stocks([
        {"code": "晚封", "board_count": 2, "first_limit_time": time(10, 30), "last_limit_time": time(10, 30)},
        {"code": "早封但炸过", "board_count": 2, "first_limit_time": time(9, 30), "last_limit_time": time(14, 55)},
        {"code": "早封且稳", "board_count": 2, "first_limit_time": time(9, 30), "last_limit_time": time(9, 30)},
    ])
    assert [r["code"] for r in rows] == ["早封且稳", "早封但炸过", "晚封"]


def test_missing_time_sorts_last_instead_of_pretending_to_be_earliest(db):
    rows = sort_today_stocks([
        {"code": "无时间", "board_count": 2, "first_limit_time": None, "last_limit_time": None},
        {"code": "有时间", "board_count": 2, "first_limit_time": time(14, 0), "last_limit_time": time(14, 0)},
    ])
    assert [r["code"] for r in rows] == ["有时间", "无时间"]


def test_sectors_sorted_by_count_then_height_then_ladder_then_time(db):
    out = sort_sectors([
        {"sector_name": "少但高", "today_limit_up_count": 2, "board_height": 5, "continuation_count": 1, "earliest_limit_time": time(9, 30), "total_seal_amount": 1e8},
        {"sector_name": "最多", "today_limit_up_count": 6, "board_height": 2, "continuation_count": 2, "earliest_limit_time": time(10, 0), "total_seal_amount": 1e8},
        {"sector_name": "同多但更高", "today_limit_up_count": 6, "board_height": 4, "continuation_count": 3, "earliest_limit_time": time(11, 0), "total_seal_amount": 1e8},
    ])
    assert [s["sector_name"] for s in out] == ["同多但更高", "最多", "少但高"]


# ── 板块卡的事实字段 ─────────────────────────────────────────────────────────

def test_sector_card_reports_ladder_seal_rate_and_earliest_time(db):
    sec = _sector(db, "中药")
    a = _stock(db, "002412", "汉森制药"); b = _stock(db, "600129", "太极集团")
    c = _stock(db, "000538", "云南白药"); d = _stock(db, "600085", "同仁堂")
    for s in (a, b, c, d):
        _relate(db, s, sec)
    _limit_up(db, a, board=4, first=time(9, 45), last=time(9, 45), seal=1.17e8)
    _limit_up(db, b, board=2, first=time(10, 3), last=time(10, 18), seal=0.82e8)
    _limit_up(db, c, board=1, first=time(9, 35), last=time(9, 35), seal=0.5e8)
    # d 炸板
    db.add(BrokenBoardDailyDetail(stock_id=d.id, stock_code=d.code, trade_date=TODAY,
                                  first_limit_time=time(9, 40), broken_times=1, pct_change=3.2))
    db.flush()

    card = _find(build_radar(db, TODAY), "中药")
    assert card["today_limit_up_count"] == 3
    assert card["board_height"] == 4
    assert card["continuation_count"] == 2 and card["first_board_count"] == 1
    assert card["board_ladder"] == [{"board": 4, "count": 1}, {"board": 2, "count": 1}, {"board": 1, "count": 1}]
    assert card["broken_count"] == 1
    assert card["seal_rate"] == 75.0            # 3 /(3+1)
    assert card["earliest_limit_time"] == time(9, 35)
    assert abs(card["total_seal_amount"] - 2.49e8) < 1


def test_total_seal_amount_is_none_when_nothing_is_known(db):
    """一只都没给封单额时返回 None——0 会被读成"没有资金排队"，跟"不知道"不是一回事。"""
    sec = _sector(db, "中药")
    st = _stock(db, "002412", "汉森制药")
    _relate(db, st, sec)
    _limit_up(db, st, seal=None)

    card = _find(build_radar(db, TODAY), "中药")
    assert card["total_seal_amount"] is None
    assert card["seal_amount_known_count"] == 0


def test_sector_with_no_limit_up_today_is_excluded(db):
    """这是涨停板块雷达，不是板块列表——今天一只涨停都没有的板块不进雷达。"""
    quiet = _sector(db, "冷门板块")
    st = _stock(db, "000001", "某股", limit_up_days_60d=9)   # 有历史核心但今天没涨停
    _relate(db, st, quiet)
    active = _sector(db, "中药")
    st2 = _stock(db, "002412", "汉森制药")
    _relate(db, st2, active)
    _limit_up(db, st2)

    names = [s["sector_name"] for s in build_radar(db, TODAY)["sectors"]]
    assert names == ["中药"]


def test_unwatched_sectors_are_never_grouped(db):
    _sector(db, "未关注板块", watched=False)
    unwatched = db.query(Sector).filter(Sector.name == "未关注板块").one()
    st = _stock(db, "002412", "汉森制药")
    _relate(db, st, unwatched)
    _limit_up(db, st)
    assert build_radar(db, TODAY)["sectors"] == []


def test_include_core_false_skips_core_recall(db):
    sec = _sector(db, "中药")
    anchor = _stock(db, "600664", "哈药股份", limit_up_days_60d=6)
    attacker = _stock(db, "002412", "汉森制药")
    _relate(db, anchor, sec); _relate(db, attacker, sec)
    _limit_up(db, attacker)

    card = _find(build_radar(db, TODAY, include_core=False), "中药")
    assert card["core_stocks"] == [] and card["core_count"] == 0
    assert card["today_limit_up_count"] == 1


def test_summary_counts_whole_market_not_just_grouped_sectors(db):
    """
    顶部摘要统计的是全市场涨停，不是"进了雷达的板块里的涨停"——有些涨停股不属于
    任何关注板块，摘要里不能把它们丢掉。
    """
    sec = _sector(db, "中药")
    a = _stock(db, "002412", "汉森制药"); _relate(db, a, sec); _limit_up(db, a, board=3)
    orphan = _stock(db, "000001", "无板块股")       # 不属于任何关注板块
    _limit_up(db, orphan, board=1)
    d = _stock(db, "600085", "同仁堂")
    db.add(BrokenBoardDailyDetail(stock_id=d.id, stock_code=d.code, trade_date=TODAY))
    db.flush()

    res = build_radar(db, TODAY)
    assert res["summary"]["limit_up_count"] == 2      # 含不属于任何板块的那只
    assert res["summary"]["board_height"] == 3
    assert res["summary"]["broken_count"] == 1
    assert res["summary"]["active_sector_count"] == 1
    assert res["summary"]["seal_rate"] == round(2 / 3 * 100, 1)


def test_empty_day_returns_empty_shape_not_error(db):
    res = build_radar(db, TODAY)
    assert res["sectors"] == []
    assert res["summary"]["limit_up_count"] == 0
    assert res["trade_date"] == "2026-08-25"
