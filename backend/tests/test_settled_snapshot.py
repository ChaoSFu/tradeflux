"""
盘中价不得冒充收盘价（2026-08-26 生产事故的回归测试）。

事故复盘：daily_update 可被 UI 随时手动触发，用户当天盘中点了 9 次（09:46~14:00）。
腾讯K线接口盘中就发当日那根未收盘的 bar，`bar.date == today` 成立，于是每次都把
当时的现价写成了"今日收盘价"；收盘后 15:30 那一跑对 19% 的股票K线拉取失败，走
"保留上次可信值"分支，14:00 的盘中价就地转正。600984 因此在库里是
close=5.43/+9.92%（盘中封板那一刻），实际收盘 4.66/-5.67% 是炸板大阴线。
"""
import importlib.util
from datetime import date, datetime
from pathlib import Path

from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import bar_is_settled, SH_TZ
from app.services.screening_service import StockWindowStats

_SPEC = importlib.util.spec_from_file_location(
    "_du_settled", Path(__file__).resolve().parents[1] / "scripts" / "daily_update.py"
)
du = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(du)

TODAY = date(2026, 8, 26)
YESTERDAY = date(2026, 8, 25)


def _stats(close, pct):
    """600984 建设机械：盘中封板 5.43/+9.92%，实际收盘 4.66/-5.67% 炸板。"""
    return StockWindowStats(
        code="600984", name="建设机械", is_st=False, is_new_stock=False, trading_days=65,
        today_bar_date=TODAY,
        today_close_price=close, today_pct_change=pct, today_turnover=None,
        today_is_limit_up=False, today_is_limit_down=False, today_is_broken_board=False,
        today_is_one_word_limit_up=False, today_is_one_word_limit_down=False,
        board_count_current=0, limit_down_count_current=0,
        board_count_60d=0, board_down_count_60d=0,
        limit_up_days_60d=0, limit_up_days_20d=0, limit_up_days_10d=0,
        pct_change_60d=0.0, pct_change_20d=0.0, pct_change_10d=0.0,
        phase="normal", emotion_score=50.0, risk_score=50.0, leader_score=50.0,
        ma30=4.2, ma60=4.0, consecutive_declines=0,
    )


def _stock(db):
    st = Stock(code="600984", name="建设机械", market="SH")
    db.add(st); db.flush()
    return st


class TestBarIsSettled:
    """判定"这个交易日收盘了没有"。光看 bar.date == today 是分不出来的。"""

    def test_盘中当日bar未收盘(self):
        assert bar_is_settled(TODAY, datetime(2026, 8, 26, 14, 0, 55, tzinfo=SH_TZ)) is False

    def test_盘后当日bar已收盘(self):
        assert bar_is_settled(TODAY, datetime(2026, 8, 26, 15, 30, tzinfo=SH_TZ)) is True

    def test_整点15点算已收盘(self):
        assert bar_is_settled(TODAY, datetime(2026, 8, 26, 15, 0, tzinfo=SH_TZ)) is True

    def test_历史交易日必然已收盘(self):
        # 盘前 09:27 那一跑写的是上一交易日，不该被当成"盘中"
        assert bar_is_settled(YESTERDAY, datetime(2026, 8, 26, 9, 27, tzinfo=SH_TZ)) is True

    def test_盘前想写当日则未收盘(self):
        assert bar_is_settled(TODAY, datetime(2026, 8, 26, 9, 27, tzinfo=SH_TZ)) is False

    def test_未来日期防御性判否(self):
        assert bar_is_settled(date(2026, 8, 27), datetime(2026, 8, 26, 16, 0, tzinfo=SH_TZ)) is False


class TestUpsertSnapshotSettled:
    """_upsert_snapshot 对 is_settled 的处理——事故的直接现场。"""

    def test_盘中写入标记为未结算(self, db):
        stock = _stock(db)
        du._upsert_snapshot(db, stock, _stats(5.43, 9.92), TODAY,
                         settled=False)
        db.flush()
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price == 5.43
        assert snap.is_settled is False, "盘中写入必须标记 is_settled=False"

    def test_收盘后拿到真数据则覆盖并标记结算(self, db):
        stock = _stock(db)
        du._upsert_snapshot(db, stock, _stats(5.43, 9.92), TODAY, settled=False)
        db.flush()
        du._upsert_snapshot(db, stock, _stats(4.66, -5.67), TODAY, settled=True)
        db.flush()
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price == 4.66, "收盘价必须覆盖盘中价"
        assert snap.pct_change == -5.67
        assert snap.is_settled is True

    def test_收盘后拉取失败必须清掉盘中价而不是留着(self, db):
        """事故的核心分支。旧代码在这里什么都不做，日志还写着"保留上次可信值"。"""
        stock = _stock(db)
        du._upsert_snapshot(db, stock, _stats(5.43, 9.92), TODAY, settled=False)
        db.flush()
        # 收盘后这一跑：K线拉取失败 → close_pct_fresh=False，但本次是已收盘的跑
        du._upsert_snapshot(db, stock, _stats(None, None), TODAY,
                         close_pct_fresh=False, turnover_fresh=False, derived_fresh=False,
                         settled=True)
        db.flush()
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price is None, "盘中价不能就地转正成收盘价，宁可缺失"
        assert snap.pct_change is None
        assert snap.is_settled is False

    def test_已结算的旧值在拉取失败时才真的保留(self, db):
        """"保留上次可信值"只有在上次确实是收盘后终值时才成立。"""
        stock = _stock(db)
        du._upsert_snapshot(db, stock, _stats(4.66, -5.67), TODAY, settled=True)
        db.flush()
        du._upsert_snapshot(db, stock, _stats(None, None), TODAY,
                         close_pct_fresh=False, turnover_fresh=False, derived_fresh=False,
                         settled=True)
        db.flush()
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price == 4.66, "上次是收盘后终值，这次拉取失败应当保留"
        assert snap.is_settled is True

    def test_盘中跑不会清掉已结算的历史值(self, db):
        """盘中跑上一交易日的行时，绝不能把昨天的终值清掉。"""
        stock = _stock(db)
        du._upsert_snapshot(db, stock, _stats(4.94, 10.02), YESTERDAY, settled=True)
        db.flush()
        du._upsert_snapshot(db, stock, _stats(None, None), YESTERDAY,
                         close_pct_fresh=False, turnover_fresh=False, derived_fresh=False,
                         settled=False)
        db.flush()
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price == 4.94
        assert snap.is_settled is True


import contextlib


@contextlib.contextmanager
def _fake_dump(*a, **k):
    """假的 dump 上下文：只负责给出一个路径，内容由 load_bars 的 monkeypatch 决定。"""
    yield __import__("pathlib").Path("/tmp/_fake_dump.parquet")


class _Log:
    """daily_update 里的 log 是主函数局部变量，测试注入一个哑实现。"""
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass


# ── 第二个洞：盘中进过池子、收盘前掉出去的股票，没人回来修它 ──────────────────
#
# 2026-08-26 18:08 那一跑明确打了"已收盘，本次写入当日终值"，但 600984 等 5 只
# 复查还是盘中价。日志里 `已有 229 条快照` 而 `快照写入 184 只`——差的 45 只
# 就是这类：盘中封涨停时被选股API当"涨停股票"返回、进了候选池写下盘中价，
# 收盘前炸板后不再是涨停股、选股API 不再返回它，收盘那一跑的候选池里根本没有它。

class TestSettleDroppedOut:
    @staticmethod
    def _seed(db, code="600984", close=5.43, pct=9.92, settled=False):
        stock = Stock(code=code, name="建设机械", market="SH")
        db.add(stock); db.flush()
        db.add(StockDailySnapshot(stock_id=stock.id, date=TODAY, close_price=close,
                                  pct_change=pct, is_limit_up=True, is_settled=settled))
        db.flush()
        return stock

    def test_掉出池子的股票被补结算(self, db, monkeypatch):
        from app.services.eastmoney_fetcher import build_kline_bar
        self._seed(db)
        real = build_kline_bar(dt=TODAY, open_p=4.9, close_p=4.66, high_p=5.43, low_p=4.6,
                               pct=-5.67, turnover=None, prev_close=4.94)
        monkeypatch.setattr(du, "fetch_klines_batch", lambda *a, **k: {"600984": [real]})
        n = du._settle_dropped_out_snapshots(db, TODAY, run_settled=True,
                                             handled=set(), fuyao_key=None, log=_Log())
        snap = db.query(StockDailySnapshot).one()
        assert n == 1
        assert snap.close_price == 4.66, "必须用真收盘价覆盖盘中价"
        assert snap.is_limit_up is False, "炸板不是涨停"
        assert snap.is_broken_board is True
        assert snap.is_settled is True

    def test_拿不到数据则清空不保留盘中价(self, db, monkeypatch):
        self._seed(db)
        monkeypatch.setattr(du, "fetch_klines_batch", lambda *a, **k: {})
        # 行情兜底也要断掉：加了那层之后不 mock 就会真的打网络，测试不再自洽
        monkeypatch.setattr(du, "fetch_stock_quotes_batch", lambda *a, **k: {})
        du._settle_dropped_out_snapshots(db, TODAY, run_settled=True,
                                         handled=set(), fuyao_key=None, log=_Log())
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price is None, "宁可缺失，不要让盘中价冒充收盘价"
        assert snap.is_settled is False

    def test_本轮已处理过的股票不重复拉(self, db, monkeypatch):
        self._seed(db)
        called = []
        monkeypatch.setattr(du, "fetch_klines_batch",
                            lambda *a, **k: called.append(1) or {})
        monkeypatch.setattr(du, "fetch_stock_quotes_batch", lambda *a, **k: {})
        n = du._settle_dropped_out_snapshots(db, TODAY, run_settled=True,
                                             handled={"600984"}, fuyao_key=None, log=_Log())
        assert n == 0 and called == [], "已在候选池里跑过的，不该再拉一次"

    def test_已结算的行不动(self, db, monkeypatch):
        self._seed(db, close=4.66, pct=-5.67, settled=True)
        called = []
        monkeypatch.setattr(du, "fetch_klines_batch",
                            lambda *a, **k: called.append(1) or {})
        monkeypatch.setattr(du, "fetch_stock_quotes_batch", lambda *a, **k: {})
        du._settle_dropped_out_snapshots(db, TODAY, run_settled=True,
                                         handled=set(), fuyao_key=None, log=_Log())
        assert called == []
        assert db.query(StockDailySnapshot).one().close_price == 4.66

    def test_盘中跑不做补结算(self, db, monkeypatch):
        """盘中没有"终值"可言，这时候去结算等于把另一个盘中价写成终值。"""
        self._seed(db)
        called = []
        monkeypatch.setattr(du, "fetch_klines_batch",
                            lambda *a, **k: called.append(1) or {})
        monkeypatch.setattr(du, "fetch_stock_quotes_batch", lambda *a, **k: {})
        n = du._settle_dropped_out_snapshots(db, TODAY, run_settled=False,
                                             handled=set(), fuyao_key=None, log=_Log())
        assert n == 0 and called == []
        assert db.query(StockDailySnapshot).one().close_price == 5.43


# ── 全库历史快照补全（2026-08-27）────────────────────────────────────────────
#
# 治的是"掉出候选池放空一段时间 → 快照断档 → 回来时 dump 的10天接不上 → 65天全量拉"
# 这条根因链。2026-08-27 盘中实测：dump 命中 138/142，回落的 4 只**全部**是 26~65 天
# 缺口，正是这一类。

def _mkbar(d, close, o=None, h=None, lo=None):
    from app.services.eastmoney_fetcher import build_kline_bar
    return build_kline_bar(dt=d, open_p=o or close, close_p=close,
                           high_p=h or close, low_p=lo or close,
                           pct=0.0, turnover=None, prev_close=close)


class TestBackfillHistoryFromDump:
    @staticmethod
    def _stocks(db, n=3):
        out = []
        for i in range(n):
            st = Stock(code=f"60000{i}", name=f"票{i}", market="SH")
            db.add(st); out.append(st)
        db.flush()
        return out

    def test_只补历史日绝不碰当日(self, db, monkeypatch):
        """当日那一行永远归主流程写——这样板块统计（只读当日）一个数都不会变。"""
        self._stocks(db, 1)
        bars = [_mkbar(YESTERDAY, 10.0), _mkbar(TODAY, 11.0)]
        monkeypatch.setattr(du, "load_bars", lambda *a, **k: {"600000": bars})
        monkeypatch.setattr(du, "daily_k_dump", _fake_dump)
        n = du._backfill_history_from_dump(db, TODAY, "key", _Log())
        db.flush()
        dates = [r.date for r in db.query(StockDailySnapshot).all()]
        assert n == 1 and dates == [YESTERDAY], "TODAY 那一根不能写"

    def test_已存在的行绝不覆盖(self, db, monkeypatch):
        """主流程写的行带着选股API的权威涨跌停标记，dump 推算的不该盖掉它。"""
        st = self._stocks(db, 1)[0]
        db.add(StockDailySnapshot(stock_id=st.id, date=YESTERDAY, close_price=99.0,
                                  is_limit_up=True, is_settled=True))
        db.flush()
        monkeypatch.setattr(du, "load_bars", lambda *a, **k: {"600000": [_mkbar(YESTERDAY, 10.0)]})
        monkeypatch.setattr(du, "daily_k_dump", _fake_dump)
        n = du._backfill_history_from_dump(db, TODAY, "key", _Log())
        db.flush()
        row = db.query(StockDailySnapshot).one()
        assert n == 0 and row.close_price == 99.0 and row.is_limit_up is True

    def test_补的是全库不只候选池(self, db, monkeypatch):
        sts = self._stocks(db, 3)
        monkeypatch.setattr(du, "load_bars", lambda path, wanted: {
            c: [_mkbar(YESTERDAY, 10.0)] for c in wanted})
        monkeypatch.setattr(du, "daily_k_dump", _fake_dump)
        n = du._backfill_history_from_dump(db, TODAY, "key", _Log())
        db.flush()
        assert n == 3, "库里 3 只股票都该补上，不管在不在候选池"

    def test_写入OHLC且换手率留None(self, db, monkeypatch):
        """dump 有完整 OHLC —— 有了 high 才能重算炸板；换手率 dump 没有，写 None 不写 0。"""
        self._stocks(db, 1)
        monkeypatch.setattr(du, "load_bars", lambda *a, **k: {
            "600000": [_mkbar(YESTERDAY, 10.0, o=9.5, h=10.5, lo=9.4)]})
        monkeypatch.setattr(du, "daily_k_dump", _fake_dump)
        du._backfill_history_from_dump(db, TODAY, "key", _Log())
        db.flush()
        r = db.query(StockDailySnapshot).one()
        assert (r.open_price, r.high_price, r.low_price) == (9.5, 10.5, 9.4)
        assert r.turnover_rate is None, "dump 不提供换手率，0 会被读成真实的0%"
        assert r.is_settled is True, "dump 收盘后生成，历史日必然是终值"

    def test_没配key直接跳过(self, db):
        self._stocks(db, 1)
        assert du._backfill_history_from_dump(db, TODAY, None, _Log()) == 0

    def test_dump不可用不影响主流程(self, db, monkeypatch):
        self._stocks(db, 1)
        import contextlib

        @contextlib.contextmanager
        def _boom(*a, **k):
            raise RuntimeError("下载失败")
            yield
        monkeypatch.setattr(du, "daily_k_dump", _boom)
        assert du._backfill_history_from_dump(db, TODAY, "key", _Log()) == 0


class TestSettleDroppedOutQuoteFallback:
    """
    2026-08-31 收盘后实跑：主链路「行情兜底补回 231/231 只当日bar」，而补结算这条
    「结算 0 只，拿不到数据清空 42 只」——同一个市场事实（当日收盘bar）两套获取
    路径，补结算那条少了行情兜底这一层，于是 42 只股票的收盘价被整片清成 NULL，
    而这些数据其实拿得到。这是本项目第 6 次踩「同一个事实两套函数」。
    """

    def test_K线接口没给当日bar时走行情兜底而不是清空(self, db, monkeypatch):
        from app.services.eastmoney_fetcher import build_kline_bar, StockQuote
        TestSettleDroppedOut._seed(db)
        # dump 和逐股K线都没有当天（盘后 dump 只覆盖到上一交易日，这就是实跑的情形）
        monkeypatch.setattr(du, "fetch_klines_batch", lambda *a, **k: {})

        captured = {}

        def _quotes(pairs):
            captured["pairs"] = pairs
            return {"600984": StockQuote(code="600984", name="建设机械", price=4.66,
                                         pct_change=-5.67, prev_close=4.94, open=4.9,
                                         high=5.43, low=4.6, turnover_rate=None,
                                         trade_date=TODAY)}

        monkeypatch.setattr(du, "fetch_stock_quotes_batch", _quotes)
        n = du._settle_dropped_out_snapshots(db, TODAY, run_settled=True,
                                             handled=set(), fuyao_key=None, log=_Log())
        snap = db.query(StockDailySnapshot).one()
        assert captured.get("pairs"), "K线拿不到就必须问行情，不能直接清空"
        assert n == 1
        assert snap.close_price == 4.66, "行情兜底拿到的收盘价必须写进去"
        assert snap.pct_change == -5.67
        assert snap.is_limit_up is False and snap.is_broken_board is True
        assert snap.is_settled is True

    def test_行情自身是过期的仍然清空(self, db, monkeypatch):
        """兜底不是"有值就用"：行情日期不是当天，说明它给的是别的日子，必须拒绝。"""
        from app.services.eastmoney_fetcher import StockQuote
        TestSettleDroppedOut._seed(db)
        monkeypatch.setattr(du, "fetch_klines_batch", lambda *a, **k: {})
        monkeypatch.setattr(du, "fetch_stock_quotes_batch", lambda pairs: {
            "600984": StockQuote(code="600984", name="建设机械", price=5.43,
                                 pct_change=9.92, prev_close=4.94, open=4.9,
                                 high=5.43, low=4.6, turnover_rate=None,
                                 trade_date=YESTERDAY)})
        du._settle_dropped_out_snapshots(db, TODAY, run_settled=True,
                                         handled=set(), fuyao_key=None, log=_Log())
        snap = db.query(StockDailySnapshot).one()
        assert snap.close_price is None, "过期行情不能冒充当日收盘价，宁可缺失"
        assert snap.is_settled is False
