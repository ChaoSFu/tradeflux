"""
成交量/成交额接入的口径纪律（2026-09-03）。

这两个字段的数据**一直都在**：fuyao dump 的 parquet 本来就有 volume/turnover 两列，
腾讯 K 线每行的 row[5] 也是成交量——只是 load_bars 和解析器没接住。零新增请求。

测试盯三件本仓库真出过事的地方：

1. **单位**。腾讯给"手"、新浪给"股"。此前 StockQuote.volume 混过这两种口径，
   新浪来源的候选 VWAP 因此系统性缩小 100 倍、被合理性校验拦掉、悄悄退化成 MA5
   ——不是随机噪音，是"只要这次刷新恰好分到新浪那一路，VWAP 就必然丢"。
   KLineBar.volume 的规矩是**永远是股**。

2. **来源口径**。dump 未复权、腾讯 qfq，而**复权会同时调整价和量**。不记来源的话，
   一段序列里混了两种口径也看不出来，量比之类的比较会静默出错。

3. **None 不是 0**。turnover_rate 那一版用 0.0 顶替"数据源没给"，结果全市场换手率
   长期恒为 0，情绪分里的 turnover 因子事实上死掉很久却因为"0是合法数字"没人发现。
"""
from datetime import date

from app.services.eastmoney_fetcher import (
    build_kline_bar, _parse_tencent_klines, _parse_sina_klines,
)


class TestVolumeUnit:
    def test_腾讯的手要换算成股(self):
        """腾讯 K 线 row[5] 单位是手，×100 才是股。"""
        bars = _parse_tencent_klines(
            [["2026-09-01", "10.00", "10.00", "10.00", "10.00", "1234"],
             ["2026-09-02", "10.00", "11.00", "11.00", "10.00", "5678"]])
        assert bars[-1].volume == 567800, "腾讯是手，必须×100"
        assert bars[-1].volume_source == "tencent"

    def test_新浪的股不换算(self):
        """新浪本来就给股，再×100 就是那次 VWAP 缩小100倍事故的镜像。"""
        bars = _parse_sina_klines([
            {"day": "2026-09-01", "open": "10.0", "close": "10.0", "high": "10.0",
             "low": "10.0", "volume": "123400"},
            {"day": "2026-09-02", "open": "10.0", "close": "11.0", "high": "11.0",
             "low": "10.0", "volume": "567800"}])
        assert bars[-1].volume == 567800, "新浪已经是股，不能再乘"
        assert bars[-1].volume_source == "sina"

    def test_两个源对同一天同一只股票给出一致的股数(self):
        """这条是上面两条的真正目的：换算对了，两路的量才可比。"""
        t = _parse_tencent_klines(
            [["2026-09-01", "10", "10", "10", "10", "100"],
             ["2026-09-02", "10", "11", "11", "10", "5678"]])
        sn = _parse_sina_klines([
            {"day": "2026-09-01", "open": "10", "close": "10", "high": "10", "low": "10", "volume": "10000"},
            {"day": "2026-09-02", "open": "10", "close": "11", "high": "11", "low": "10", "volume": "567800"}])
        assert t[-1].volume == sn[-1].volume


class TestVolumeMissing:
    def test_源没给成交量时是None不是0(self):
        bars = _parse_tencent_klines(
            [["2026-09-01", "10", "10", "10", "10"],
             ["2026-09-02", "10", "11", "11", "10"]])
        assert bars[-1].volume is None, "缺字段就是不知道，写0会让'零成交'和'没数据'分不开"
        assert bars[-1].volume_source is None, "没有量就不该有来源标记"

    def test_成交量畸形时不崩也不编(self):
        bars = _parse_tencent_klines(
            [["2026-09-01", "10", "10", "10", "10", "100"],
             ["2026-09-02", "10", "11", "11", "10", "n/a"]])
        assert bars[-1].volume is None

    def test_腾讯不给成交额一律None(self):
        """不拿 volume×close 估算成交额——那是编数据。"""
        bars = _parse_tencent_klines(
            [["2026-09-01", "10", "10", "10", "10", "100"],
             ["2026-09-02", "10", "11", "11", "10", "5678"]])
        assert bars[-1].amount is None


class TestVolumeSource:
    def test_来源标记跟着量一起走(self):
        b = build_kline_bar(dt=date(2026, 9, 2), open_p=10, close_p=11, high_p=11,
                            low_p=10, pct=10.0, turnover=None, prev_close=10.0,
                            volume=123.0, amount=456.0, volume_source="dump")
        assert (b.volume, b.amount, b.volume_source) == (123.0, 456.0, "dump")

    def test_不传量时三个字段都是None(self):
        b = build_kline_bar(dt=date(2026, 9, 2), open_p=10, close_p=11, high_p=11,
                            low_p=10, pct=10.0, turnover=None, prev_close=10.0)
        assert b.volume is None and b.amount is None and b.volume_source is None
