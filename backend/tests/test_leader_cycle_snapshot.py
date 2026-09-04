"""
高标龙头周期的每日事实快照落库。

**只落事实、不落状态判定**：状态机的六条转换全是人定阈值，而阈值会改。把结论冻进
历史，口径一变整段历史就失去意义；只存事实则永远可以按新口径重算。跟
RegulatoryStatusDaily "派生事件不落库"是同一条设计决定。

为什么现在就要落：生命周期的价值全在时间序列上——「断板 D+2 有没有创新低」
「RS 在改善还是恶化」都要靠相邻两天相减。今天不记，一个月后还是只有一个截面，
而且丢掉的那段永远补不回来。
"""
from datetime import date, timedelta

from app.models.leader_cycle import LeaderCycleSnapshot
from app.models.market_index import IndexDailySnapshot
from app.models.stock import Stock
from app.services.eastmoney_fetcher import build_kline_bar
from app.services.leader_cycle_snapshot_service import build_snapshots

D0 = date(2026, 8, 1)


def _bars(closes, start=D0):
    out, prev = [], closes[0]
    for i, c in enumerate(closes[1:], start=1):
        out.append(build_kline_bar(
            dt=start + timedelta(days=i), open_p=c, close_p=c, high_p=c, low_p=c,
            pct=(c / prev - 1) * 100, turnover=None, limit_pct=9.90, prev_close=prev))
        prev = c
    return out


def _seed_stock(db, code="600001", in_pool=True, board60=5):
    st = Stock(code=code, name="测试", market="SH",
               in_strong_pool=in_pool, board_count_60d=board60)
    db.add(st); db.flush()
    return st


class TestBuildSnapshots:
    def test_落一行事实且不含任何状态字段(self, db):
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 14.0])
        r = build_snapshots(db, date(2026, 8, 7), {st.code: bars})
        assert r["written"] == 1
        row = db.query(LeaderCycleSnapshot).one()
        assert row.peak_board_count == 4
        assert row.break_date == bars[-1].date
        assert row.board_count_60d == 5, "历史辨识度另存，不跟当前周期混"
        assert not hasattr(row, "state") and not hasattr(row, "lifecycle_state"), \
            "这一层不许出现状态字段——那是状态机的事，建在这层之上"

    def test_同一天重复跑是覆盖不是追加(self, db):
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 14.0])
        build_snapshots(db, date(2026, 8, 7), {st.code: bars})
        build_snapshots(db, date(2026, 8, 7), {st.code: bars})
        assert db.query(LeaderCycleSnapshot).count() == 1, \
            "daily_update 一天跑 2~3 次，必须幂等"

    def test_不同日期各自成行(self, db):
        """相邻两天相减是这张表存在的全部意义。"""
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 14.0])
        build_snapshots(db, date(2026, 8, 7), {st.code: bars})
        build_snapshots(db, date(2026, 8, 8), {st.code: bars})
        assert db.query(LeaderCycleSnapshot).count() == 2

    def test_识别不出周期时不建空行(self, db):
        """缺行表达'没有周期'，比一行字段全 NULL 清楚。"""
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 11.0])      # 只有 2 连板
        r = build_snapshots(db, date(2026, 8, 5), {st.code: bars})
        assert r["no_cycle"] == 1 and r["written"] == 0
        assert db.query(LeaderCycleSnapshot).count() == 0

    def test_不在强势池的股票不落(self, db):
        st = _seed_stock(db, in_pool=False)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64])
        assert build_snapshots(db, date(2026, 8, 6), {st.code: bars})["written"] == 0

    def test_没有基准指数时RS为None而不是0(self, db):
        """None = 不知道。写 0 会让'跟基准持平'和'算不出来'分不开。"""
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 14.0])
        build_snapshots(db, date(2026, 8, 7), {st.code: bars})
        row = db.query(LeaderCycleSnapshot).one()
        assert row.rs_market_20 is None and row.rs_sector_20 is None

    def test_有基准时算出RS(self, db):
        st = _seed_stock(db)
        # 上证指数 11 根：前 10 根 100，最后一根 110（近10日 +10%）
        for i, c in enumerate([100.0] * 10 + [110.0]):
            db.add(IndexDailySnapshot(index_code="000001",
                                      date=D0 + timedelta(days=i), close=c))
        db.flush()
        closes = [100.0] * 10 + [130.0]      # 个股近10日 +30%
        bars = []
        prev = 100.0
        for i, c in enumerate(closes):
            bars.append(build_kline_bar(
                dt=D0 + timedelta(days=i), open_p=c, close_p=c, high_p=c, low_p=c,
                pct=(c / prev - 1) * 100, turnover=None, limit_pct=9.90, prev_close=prev))
            prev = c
        # 造一段 4 连板让它能识别出周期
        for k in range(4):
            prev = bars[-1].close_price
            c = round(prev * 1.10, 2)
            bars.append(build_kline_bar(
                dt=D0 + timedelta(days=11 + k), open_p=c, close_p=c, high_p=c, low_p=c,
                pct=10.0, turnover=None, limit_pct=9.90, prev_close=prev))
        build_snapshots(db, date(2026, 8, 20), {st.code: bars})
        row = db.query(LeaderCycleSnapshot).one()
        assert row.rs_market_10 is not None, "指数和个股都齐了就该算得出"


class TestConfidence:
    def test_周期区间内的缺口会被如实标注(self, db):
        """
        缺口只算 [周期起点, 最新bar] 区间内的。最新 bar 之后的交易日不算缺口——
        那是"还没到的数据"，不是"该有却没有"。
        """
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 14.0])   # 08-02 ~ 08-06
        gap_day = date(2026, 8, 7)
        later = build_kline_bar(dt=date(2026, 8, 10), open_p=14.0, close_p=14.0,
                                high_p=14.0, low_p=14.0, pct=0.0, turnover=None,
                                limit_pct=9.90, prev_close=14.0)
        days = [b.date for b in bars] + [gap_day, later.date]   # 08-07 应有交易日却无 bar
        build_snapshots(db, date(2026, 8, 10), {st.code: bars + [later]},
                        trading_days=days)
        row = db.query(LeaderCycleSnapshot).one()
        assert row.missing_days == 1 and row.peak_board_confident is False, \
            "周期内缺一天，连板数就可能偏低，必须标出来"

    def test_均线窗口不足时标出来(self, db):
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 14.0])   # 只有 5 根
        build_snapshots(db, date(2026, 8, 7), {st.code: bars})
        row = db.query(LeaderCycleSnapshot).one()
        assert row.ma_window_complete is False, \
            "窗口不满时 ma 是 0.0，必须能区分'均线是0'和'没攒够'"


class TestTurnoverWiring:
    """
    首版把 row.turnover_rate 直接写成 last.turnover_rate，而 KLineBar 那个字段
    永远是 None（腾讯/新浪/dump 都不提供换手率）——结果生产上 60 只全空。
    零件造好了没装上，这条测试盯住它真的被调用。
    """
    def test_有流通股本时算出换手率(self, db):
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 14.0])
        bars[-1].volume = 1.0e7          # 1000万股成交 → 10%
        st.float_shares = 1.0e8          # 1亿流通股
        st.float_shares_date = bars[-1].date   # 观测日不能在 trade_date 之后
        db.flush()
        # trade_date 必须等于最后一根 bar 的日期，否则按数据契约当日字段一律留空
        build_snapshots(db, bars[-1].date, {st.code: bars})
        assert db.query(LeaderCycleSnapshot).one().turnover_rate == 10.0

    def test_没有流通股本时是None不是0(self, db):
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 14.0])
        bars[-1].volume = 1.0e7
        build_snapshots(db, date(2026, 8, 7), {st.code: bars})
        assert db.query(LeaderCycleSnapshot).one().turnover_rate is None

    def test_流通股本观测过旧时不用(self, db):
        """除权、解禁会让流通股本台阶式跳变，旧观测算出的数悄悄错 20% 也不报警。"""
        st = _seed_stock(db)
        st.float_shares = 1.0e8
        st.float_shares_date = date(2026, 1, 1)     # 半年前
        db.flush()
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 14.0])
        bars[-1].volume = 1.0e7
        build_snapshots(db, date(2026, 8, 7), {st.code: bars})
        assert db.query(LeaderCycleSnapshot).one().turnover_rate is None


class TestFreshnessContract:
    """
    2026-09-04 加的数据契约：**今天那根 bar 必须真的是今天的**。

    如果上游最终没补回来，bars[-1] 还是昨天的，直接写下去就成了"昨天的值挂着
    今天的日期"——这轮反复修的正是这类错（盘中价冒充收盘价、停牌日顺延陈旧连板数）。
    这一层要守自己的数据契约，不能完全相信调用方已经修好了当日 bar。
    """

    def test_bar不是今天时当日字段留空(self, db):
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 14.0])
        bars[-1].volume = 1.0e7
        st.float_shares, st.float_shares_date = 1.0e8, bars[-1].date
        db.flush()
        # 最后一根是 bars[-1].date，却按后一天落快照
        r = build_snapshots(db, bars[-1].date + timedelta(days=3), {st.code: bars})
        row = db.query(LeaderCycleSnapshot).one()
        assert row.data_fresh is False and row.latest_bar_date == bars[-1].date
        assert row.latest_close is None and row.volume is None \
            and row.turnover_rate is None, "宁可缺失，也不要让昨天的值挂今天的日期"
        assert r["stale"] == 1, "不新鲜的只数要报出来，不能静默"

    def test_周期事实仍然写入(self, db):
        """周期本身（峰值、断板日、回撤）是历史事实，不随当日新鲜度失效。"""
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 14.0])
        build_snapshots(db, bars[-1].date + timedelta(days=3), {st.code: bars})
        row = db.query(LeaderCycleSnapshot).one()
        assert row.peak_board_count == 4 and row.peak_price == 14.64


class TestSuspensionNotGap:
    """
    停牌 ≠ 数据缺口。爱丽家居 08-03~05 停牌三天，数据其实 100% 完整，
    旧实现却会判成 missing_days=3、peak_board_confident=False——
    **因为股票没交易而怀疑自己的数据**。
    """

    def test_已知停牌不算数据缺口(self, db):
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64])
        susp = date(2026, 8, 10)
        later = build_kline_bar(dt=date(2026, 8, 11), open_p=14.0, close_p=14.0,
                                high_p=14.0, low_p=14.0, pct=0.0, turnover=None,
                                limit_pct=9.90, prev_close=14.64)
        days = [b.date for b in bars] + [susp, later.date]
        build_snapshots(db, later.date, {st.code: bars + [later]},
                        trading_days=days, suspended_map={st.code: [susp]})
        row = db.query(LeaderCycleSnapshot).one()
        assert row.absent_days == 1 and row.suspended_days == 1
        assert row.missing_days == 0 and row.peak_board_confident is True

    def test_不知道是否停牌时保守判为缺口(self, db):
        """没有停牌证据就不能假设它停牌了——宁可标"可能偏低"。"""
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64])
        gap = date(2026, 8, 10)
        later = build_kline_bar(dt=date(2026, 8, 11), open_p=14.0, close_p=14.0,
                                high_p=14.0, low_p=14.0, pct=0.0, turnover=None,
                                limit_pct=9.90, prev_close=14.64)
        days = [b.date for b in bars] + [gap, later.date]
        build_snapshots(db, later.date, {st.code: bars + [later]}, trading_days=days)
        row = db.query(LeaderCycleSnapshot).one()
        assert row.missing_days == 1 and row.peak_board_confident is False


class TestChangeVelocity:
    """
    2026-09-04 加的「变化速度」字段。

    动机：截面数字回答不了"它正在变强还是已经强了很久"。RS20=+12 是从 -5 爬上来
    的，还是从 +30 掉下来的？含义完全相反，而截面看不出来。这些字段全部由**相邻
    快照相减**得到，仍然是事实层，不是判定。
    """

    def _row(self, db, code="600001"):
        return db.query(LeaderCycleSnapshot).filter_by(
            stock_id=db.query(Stock).filter_by(code=code).one().id).order_by(
            LeaderCycleSnapshot.date.desc()).first()

    def test_没有昨天的快照时delta是空值而不是0(self, db):
        """
        0 的意思是"没变化"，None 的意思是"不知道"。第一天就写 0，等于宣称
        这只票 RS 纹丝不动——这正是这轮排查里反复出现的那类错。
        """
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64, 16.1])
        build_snapshots(db, bars[-1].date, {st.code: bars},
                        trading_days=[b.date for b in bars])
        row = self._row(db)
        assert row.rs_market_20_delta_1d is None
        assert row.rs_market_20_delta_3d is None

    def test_量比缺任一根就不算(self, db):
        """
        5 日均量拿 4 根算，跟 ma60 拿 15 根算是同一类错：名字说 5 根，实际不是。
        宁可给 None。
        """
        from app.services.leader_cycle_snapshot_service import _ratio
        assert _ratio(100.0, [10.0, 10.0, 10.0, 10.0, None]) is None
        assert _ratio(100.0, [10.0, 10.0, 10.0, 10.0]) is None, "只有4根不能冒充5根"
        assert _ratio(None, [10.0] * 5) is None
        assert _ratio(100.0, [10.0] * 5) == 10.0

    def test_量比用昨天之前5根不含当日(self, db):
        """含当日就是拿自己跟含自己的均值比，放大越猛比值越被自己拉低。"""
        st = _seed_stock(db)
        # 先 4 连板攒出一个周期（不然识别不出周期，压根不会落快照），再走平
        closes = [10.0, 11.0, 12.1, 13.31, 14.64] + [14.64] * 5
        bars, prev = [], closes[0]
        for i, c in enumerate(closes[1:], start=1):
            bars.append(build_kline_bar(
                dt=D0 + timedelta(days=i), open_p=c, close_p=c, high_p=c, low_p=c,
                pct=(c / prev - 1) * 100, turnover=None, limit_pct=9.90,
                prev_close=prev,
                volume=(5000.0 if i == len(closes) - 1 else 1000.0),
                amount=None, volume_source="tencent"))
            prev = c
        build_snapshots(db, bars[-1].date, {st.code: bars},
                        trading_days=[b.date for b in bars])
        row = self._row(db)
        assert row.volume_ratio_5d == 5.0, "5000 ÷ 前五根均值1000；含当日会变成 3.57"
        assert row.amount_ratio_5d is None, "amount 全是 None，不能补零凑出一个比值"

    def test_行情不新鲜时不产出变化速度(self, db):
        """
        最后一根 bar 不是当日 → latest_close 本身就是隔夜陈值。拿陈值去算
        "今天创没创新高"，会把上周的高点写成今天的突破。
        """
        st = _seed_stock(db)
        bars = _bars([10.0, 11.0, 12.1, 13.31, 14.64])
        later = bars[-1].date + timedelta(days=3)
        build_snapshots(db, later, {st.code: bars},
                        trading_days=[b.date for b in bars] + [later])
        row = self._row(db)
        assert row.data_fresh is False
        assert row.new_post_break_high_today is None
        assert row.volume_ratio_5d is None
        assert row.dist_to_post_break_high is None
