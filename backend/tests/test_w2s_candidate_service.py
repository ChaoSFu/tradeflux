"""
候选池发现：来源池取数 + 两组形态条件。

2026-09-09 换了来源——不再跑两路盘前 Prompt，改读本地三个股池（强势股 / 当日
涨停 / 成交额池）。**形态条件一个字没改**：来源换了不代表"什么叫弱转强的起点"
换了，这里的用例正是把那条边界钉住的。
"""
from app.services.w2s_candidate_service import (
    compute_ma, compute_pct20_percentile, verify_setup_pullback, verify_setup_ma_squeeze,
    detect_recall_anomaly,
)


def test_compute_ma_insufficient_data_returns_none():
    assert compute_ma([1.0, 2.0], 5) is None


def test_compute_ma_exact_window():
    assert compute_ma([1.0, 2.0, 3.0, 4.0, 5.0], 5) == 3.0


def test_compute_ma_uses_most_recent_window():
    assert compute_ma([10.0, 1.0, 2.0, 3.0], 3) == 2.0


def test_compute_pct20_percentile_bounds():
    assert compute_pct20_percentile(5.0, []) == 0.0
    assert compute_pct20_percentile(100.0, [10.0, 20.0, 30.0]) == 1.0
    assert compute_pct20_percentile(0.0, [10.0, 20.0, 30.0]) == 0.0


def test_verify_setup_pullback_passes_with_all_conditions_met():
    assert verify_setup_pullback(limit_up_days_20d=2, pct20_percentile=0.5, yesterday_pct_change=-1.5) is True


def test_verify_setup_pullback_fails_without_yesterday_decline():
    assert verify_setup_pullback(limit_up_days_20d=2, pct20_percentile=0.5, yesterday_pct_change=1.0) is False


def test_verify_setup_pullback_fails_without_limit_up_or_top_percentile():
    assert verify_setup_pullback(limit_up_days_20d=0, pct20_percentile=0.3, yesterday_pct_change=-1.0) is False


def test_verify_setup_pullback_top_percentile_alone_is_sufficient():
    assert verify_setup_pullback(limit_up_days_20d=0, pct20_percentile=0.85, yesterday_pct_change=-1.0) is True


def test_verify_setup_ma_squeeze_passes_with_all_conditions_met():
    assert verify_setup_ma_squeeze(pct20_percentile=0.9, yesterday_close=9.5, ma5=10.0, ma20=8.0) is True


def test_verify_setup_ma_squeeze_fails_without_ma_data():
    assert verify_setup_ma_squeeze(pct20_percentile=0.9, yesterday_close=9.5, ma5=None, ma20=None) is False


def test_verify_setup_ma_squeeze_fails_when_not_between_ma5_and_ma20():
    # 昨收要低于MA5(跌破)但仍高于MA20——价格已经跌破 MA20 时不成立
    assert verify_setup_ma_squeeze(pct20_percentile=0.9, yesterday_close=7.0, ma5=10.0, ma20=8.0) is False


def test_verify_setup_ma_squeeze_fails_below_top_percentile():
    assert verify_setup_ma_squeeze(pct20_percentile=0.5, yesterday_close=9.5, ma5=10.0, ma20=8.0) is False


def test_recall_anomaly_none_with_insufficient_history():
    assert detect_recall_anomaly(5, [30, 32]) is None  # 只有2次历史，不足min_history=3


def test_recall_anomaly_none_when_within_normal_range():
    assert detect_recall_anomaly(28, [30, 32, 29, 31]) is None


def test_recall_anomaly_flags_sharp_drop():
    # 历史均值30，本次只有5，比例远低于low_ratio=0.3
    reason = detect_recall_anomaly(5, [30, 32, 29, 31])
    assert reason is not None and "显著低于" in reason


def test_recall_anomaly_flags_sharp_spike():
    # 历史均值30，本次150，比例远高于high_ratio=3.0
    reason = detect_recall_anomaly(150, [30, 32, 29, 31])
    assert reason is not None and "显著高于" in reason


# ── 来源池：读库，不发外部请求 ───────────────────────────────────────────
from datetime import date as _d, timedelta as _td  # noqa: E402

import pytest  # noqa: E402

from app.models.stock import Stock, StockDailySnapshot  # noqa: E402
from app.models.turnover_pool import TurnoverPoolDaily  # noqa: E402
from app.services.w2s_candidate_service import collect_source_pools  # noqa: E402

_D = [_d(2026, 9, 7) + _td(days=i) for i in range(3)]


def _stk(db, code, **kw):
    st = Stock(code=code, name=f"股{code}", market="SH", **kw)
    db.add(st); db.flush()
    return st


class TestSourcePools:
    """
    候选来源 = 页面上看得见的那三个股池。

    改这个来源的理由是**同一个「哪些票值得看」的问题不能有两套答案**：原来两路
    Prompt 是独立的第四套口径，用户在活跃股池里看到的票不一定在雷达里。
    """

    def test_三个池子各自取到(self, db):
        a = _stk(db, "600001", in_strong_pool=True)
        b = _stk(db, "600002")
        _stk(db, "600003")
        db.add(StockDailySnapshot(stock_id=b.id, date=_D[1], close_price=10.0,
                                  is_limit_up=True, is_settled=True))
        db.add(TurnoverPoolDaily(date=_D[1], stock_code="600003", stock_name="股600003",
                                 rank=1, amount=1e9, pct_change=1.0))
        db.commit()
        pools = collect_source_pools(db, _D[2])
        assert pools["strong"] == {"600001"}
        assert pools["limit_up"] == {"600002"}
        assert pools["turnover"] == {"600003"}
        assert a  # 只是让 a 被用到

    def test_盘前取最近一个有数据的交易日(self, db):
        """
        **不硬钉 as_of。** 盘前跑的时候今天那批还没写出来，硬钉会得到空集，
        而空集跟「今天真没有涨停」看起来一模一样。
        """
        b = _stk(db, "600011")
        db.add(StockDailySnapshot(stock_id=b.id, date=_D[0], close_price=10.0,
                                  is_limit_up=True, is_settled=True))
        db.add(TurnoverPoolDaily(date=_D[0], stock_code="600011", stock_name="x",
                                 rank=1, amount=1e9, pct_change=1.0))
        db.commit()
        pools = collect_source_pools(db, _D[2])
        assert pools["limit_up"] == {"600011"} and pools["turnover"] == {"600011"}

    def test_不看未来(self, db):
        """as_of 之后的行不能进来——那是 look-ahead。"""
        b = _stk(db, "600012")
        db.add(StockDailySnapshot(stock_id=b.id, date=_D[2], close_price=10.0,
                                  is_limit_up=True, is_settled=True))
        db.commit()
        assert collect_source_pools(db, _D[0])["limit_up"] == set()

    def test_池子空了不报错(self, db):
        pools = collect_source_pools(db, _D[0])
        assert pools == {"strong": set(), "limit_up": set(), "turnover": set()}

    def test_不再调用选股接口(self):
        """来源改成读库之后，这一步不该再发任何外部请求。"""
        import pathlib
        from app.services import w2s_candidate_service as m
        src = pathlib.Path(m.__file__).read_text(encoding="utf-8")
        assert "fetch_strong_pool_codes" not in src


class TestMissDaysAndLegacy:
    """
    2026-09-09 用户提问「风语筑不属于任何股池，为什么在列表里」查出来的两件事。

    答案是：它是老来源（prompt1，08-27 收进来的）存量候选，新来源没再命中它。
    但顺着查出两个更值得修的问题——都在这里钉住。
    """

    def _cal(self, db, days):
        from app.services.trading_calendar import _write_cache
        _write_cache(db, days)

    def _cand(self, db, code, *, source, last_seen, miss=0, active=True):
        from app.models.weak_to_strong_radar import WeakToStrongCandidate
        st = _stk(db, code)
        c = WeakToStrongCandidate(
            stock_id=st.id, stock_code=code, stock_name=st.name,
            first_seen_date=last_seen, last_seen_date=last_seen,
            consecutive_miss_days=miss, candidate_source=source, is_active=active)
        db.add(c); db.commit()
        return c

    def test_miss天数按交易日历数不是按运行次数(self, db):
        """
        原来是每跑一次 +=1，于是它数的是"发现跑了几次"而不是"过了几天"——
        手动点几次「更新数据」就涨几。实测 603466：last_seen 09-08、as_of 09-09，
        实际只隔 1 个交易日，miss 却已经是 7。
        """
        from app.services.w2s_candidate_service import discover_candidates
        days = [_d(2026, 9, i) for i in (7, 8, 9)]
        self._cal(db, days)
        c = self._cand(db, "600021", source="strong", last_seen=days[1], miss=6)
        discover_candidates(db, days[2])
        discover_candidates(db, days[2])      # 同一天再跑一次
        db.refresh(c)
        assert c.consecutive_miss_days == 1, "跑两次也只隔了一个交易日"
        assert c.is_active is True

    def test_老来源的存量候选直接失活(self, db):
        """来源都撤了，候选资格自然也就没了——不该再占一个观察窗口。"""
        from app.services.w2s_candidate_service import discover_candidates
        days = [_d(2026, 9, i) for i in (7, 8, 9)]
        self._cal(db, days)
        legacy = self._cand(db, "600022", source="prompt1", last_seen=days[1])
        fresh = self._cand(db, "600023", source="strong", last_seen=days[1])
        stats = discover_candidates(db, days[2])
        db.refresh(legacy); db.refresh(fresh)
        assert legacy.is_active is False and stats.get("legacy_dropped") == 1
        assert fresh.is_active is True, "新来源的候选照常走观察窗口"

    def test_拿不到日历时不失活也不猜(self, db):
        """**不知道就是不知道。** 没日历就别拿一个编出来的天数去踢候选。"""
        from app.services.w2s_candidate_service import discover_candidates
        c = self._cand(db, "600024", source="strong", last_seen=_d(2026, 9, 8), miss=3)
        discover_candidates(db, _d(2026, 9, 9))
        db.refresh(c)
        assert c.consecutive_miss_days == 3 and c.is_active is True

    def test_没有新候选时失活逻辑照常跑(self, db):
        """
        原来 `if not verified_by_code: return` 把失活逻辑整段跳过了——池子空一周，
        候选就一周不会过期。**空集是"今天没有新候选"，不是"今天什么都不用做"。**
        """
        from app.services.w2s_candidate_service import discover_candidates
        days = [_d(2026, 9, i) for i in range(1, 12)]
        self._cal(db, days)
        near = self._cand(db, "600025", source="strong", last_seen=days[7])
        far = self._cand(db, "600026", source="strong", last_seen=days[0])
        stats = discover_candidates(db, days[10])   # 三个池子都空
        db.refresh(near); db.refresh(far)
        assert stats["verified"] == 0
        # 计数被更新了 = 失活逻辑确实跑了（原来这里整段被跳过）
        assert near.consecutive_miss_days == 3 and near.is_active is True
        assert far.consecutive_miss_days == 10 and far.is_active is False, \
            "隔了 10 个交易日、窗口 7 天，该失活"
