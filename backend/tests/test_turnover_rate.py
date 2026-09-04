"""
换手率推算（2026-09-03）。零新增请求：流通股本从涨停池的流通市值反推。

    流通股本 = 流通市值 / 收盘价
    换手率%  = 成交量(股) / 流通股本 × 100

两条纪律：
1. **缺失一律 None，绝不返回 0**。turnover_rate 那一版用 0.0 顶替"数据源没给"，
   结果全市场换手率长期恒为 0，情绪分里的因子事实上死掉很久，却因为"0是个合法
   数字"完全没有报错、没人发现。
2. **流通股本观测过旧就不用**。除权、解禁会让它台阶式跳变，拿三个月前的值算今天
   的换手率可能差 20% 以上，而这种错不会报警。
"""
from datetime import date, timedelta

from app.models.limit_up_detail import BrokenBoardDailyDetail, LimitUpDailyDetail
from app.models.stock import Stock
from app.services.turnover_rate_service import (
    compute_turnover_rate, refresh_float_shares, MAX_FLOAT_SHARES_AGE_DAYS,
)

D = date(2026, 9, 3)


def _seed_limit_up(db, st, float_market_cap, price, d=D):
    db.add(LimitUpDailyDetail(stock_id=st.id, stock_code=st.code, trade_date=d,
                              float_market_cap=float_market_cap, price=price))
    db.flush()


class TestComputeTurnoverRate:
    def test_基本换算(self):
        # 1000万股成交 / 1亿流通股 = 10%
        assert compute_turnover_rate(1e7, 1e8, D, D) == 10.0

    def test_缺成交量返回None不是0(self):
        assert compute_turnover_rate(None, 1e8, D, D) is None

    def test_缺流通股本返回None不是0(self):
        assert compute_turnover_rate(1e7, None, None, D) is None

    def test_观测过旧则不用(self):
        old = D - timedelta(days=MAX_FLOAT_SHARES_AGE_DAYS + 1)
        assert compute_turnover_rate(1e7, 1e8, old, D) is None, \
            "解禁/除权会让流通股本台阶式跳变，旧观测算出的数不可信"
        ok = D - timedelta(days=MAX_FLOAT_SHARES_AGE_DAYS - 1)
        assert compute_turnover_rate(1e7, 1e8, ok, D) == 10.0

    def test_观测日在未来视为不可用(self):
        assert compute_turnover_rate(1e7, 1e8, D + timedelta(days=1), D) is None


class TestRefreshFloatShares:
    @staticmethod
    def _seed(db, code="600001", fmc=1.0e9, price=10.0, d=D):
        st = Stock(code=code, name="测试", market="SH")
        db.add(st); db.flush()
        db.add(LimitUpDailyDetail(stock_id=st.id, stock_code=code, trade_date=d,
                                  float_market_cap=fmc, price=price))
        db.flush()
        return st

    def test_从流通市值反推流通股本(self, db):
        st = self._seed(db, fmc=1.0e9, price=10.0)     # 10亿市值 / 10元 = 1亿股
        r = refresh_float_shares(db, D)
        assert r["updated"] == 1
        assert abs(st.float_shares - 1.0e8) < 1
        assert st.float_shares_date == D

    def test_旧观测不覆盖新观测(self, db):
        st = self._seed(db, fmc=1.0e9, price=10.0, d=D)
        refresh_float_shares(db, D)
        # 补一条更早的记录，再按那天刷新
        db.add(LimitUpDailyDetail(stock_id=st.id, stock_code=st.code,
                                  trade_date=D - timedelta(days=10),
                                  float_market_cap=5.0e8, price=10.0))
        db.flush()
        refresh_float_shares(db, D - timedelta(days=10))
        assert st.float_shares_date == D, "已有更新的观测时，旧的不该覆盖"
        assert abs(st.float_shares - 1.0e8) < 1

    def test_价格为0时跳过不写(self, db):
        st = self._seed(db, fmc=1.0e9, price=0.0)
        assert refresh_float_shares(db, D)["updated"] == 0
        assert st.float_shares is None, "除以0算不出来，就不该有值"

    def test_当日没有明细时不报错(self, db):
        assert refresh_float_shares(db, D)["updated"] == 0


class TestBothPools:
    """
    涨停池和炸板池的 ltsz 是同一个事实的两个来源，只读一张纯属白白少一半覆盖。
    实测强势池 61 只里只有 36 只拿到流通股本，上限卡在明细表的历史深度。
    """
    def test_只在炸板池出现的股票也能拿到流通股本(self, db):
        st = Stock(code="600009", name="只炸板", market="SH")
        db.add(st); db.flush()
        db.add(BrokenBoardDailyDetail(stock_id=st.id, stock_code=st.code, trade_date=D,
                                      float_market_cap=2.0e9, price=20.0))
        db.flush()
        assert refresh_float_shares(db, D)["updated"] == 1
        assert abs(st.float_shares - 1.0e8) < 1


class TestMarketCapSource:
    """
    2026-09-04 加的第二个来源：全市场 clist 的流通市值。

    明细表只含当天涨停/炸板的股票，实测强势池 61 只只覆盖到 38 只——**上限卡在
    "这只票最近进没进过涨停池"，跟它的流通股本毫无关系**。全市场扫一遍才是对的
    覆盖面。

    两源冲突时以明细为准并计数：明细的 ltsz 是收盘后归档的确定值，clist 的 f21
    可能是盘中快照。**分歧率异常高就说明字段口径理解错了**（比如 f21 根本不是
    流通市值），必须能看见而不是静默取其一。
    """

    def _stock(self, db, code="600001"):
        st = Stock(code=code, name="测试", market="SH")
        db.add(st); db.flush()
        return st

    def test_明细里没有的股票靠全市场补上(self, db):
        st = self._stock(db)
        r = refresh_float_shares(db, D, market_caps={st.code: (1.0e9, 10.0)})
        assert r["updated"] == 1 and r["from_clist"] == 1 and r["from_detail"] == 0
        assert st.float_shares == 1.0e8 and st.float_shares_date == D

    def test_两源都有时以明细为准(self, db):
        st = self._stock(db)
        _seed_limit_up(db, st, float_market_cap=1.0e9, price=10.0)   # → 1e8 股
        refresh_float_shares(db, D, market_caps={st.code: (2.0e9, 10.0)})
        assert st.float_shares == 1.0e8, "明细是收盘后归档的确定值，优先"

    def test_两源分歧超过五个点要计数(self, db):
        st = self._stock(db)
        _seed_limit_up(db, st, float_market_cap=1.0e9, price=10.0)
        r = refresh_float_shares(db, D, market_caps={st.code: (2.0e9, 10.0)})
        assert r["disagree"] == 1, "分歧率高说明字段口径理解错了，不能静默取其一"

    def test_细微差异不算分歧(self, db):
        """盘中快照和收盘归档本来就会差一点，别把噪声当信号。"""
        st = self._stock(db)
        _seed_limit_up(db, st, float_market_cap=1.0e9, price=10.0)
        r = refresh_float_shares(db, D, market_caps={st.code: (1.02e9, 10.0)})
        assert r["disagree"] == 0

    def test_流通市值或价格缺失时跳过不写0(self, db):
        st = self._stock(db)
        r = refresh_float_shares(db, D, market_caps={
            st.code: (None, 10.0), "600002": (1.0e9, None)})
        assert r["updated"] == 0 and st.float_shares is None
