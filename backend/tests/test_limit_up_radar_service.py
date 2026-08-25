"""
涨停板块雷达聚合测试（2026-08-25新增）。

最重要的是 Core Recall 的 Golden Cases：这个功能最不能接受的失败模式，是一只
真正的板块核心因为"今天没涨停"从页面上消失，让用户把"老核心负反馈+低位补涨"
误读成"板块正在增强"。
"""
from datetime import date, time, timedelta

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


def _trading_calendar(db, n=60):
    """
    建 n 个交易日的"日历"。涨停次数现在是从快照历史现算的，而交易日历本身也是从
    stock_daily_snapshots 的 distinct 日期反推的，所以测试必须先有日历才能算窗口。
    用一只专门的日历股来铺这些日期，避免污染被测股票。
    返回按时间正序的交易日列表。
    """
    cal = _stock(db, "000000", "日历股")
    days, d = [], TODAY
    while len(days) < n:
        if d.weekday() < 5:          # 简化：跳过周末即可，测试不需要真实节假日
            days.append(d)
        d -= timedelta(days=1)
    days = sorted(days)
    for day in days:
        db.add(StockDailySnapshot(stock_id=cal.id, date=day, close_price=10.0, pct_change=0.0))
    db.flush()
    return days


def _seed_limit_ups(db, stock, days, indices):
    """在 days[i] 这些交易日给 stock 写涨停快照（indices 是相对 days 的下标）。"""
    for i in indices:
        db.add(StockDailySnapshot(stock_id=stock.id, date=days[i], close_price=10.0,
                                  pct_change=10.0, is_limit_up=True))
    db.flush()


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
    days = _trading_calendar(db)
    anchor = _stock(db, "600664", "哈药股份")
    attacker = _stock(db, "002412", "汉森制药")
    _relate(db, anchor, sec); _relate(db, attacker, sec)
    # 6次涨停全部落在 40~55 个交易日之前：近10日和近20日窗口内一次都没有
    _seed_limit_ups(db, anchor, days, [4, 7, 11, 15, 18, 22])
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
    days = _trading_calendar(db)
    st = _stock(db, "600664", "哈药股份")
    _relate(db, st, sec, is_leader=True)
    _seed_limit_ups(db, st, days, [3, 6, 9, 12, 16, 20, 24, 28])   # 近60日8次涨停
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


# ── 冻结字段回归（用户 2026-08-25 在生产上发现）─────────────────────────────

def test_rolling_counts_are_recomputed_not_read_from_frozen_stock_fields(db):
    """
    真实bug回归。生产上 002432 九安医疗在页面显示"近10日涨停2次·近20日涨停5次·
    近60日涨停9次"，实际拉K线数出来是 0/1/7。

    根因：Stock.limit_up_days_* 只在 daily_update 处理**候选池内**股票时才重算。
    九安医疗最后一次涨停是 2026-07-31，之后掉出候选池，这三个数字就冻结在7月31日
    那天算出来的值，一冻17个交易日。而冻结值永远是"股票最热时"算的、必然偏高，
    Core Recall 恰恰是设计来捞已经冷却的老核心——最需要准的那批正好最不准。

    这里把 Stock 上的字段刻意设成那组错误的冻结值(2/5/9)，快照历史里只放真实的
    涨停日，断言页面拿到的是现算的真实值而不是冻结值。
    """
    sec = _sector(db, "医药生物")
    days = _trading_calendar(db)
    stale = _stock(db, "002432", "九安医疗",
                   limit_up_days_10d=2, limit_up_days_20d=5,   # ← 冻结的错误值
                   limit_up_days_60d=9, board_count_60d=2)
    attacker = _stock(db, "002412", "汉森制药")
    _relate(db, stale, sec); _relate(db, attacker, sec)
    _limit_up(db, attacker)
    # 真实涨停历史：近60日7次，最近一次在第38个交易日（近20日窗口内），近10日0次
    _seed_limit_ups(db, stale, days, [5, 9, 24, 25, 27, 31, 41])
    _snap(db, stale, -2.48)

    card = _find(build_radar(db, TODAY), "医药生物")
    core = next(c for c in card["core_stocks"] if c["code"] == "002432")
    assert core["limit_up_days_10d"] == 0, "冻结值是2，真实近10日一次涨停都没有"
    assert core["limit_up_days_20d"] == 1, "冻结值是5，真实近20日只有1次"
    assert core["limit_up_days_60d"] == 7, "冻结值是9，真实近60日7次"
    # 召回理由是给用户看的事实陈述，不能出现那句假话
    assert not any("近10日涨停2次" in r for r in core["core_reasons"])
    assert "近60日涨停7次" in core["core_reasons"]
    # 近10日为0 ⇒ 不该再挂"当前核心"
    assert "CURRENT_CORE" not in core["core_roles"]


def test_recompute_counts_consecutive_boards_across_trading_days(db):
    """最高连板要按交易日连续判断，跨周末不算断板。"""
    from app.services.limit_up_radar_service import compute_limit_up_history
    days = _trading_calendar(db)
    st = _stock(db, "000017", "深中华A")
    _seed_limit_ups(db, st, days, [50, 51, 52, 53, 57])   # 一段4连板 + 一个孤立涨停
    hist = compute_limit_up_history(db, {st.id}, TODAY)[st.id]
    assert hist.max_consecutive_60d == 4
    assert hist.counts[60] == 5


def test_recompute_falls_back_when_no_trading_calendar_exists(db):
    """一条快照都没有（新库/空库）时不能崩，返回全0而不是抛错。"""
    from app.services.limit_up_radar_service import compute_limit_up_history
    st = _stock(db, "000001", "测试")
    hist = compute_limit_up_history(db, {st.id}, TODAY)[st.id]
    assert hist.counts == {} and hist.max_consecutive_60d == 0


# ── 历史窗口新鲜度（用户 2026-08-25 提出）────────────────────────────────────

def test_history_window_excludes_the_in_progress_day(db):
    """
    盘中今天的 daily_update 还没跑 → 今天没有快照 → 交易日历自然落在上一个交易日，
    滚动窗口算的是"截至上一个完整交易日"。今天的涨停在 limit_up_daily_details 里
    单独展示，不能混进历史窗口重复计算。
    """
    sec = _sector(db, "中药")
    days = _trading_calendar(db)[:-1]        # 刻意不给 TODAY 建快照，模拟盘中
    db.query(StockDailySnapshot).filter(StockDailySnapshot.date == TODAY).delete()
    db.flush()
    st = _stock(db, "002412", "汉森制药")
    _relate(db, st, sec)
    _limit_up(db, st)                        # 今天涨停（只在明细表里）

    res = build_radar(db, TODAY)
    assert res["history_as_of"] == days[-1].isoformat()   # 窗口停在上一交易日
    assert res["history_lag_days"] <= 1                   # 落后1天是盘中常态
    assert res["warnings"] == []                          # 不该为此告警
    # 今天的涨停不能被算进"近10日涨停次数"
    card = _find(res, "中药")
    assert card["today_limit_up_stocks"][0]["limit_up_days_10d"] == 0


def test_stale_snapshot_history_is_reported_not_silently_used(db):
    """
    daily_update 好几天没跑时，交易日历本身是旧的，"近10日"实际变成"截至N天前的
    10日"。页面照样能给出精确数字，但那个数字回答的不是用户以为的问题——必须显式
    告知，不能算得出来就当算对了。
    """
    sec = _sector(db, "中药")
    old_day = date(2026, 8, 18)              # 比 TODAY(08-25) 早5个交易日
    cal = _stock(db, "000000", "日历股")
    db.add(StockDailySnapshot(stock_id=cal.id, date=old_day, close_price=10.0))
    st = _stock(db, "002412", "汉森制药")
    _relate(db, st, sec)
    _limit_up(db, st)
    db.flush()

    res = build_radar(db, TODAY)
    assert res["history_as_of"] == "2026-08-18"
    assert res["history_lag_days"] >= 2
    assert any("落后" in w and "每日数据更新" in w for w in res["warnings"])


def test_no_snapshot_history_at_all_warns_loudly(db):
    """
    一条历史快照都没有时，所有滚动窗口都是0，核心锚会大面积漏召回——这是静默的
    整体失效，必须明确告警而不是显示成"这些板块本来就没有核心"。
    """
    sec = _sector(db, "中药")
    st = _stock(db, "002412", "汉森制药")
    _relate(db, st, sec)
    _limit_up(db, st)

    res = build_radar(db, TODAY)
    assert res["history_as_of"] is None
    assert any("没有任何历史快照" in w for w in res["warnings"])


# ── 东财条件选股：核心召回的权威口径（用户 2026-08-25 提出并核实）───────────

def _em_recall(db, payload: dict):
    """写一份东财召回数据。payload: {code: {"lu10","lu20","lu60","mb","chg"}}"""
    import json
    from app.models.app_config import AppConfig
    from app.services.limit_up_detail_service import core_recall_key
    db.add(AppConfig(key=core_recall_key(TODAY), value=json.dumps(payload)))
    db.commit()


def test_eastmoney_counts_win_over_local_snapshot_recompute(db):
    """
    东财的滚动统计是服务端实时算的，跟真实K线逐项核对过全对；本地从快照重算受
    快照缺口影响会**偏低**（实测 600664 哈药股份近60日真实9次、快照里只有6次，
    漏了 07-10/13/14）。偏低就是漏召回，所以东财优先。

    这里本地快照只有6次、东财说9次 → 展示和召回都必须用9。
    """
    sec = _sector(db, "中药")
    days = _trading_calendar(db)
    st = _stock(db, "600664", "哈药股份")
    attacker = _stock(db, "002412", "汉森制药")
    _relate(db, st, sec); _relate(db, attacker, sec)
    _seed_limit_ups(db, st, days, [4, 7, 11, 15, 18, 22])      # 本地只数出6次
    _limit_up(db, attacker)
    _em_recall(db, {"600664": {"n": "哈药股份", "lu10": 0, "lu20": 3,
                               "lu60": 9, "mb": 5, "chg": -0.12}})

    card = _find(build_radar(db, TODAY), "中药")
    core = next(c for c in card["core_stocks"] if c["code"] == "600664")
    assert core["limit_up_days_60d"] == 9, "东财说9次，不能用本地少数的6次"
    assert core["limit_up_days_20d"] == 3
    assert core["board_count_60d"] == 5
    assert "近60日曾涨停9次（含炸板）" in core["core_reasons"]


def test_eastmoney_chg_fills_core_pct_when_daily_snapshot_is_missing(db):
    """
    核心锚大多不在候选池内、盘中没有当日快照，"老核心正/负反馈"这个页面最重要的
    结论此前在盘中完全拿不到。东财选股顺带回传的 CHG 正好补上这个缺口。
    """
    sec = _sector(db, "中药")
    _trading_calendar(db)
    st = _stock(db, "600664", "哈药股份")
    attacker = _stock(db, "002412", "汉森制药")
    _relate(db, st, sec); _relate(db, attacker, sec)
    _limit_up(db, attacker)
    _em_recall(db, {"600664": {"n": "哈药股份", "lu10": 0, "lu20": 3,
                               "lu60": 9, "mb": 5, "chg": -6.2}})

    card = _find(build_radar(db, TODAY), "中药")
    core = next(c for c in card["core_stocks"] if c["code"] == "600664")
    assert core["pct_change"] == -6.2          # 没有当日快照也拿得到
    assert card["core_avg_pct_change"] == -6.2
    assert card["core_pct_known_count"] == 1


def test_local_snapshot_recompute_is_the_fallback_when_eastmoney_unavailable(db):
    """东财拉不到时（接口失败/名单为空）退回本地重算，不能整个失效。"""
    sec = _sector(db, "中药")
    days = _trading_calendar(db)
    st = _stock(db, "600664", "哈药股份")
    attacker = _stock(db, "002412", "汉森制药")
    _relate(db, st, sec); _relate(db, attacker, sec)
    _seed_limit_ups(db, st, days, [4, 7, 11, 15, 18, 22])
    _limit_up(db, attacker)
    # 不写任何 AppConfig → 没有东财数据

    card = _find(build_radar(db, TODAY), "中药")
    core = next(c for c in card["core_stocks"] if c["code"] == "600664")
    assert core["limit_up_days_60d"] == 6      # 本地重算值
    assert "近60日涨停6次" in core["core_reasons"]   # 本地口径=收盘涨停，不带"曾"


def test_strong_pool_members_are_always_recalled(db):
    """
    强势股池本身就是"选龙头"用的，它的 prompt 里有一条"近20个交易日涨幅前10"是纯
    趋势、不含涨停，按涨停次数的那几条召回条件覆盖不到。用本地 in_strong_pool
    保证包含关系，好过在两个自然语言 prompt 之间维护同步。
    """
    sec = _sector(db, "中药")
    _trading_calendar(db)
    trend = _stock(db, "600519", "趋势股", in_strong_pool=True)   # 无任何涨停记录
    attacker = _stock(db, "002412", "汉森制药")
    _relate(db, trend, sec); _relate(db, attacker, sec)
    _limit_up(db, attacker)

    card = _find(build_radar(db, TODAY), "中药")
    core = next(c for c in card["core_stocks"] if c["code"] == "600519")
    assert "在强势股池内" in core["core_reasons"]


def test_eastmoney_counts_are_labelled_as_touched_limit_not_closed_limit(db):
    """
    东财 DURATION_LIMIT_UP 数的是"当日曾触及涨停"，**含炸板**，跟本地/K线口径的
    "收盘涨停"不是同一个指标。2026-08-25 用 603580 艾艾精工核实：近60日收盘涨停
    15天、盘中触板未封4天，合计19，东财正好报19。

    用它做召回没问题（更宽⇒召回更全），但显示成"近60日涨停19次"会被读成19次收盘
    涨停。文案必须跟指标对齐。
    """
    sec = _sector(db, "塑料")
    _trading_calendar(db)
    st = _stock(db, "603580", "艾艾精工")
    attacker = _stock(db, "002412", "汉森制药")
    _relate(db, st, sec); _relate(db, attacker, sec)
    _limit_up(db, attacker)
    _em_recall(db, {"603580": {"n": "艾艾精工", "lu10": 3, "lu20": 6,
                               "lu60": 19, "mb": 4, "chg": 2.5}})

    card = _find(build_radar(db, TODAY), "塑料")
    core = next(c for c in card["core_stocks"] if c["code"] == "603580")
    reasons = core["core_reasons"]
    assert "近60日曾涨停19次（含炸板）" in reasons
    assert not any(r == "近60日涨停19次" for r in reasons), "不能让用户读成19次收盘涨停"
