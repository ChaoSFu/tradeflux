"""
相对强度 RS 的口径纪律（2026-09-03）。

RS(N) = 个股近N交易日复合收益% − 同制度基准指数同期收益%。

三条盯的都是本仓库真踩过的坑：

1. **不能用 pct_change_60d**。那个字段是 sum(日涨幅) 简单相加，不是复合收益。
   实测 603580 近60日真实 +204.85%、相加只得 123.14%，差 80 个百分点，而且误差
   随涨幅放大——正好专坑大涨股，而高标龙头全是这一类。
2. **基准按板块配对**。创业板 20% 涨跌幅、主板 10%，拿上证给创业板当基准会系统性
   高估 RS。配对复用 deviation_service.board_index_code()，不写第二套映射。
3. **日期对不齐就返回 None**。个股快照有缺口（实测覆盖率 94.3%），"往前数N根bar"
   不等于"往前N个交易日"。拿跨了70自然日的个股收益去减60交易日的指数收益，得到的
   数没有意义、而且不会报错。锚点一律以指数交易日历为准，个股那天没收盘价就是不知道。
"""
from datetime import date, timedelta

from app.models.market_index import IndexDailySnapshot
from app.services.relative_strength_service import MarketBenchmark, compute_rs_market

D0 = date(2026, 6, 1)


def _seed_index(db, code, closes):
    """closes: [c0, c1, ...] 按交易日顺序（这里用连续自然日模拟）。"""
    for i, c in enumerate(closes):
        db.add(IndexDailySnapshot(index_code=code, date=D0 + timedelta(days=i), close=c))
    db.flush()


def _stock_closes(closes):
    return {D0 + timedelta(days=i): c for i, c in enumerate(closes)}


class TestRSMarket:
    def test_跑赢基准为正跑输为负(self, db):
        _seed_index(db, "000001", [100.0] * 10 + [110.0])      # 指数 10 日 +10%
        b = MarketBenchmark(db, windows=(10,))
        win = compute_rs_market(b, "600001", _stock_closes([100.0] * 10 + [130.0]))
        lose = compute_rs_market(b, "600002", _stock_closes([100.0] * 10 + [105.0]))
        assert win[10] == 20.0, "个股+30% 指数+10% → RS +20 个百分点"
        assert lose[10] == -5.0

    def test_基准按板块配对而不是一律上证(self, db):
        _seed_index(db, "000001", [100.0] * 10 + [110.0])   # 上证 +10%
        _seed_index(db, "399006", [100.0] * 10 + [140.0])   # 创业板指 +40%
        b = MarketBenchmark(db, windows=(10,))
        s = _stock_closes([100.0] * 10 + [130.0])           # 个股 +30%
        assert compute_rs_market(b, "600001", s)[10] == 20.0, "沪主板对上证"
        assert compute_rs_market(b, "300001", s)[10] == -10.0, \
            "创业板必须对创业板指——用上证会把 -10 算成 +20，方向都反了"

    def test_用复合收益而不是日涨幅相加(self, db):
        """
        连续三个涨停：复合 +33.1%，日涨幅相加 +30%。差距随涨幅放大，
        高标龙头是误差最大的那一类，所以必须从收盘价序列算。
        """
        _seed_index(db, "000001", [100.0] * 3 + [100.0])
        b = MarketBenchmark(db, windows=(3,))
        rs = compute_rs_market(b, "600001", _stock_closes([100.0, 110.0, 121.0, 133.1]))
        assert rs[3] is not None and abs(rs[3] - 33.1) < 0.01, \
            f"应为复合 +33.1，简单相加会得 30.0，实际 {rs[3]}"

    def test_个股在锚点日停牌则返回None(self, db):
        """不用邻近日期顶替：那会把两段不同区间的收益相减，且不会报错。"""
        _seed_index(db, "000001", [100.0] * 10 + [110.0])
        b = MarketBenchmark(db, windows=(10,))
        closes = _stock_closes([100.0] * 10 + [130.0])
        del closes[D0]                       # 锚点日缺失
        assert compute_rs_market(b, "600001", closes)[10] is None

    def test_指数历史不够长则该窗口为None(self, db):
        _seed_index(db, "000001", [100.0, 101.0, 102.0])
        b = MarketBenchmark(db, windows=(10,))
        assert compute_rs_market(b, "600001", _stock_closes([100.0, 110.0, 121.0]))[10] is None

    def test_北交所没有基准返回None(self, db):
        """
        board_index_code 对北交所返回 None，这里原样承认。不去扩展那个函数——
        它是严重异动预测共用的，改它会波及监管模块；而强势池本来就排除北交所。
        """
        _seed_index(db, "000001", [100.0] * 10 + [110.0])
        b = MarketBenchmark(db, windows=(10,))
        assert compute_rs_market(b, "920510", _stock_closes([100.0] * 10 + [130.0]))[10] is None
