"""
板块指数回填的可重入性与限流自保（2026-09-03）。

背景：板块指数是 RS_sector 的基准，只能从东财 push2his 拿（腾讯没有 BK 板块码，
fetch_index_kline 那条腾讯优先的路对板块必然失败）。而 **push2his 限流很凶**——
实测约 15 次快速请求就把开发机 IP 打进限流，连既有代码依赖的上证指数也一起拉不到。

所以回填必须满足两条：中途被掐断不能丢已补的；被限流要主动停手而不是硬撑。
"""
from datetime import date, timedelta

from app.models.market_index import SectorIndexDaily
from app.services import sector_index_service as sis


def _fake_bars(n, start=date(2026, 1, 5)):
    return [{"date": str(start + timedelta(days=i)), "open": 100.0, "close": 100.0 + i,
             "high": 101.0 + i, "low": 99.0 + i, "volume": 1e6, "amount": 1e8,
             "pct_change": 1.0} for i in range(n)]


class TestBackfillResumable:
    def test_已补够的板块直接跳过(self, db, monkeypatch):
        for i in range(sis.MIN_BARS_FOR_RS):
            db.add(SectorIndexDaily(sector_code="BK0832",
                                    date=date(2026, 1, 5) + timedelta(days=i), close=100.0 + i))
        db.flush()
        called = []
        monkeypatch.setattr(sis, "fetch_sector_kline",
                            lambda c, days=300: called.append(c) or _fake_bars(80))
        r = sis.backfill_sector_index(db, ["BK0832"], delay=0)
        assert r["skipped"] == 1 and called == [], "补够了就不该再打网络"

    def test_每只板块独立提交中途掐断不丢已补的(self, db, monkeypatch):
        seen = []
        def _f(code, days=300):
            seen.append(code)
            if code == "BK0003":
                raise RuntimeError("模拟限流")
            return _fake_bars(80)
        monkeypatch.setattr(sis, "fetch_sector_kline",
                            lambda c, days=300: [] if c == "BK0003" else _fake_bars(80))
        r = sis.backfill_sector_index(db, ["BK0001", "BK0002", "BK0003"], delay=0)
        assert r["filled"] == 2 and r["failed"] == ["BK0003"]
        got = {c for (c,) in db.query(SectorIndexDaily.sector_code).distinct().all()}
        assert got == {"BK0001", "BK0002"}, "失败那只不写，成功的两只必须已落库"

    def test_重跑只补没补够的(self, db, monkeypatch):
        monkeypatch.setattr(sis, "fetch_sector_kline",
                            lambda c, days=300: [] if c == "BK0003" else _fake_bars(80))
        sis.backfill_sector_index(db, ["BK0001", "BK0002", "BK0003"], delay=0)
        calls = []
        monkeypatch.setattr(sis, "fetch_sector_kline",
                            lambda c, days=300: calls.append(c) or _fake_bars(80))
        r = sis.backfill_sector_index(db, ["BK0001", "BK0002", "BK0003"], delay=0)
        assert calls == ["BK0003"], f"重跑只该补失败那只，实际打了 {calls}"
        assert r["skipped"] == 2


class TestBackfillThrottleGuard:
    def test_连续失败到阈值主动停手(self, db, monkeypatch):
        """继续打只会加深封锁，还会连累依赖同一域名的指数同步。"""
        calls = []
        monkeypatch.setattr(sis, "fetch_sector_kline",
                            lambda c, days=300: calls.append(c) or [])
        codes = [f"BK{i:04d}" for i in range(20)]
        r = sis.backfill_sector_index(db, codes, delay=0, stop_after_failures=3)
        assert r["aborted"] is True
        assert len(calls) == 3, f"到阈值就该停，实际打了 {len(calls)} 次"

    def test_中间成功会重置连续失败计数(self, db, monkeypatch):
        seq = {"BK0000": [], "BK0001": [], "BK0002": _fake_bars(80),
               "BK0003": [], "BK0004": [], "BK0005": []}
        monkeypatch.setattr(sis, "fetch_sector_kline", lambda c, days=300: seq.get(c, []))
        r = sis.backfill_sector_index(db, list(seq), delay=0, stop_after_failures=3)
        assert r["filled"] == 1
        assert r["aborted"] is True, "重置后再连续失败3次仍应停手"
