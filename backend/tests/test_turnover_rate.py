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

from app.models.limit_up_detail import LimitUpDailyDetail
from app.models.stock import Stock
from app.services.turnover_rate_service import (
    compute_turnover_rate, refresh_float_shares, MAX_FLOAT_SHARES_AGE_DAYS,
)

D = date(2026, 9, 3)


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
        assert refresh_float_shares(db, D) == {"updated": 0, "seen": 0}
