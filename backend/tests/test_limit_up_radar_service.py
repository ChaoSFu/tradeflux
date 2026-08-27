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


def _radar(db, trade_date=TODAY, **kw):
    """测试默认关闭板块入选门槛——绝大多数用例只造1只涨停，不关会被门槛整个滤掉，
    而它们测的不是门槛。门槛本身由 test_sector_threshold_* 专门覆盖。"""
    kw.setdefault("min_limit_up", 0)
    kw.setdefault("min_board_height", 0)
    return build_radar(db, trade_date, **kw)


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

    card = _find(_radar(db, TODAY), "中药")
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

    card = _find(_radar(db, TODAY), "创新药")
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

    card = _find(_radar(db, TODAY), "光伏")
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

    res = _radar(db, TODAY, group_mode="all_watched_sectors")
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

    res = _radar(db, TODAY, group_mode="primary")
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

    card = _find(_radar(db, TODAY), "中药")
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


def test_sectors_sorted_by_board_height_first_then_count(db):
    """
    2026-08-25 改为**高度优先**：连板高度是板块情绪级别的直接体现，一个出了5板龙头
    的板块即使只有2只涨停，也比10只清一色首板的板块更值得先看——首板扎堆更可能是
    普涨或题材扩散，高板才代表有资金愿意接力。
    """
    out = sort_sectors([
        {"sector_name": "多但全首板", "today_limit_up_count": 10, "board_height": 1, "continuation_count": 0, "earliest_limit_time": time(9, 30), "total_seal_amount": 1e8},
        {"sector_name": "少但5板", "today_limit_up_count": 2, "board_height": 5, "continuation_count": 1, "earliest_limit_time": time(10, 0), "total_seal_amount": 1e8},
        {"sector_name": "同5板但更多", "today_limit_up_count": 6, "board_height": 5, "continuation_count": 3, "earliest_limit_time": time(11, 0), "total_seal_amount": 1e8},
        {"sector_name": "4板", "today_limit_up_count": 8, "board_height": 4, "continuation_count": 2, "earliest_limit_time": time(9, 25), "total_seal_amount": 1e8},
    ])
    assert [s["sector_name"] for s in out] == ["同5板但更多", "少但5板", "4板", "多但全首板"]


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

    card = _find(_radar(db, TODAY), "中药")
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

    card = _find(_radar(db, TODAY), "中药")
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

    names = [s["sector_name"] for s in _radar(db, TODAY)["sectors"]]
    assert names == ["中药"]


def test_unwatched_sectors_are_never_grouped(db):
    _sector(db, "未关注板块", watched=False)
    unwatched = db.query(Sector).filter(Sector.name == "未关注板块").one()
    st = _stock(db, "002412", "汉森制药")
    _relate(db, st, unwatched)
    _limit_up(db, st)
    assert _radar(db, TODAY)["sectors"] == []


def test_include_core_false_skips_core_recall(db):
    sec = _sector(db, "中药")
    anchor = _stock(db, "600664", "哈药股份", limit_up_days_60d=6)
    attacker = _stock(db, "002412", "汉森制药")
    _relate(db, anchor, sec); _relate(db, attacker, sec)
    _limit_up(db, attacker)

    card = _find(_radar(db, TODAY, include_core=False), "中药")
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

    res = _radar(db, TODAY)
    assert res["summary"]["limit_up_count"] == 2      # 含不属于任何板块的那只
    assert res["summary"]["board_height"] == 3
    assert res["summary"]["broken_count"] == 1
    assert res["summary"]["active_sector_count"] == 1
    assert res["summary"]["seal_rate"] == round(2 / 3 * 100, 1)


def test_empty_day_returns_empty_shape_not_error(db):
    res = _radar(db, TODAY)
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

    card = _find(_radar(db, TODAY), "医药生物")
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

    res = _radar(db, TODAY)
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

    res = _radar(db, TODAY)
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

    res = _radar(db, TODAY)
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

    card = _find(_radar(db, TODAY), "中药")
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

    card = _find(_radar(db, TODAY), "中药")
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

    card = _find(_radar(db, TODAY), "中药")
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

    card = _find(_radar(db, TODAY), "中药")
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

    card = _find(_radar(db, TODAY), "塑料")
    core = next(c for c in card["core_stocks"] if c["code"] == "603580")
    reasons = core["core_reasons"]
    assert "近60日曾涨停19次（含炸板）" in reasons
    assert not any(r == "近60日涨停19次" for r in reasons), "不能让用户读成19次收盘涨停"


# ── 板块入选门槛（用户 2026-08-25 要求）──────────────────────────────────────

def _sector_with(db, name, boards):
    """造一个板块，boards 是每只涨停股的连板数列表。"""
    sec = _sector(db, name)
    for i, b in enumerate(boards):
        st = _stock(db, f"{abs(hash(name)) % 900000 + i:06d}", f"{name}{i}")
        _relate(db, st, sec)
        _limit_up(db, st, board=b)
    return sec


def test_sector_threshold_and_branch(db):
    """
    第一条（AND）：涨停够多**且**连板够高 —— 已经走出高度的主线。
    用户原话："涨停个股数<3或者最高连板<3的板块不展示……我要看的是当前最强的板块
    和可能成为最强的板块。"

    2026-08-26 改动：这一条仍在，但不再是唯一的入选路径，见下面 alone 分支。
    """
    _sector_with(db, "达标", [4, 2, 1])        # 涨停3 最高4板 → 留（AND）
    _sector_with(db, "只有高度", [5, 1])       # 涨停2 最高5板 → 滤掉（两条都不满足）
    _sector_with(db, "互联网金融", [1, 1])     # 涨停2 最高1板 → 滤掉（用户举的例子）

    res = build_radar(db, TODAY)
    assert [s["sector_name"] for s in res["sectors"]] == ["达标"]
    assert res["hidden_sector_count"] == 2
    assert res["filter_min_limit_up"] == 3 and res["filter_min_board_height"] == 3
    assert res["filter_min_limit_up_alone"] == 4


def test_sector_threshold_alone_branch(db):
    """
    第二条（OR）：涨停只数单独达到 4 就入选，不看高度。

    起因是用户 2026-08-26 发现「病原体防治」4 只涨停却不展示——它最高只有 2 板，
    被纯 AND 挡掉，而那 4 只涨停比当时展示的 9 个板块里 6 个都多。捕捉的是
    **横向一致性**（今天同时开火的票够多），跟第一条的纵向高度是两种不同的强。

    **已知取舍**：这一条不看高度，所以"涨停7全是首板"也会进来——而用户此前明确
    说过首板扎堆更可能是普涨/题材扩散、想滤掉。当天实测这条只多放进 3 个板块
    （全是 4涨停2板 的同一形态），没出现全首板的情况；真要卡住那种，改成
    "涨停>=4 且 最高>=2" 即可，那样今天的结果一模一样。
    """
    _sector_with(db, "病原体防治", [2, 1, 1, 1])          # 涨停4 最高2板 → 靠 alone 入选
    _sector_with(db, "全首板", [1, 1, 1, 1, 1, 1, 1])     # 涨停7 最高1板 → 也会入选（取舍）
    _sector_with(db, "不够宽", [1, 1, 1])                 # 涨停3 最高1板 → 两条都不满足
    res = build_radar(db, TODAY)
    assert set(s["sector_name"] for s in res["sectors"]) == {"病原体防治", "全首板"}
    assert res["hidden_sector_count"] == 1


def test_sector_threshold_alone_can_be_disabled(db):
    """min_limit_up_alone=0 关掉第二条，退回改动前的纯 AND。"""
    _sector_with(db, "病原体防治", [2, 1, 1, 1])
    res = build_radar(db, TODAY, min_limit_up_alone=0)
    assert res["sectors"] == []
    assert res["hidden_sector_count"] == 1


def test_sector_threshold_is_adjustable(db):
    _sector_with(db, "只有个数", [1, 1, 1, 1, 1, 1, 1])
    _sector_with(db, "只有高度", [5, 1])

    # 单独验 AND 那一支时要先关掉 alone，否则"只有个数"永远靠 7>=4 入选
    res = build_radar(db, TODAY, min_limit_up=3, min_board_height=1, min_limit_up_alone=0)
    assert [s["sector_name"] for s in res["sectors"]] == ["只有个数"]
    res = build_radar(db, TODAY, min_limit_up=1, min_board_height=3, min_limit_up_alone=0)
    assert [s["sector_name"] for s in res["sectors"]] == ["只有高度"]


def test_hidden_sector_count_is_reported_so_nothing_is_silently_dropped(db):
    """被滤掉多少个必须返回给页面——用户要能看出是不是把想看的板块也滤掉了。"""
    _sector_with(db, "达标", [4, 2, 1])
    for i in range(5):
        _sector_with(db, f"噪音{i}", [1, 1])

    res = build_radar(db, TODAY)
    assert len(res["sectors"]) == 1
    assert res["hidden_sector_count"] == 5


# ── 板块门槛：AND 之外加一条"横向一致性"逃生口（2026-08-26）──────────────────
#
# 起因：用户发现「病原体防治」当天 4 只涨停却不展示——它最高只有 2 板，被纯 AND
# 挡掉，而那 4 只涨停比当时展示的 9 个板块里 6 个都多。它的形态是 3 只首板 +
# 1 只一字2板、首封全在 09:25~09:37、封板率 80%，典型的"资金今天在这里形成了
# 集团进攻但还没分出龙头"——正是这个页面立项要抓的东西。

def _sec(name, lu, bh):
    return {"sector_name": name, "today_limit_up_count": lu, "board_height": bh}


def _passes(c, min_lu=3, min_bh=3, min_alone=4):
    return ((c["today_limit_up_count"] >= min_lu and c["board_height"] >= min_bh)
            or (min_alone > 0 and c["today_limit_up_count"] >= min_alone))


def test_有高度的主线仍然入选():
    assert _passes(_sec("黄金概念", 6, 5)) is True
    assert _passes(_sec("智能家居", 3, 3)) is True


def test_横向一致性够强但没高度也能入选():
    """病原体防治：4只涨停、最高2板。纯 AND 会滤掉它。"""
    assert _passes(_sec("病原体防治", 4, 2)) is True
    assert _passes(_sec("病原体防治", 4, 2), min_alone=0) is False, "关掉逃生口应退回纯AND"


def test_噪音板块仍然被滤掉():
    """两条都不满足的才是真噪音——这个页面加门槛的初衷不能被冲掉。"""
    assert _passes(_sec("互联网金融", 2, 1)) is False
    assert _passes(_sec("零售概念", 3, 1)) is False, "3只全首板：横向不够宽、纵向没高度"


def test_阈值取4而不是2或5():
    """
    量出来的不是拍的：当天数据下 alone=4 多出 3 个板块（全是4涨停2板的同一形态），
    alone=5 多出 0 个（等于没改）；而把高度门槛降到 2 会从 9 个涨到 17 个，
    正是用户当初嫌"板块太多"要加门槛的状态。
    """
    s = _sec("病原体防治", 4, 2)
    assert _passes(s, min_alone=4) is True
    assert _passes(s, min_alone=5) is False
    # 降高度门槛那条路会把 3只全首板 的噪音也放进来，所以不走
    assert _passes(_sec("零售概念", 3, 1), min_bh=2) is False
    assert _passes(_sec("零售概念", 3, 2), min_bh=2) is True


# ── 断板最高板（2026-08-27 用户提出）──────────────────────────────────────────
#
# 神奇制药 600613 当前是首板，board_count=1，在"最高3板"的医药生物板块里毫不起眼；
# 但东财 zttj 显示它是 11日7板——历史上打出过 7 个板，只是中间断了。这种票的市场
# 辨识度跟一个真正的首板完全不是一回事，只看最高连板会把它埋在一堆首板里。

def test_断板最高板取N大于M的累计板数():
    from app.services.limit_up_radar_service import broken_streak_height
    # 医药生物实盘：冀衡3日3板 / 千金2日2板 / 神奇11日7板 / 百花首板
    rows = [{"limit_stat_days": 3, "limit_stat_count": 3},
            {"limit_stat_days": 2, "limit_stat_count": 2},
            {"limit_stat_days": 11, "limit_stat_count": 7},
            {"limit_stat_days": 1, "limit_stat_count": 1}]
    assert broken_streak_height(rows) == 7


def test_全是连板时返回None不退化成连板的复制品():
    """3日3板是连着的（M==N），算进来这一列就等于 board_height，没有信息量。"""
    from app.services.limit_up_radar_service import broken_streak_height
    assert broken_streak_height([{"limit_stat_days": 5, "limit_stat_count": 5}]) is None
    assert broken_streak_height([{"limit_stat_days": 1, "limit_stat_count": 1}]) is None


def test_缺字段与空列表都返回None():
    from app.services.limit_up_radar_service import broken_streak_height
    assert broken_streak_height([]) is None
    assert broken_streak_height([{"limit_stat_days": None, "limit_stat_count": 7}]) is None
    assert broken_streak_height([{}]) is None


def test_多只断板股取最大(db):
    from app.services.limit_up_radar_service import broken_streak_height
    rows = [{"limit_stat_days": 11, "limit_stat_count": 7},
            {"limit_stat_days": 20, "limit_stat_count": 9},
            {"limit_stat_days": 4, "limit_stat_count": 2}]
    assert broken_streak_height(rows) == 9


# ── 板块排序主键可切换（2026-08-27）──────────────────────────────────────────

def _S(name, bh, bsh, lu, cont=1):
    return dict(sector_name=name, board_height=bh, broken_streak_height=bsh,
                today_limit_up_count=lu, continuation_count=cont,
                earliest_limit_time=None, total_seal_amount=1.0)


def test_默认按最高连板排序():
    from app.services.limit_up_radar_service import sort_sectors
    got = [s["sector_name"] for s in sort_sectors([
        _S("新零售", 6, 6, 4), _S("农林牧渔", 3, 6, 7),
        _S("黄金概念", 6, None, 3), _S("光通信", 3, None, 6)])]
    assert got == ["新零售", "黄金概念", "农林牧渔", "光通信"], \
        "连板高度优先，同高度再比涨停只数"


def test_切换后按最高断板排序():
    """农林牧渔连板只有3、按连板排在后面，但它有断6板 —— 按断板排该到最前。"""
    from app.services.limit_up_radar_service import sort_sectors
    got = [s["sector_name"] for s in sort_sectors([
        _S("新零售", 6, 6, 4), _S("农林牧渔", 3, 6, 7),
        _S("黄金概念", 6, None, 3), _S("光通信", 3, None, 6)],
        by="broken_streak_height")]
    assert got == ["农林牧渔", "新零售", "光通信", "黄金概念"], \
        "断板同为6时按涨停只数(7>4)；没有断板股的排最后，内部仍按涨停只数"


def test_两种模式的次级键一致():
    """次级键都是涨停数优先——切换的只是主键，不该顺带改变别的规则。"""
    from app.services.limit_up_radar_service import sort_sectors
    rows = [_S("A", 3, 3, 2), _S("B", 3, 3, 9)]
    assert [s["sector_name"] for s in sort_sectors(rows)] == ["B", "A"]
    assert [s["sector_name"] for s in sort_sectors(rows, by="broken_streak_height")] == ["B", "A"]
