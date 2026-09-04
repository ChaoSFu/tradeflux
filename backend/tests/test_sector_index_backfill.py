"""
板块指数回填的可重入性与限流自保。

背景：板块指数是 RS_sector 的基准，只能从东财 push2his 拿（腾讯没有 BK 板块码，
fetch_index_kline 那条腾讯优先的路对板块必然失败）。而 **push2his 限流很凶**——
实测约 15 次快速请求就把开发机 IP 打进限流，连既有代码依赖的上证指数也一起拉不到。

2026-09-04 重做限速。第一版是固定 sleep + 连续失败 N 次停手，两个洞：

  · **分不清"被限流"和"这个板块本来就没数据"**：两者都返回 []，于是连续几个没有
    指数日线的板块会被误判成限流而停手，反过来真被限流时又可能因为中间夹了成功
    而一直硬撑。分不清就只有两种结局：该退避时硬打，不该退避时空等。
  · **停手只约束本次运行**。真正打死 IP 的是"跑挂了→立刻重跑"的循环，而那个循环
    跨进程。所以判定被限流后要把冷却写进库，任何进程重跑都被挡下。
"""
from datetime import date, timedelta

import pytest

from app.models.market_index import SectorIndexDaily
from app.services import sector_index_service as sis
from app.services.rate_limiter import (
    AdaptiveRateLimiter, Outcome, cooldown_remaining, clear_cooldown, set_cooldown,
)


def _fake_bars(n, start=date(2026, 1, 5)):
    return [{"date": str(start + timedelta(days=i)), "open": 100.0, "close": 100.0 + i,
             "high": 101.0 + i, "low": 99.0 + i, "volume": 1e6, "amount": 1e8,
             "pct_change": 1.0} for i in range(n)]


def _stub(monkeypatch, table, calls=None):
    """
    table: {板块码: "ok" | "blocked" | "no_data" | "error"}，缺省当 blocked。
    打桩的是 detailed 版——限速逻辑全靠它区分失败原因。
    """
    def _f(code, days=300):
        if calls is not None:
            calls.append(code)
        kind = table.get(code, "blocked")
        return (_fake_bars(80) if kind == "ok" else [], kind,
                "" if kind == "ok" else f"模拟{kind}", None)
    monkeypatch.setattr(sis, "fetch_sector_kline_detailed", _f)


class TestBackfillResumable:

    def test_已补够的板块直接跳过(self, db, monkeypatch):
        for i in range(sis.MIN_BARS_FOR_RS):
            db.add(SectorIndexDaily(sector_code="BK0832",
                                    date=date(2026, 1, 5) + timedelta(days=i),
                                    close=100.0 + i))
        db.flush()
        calls = []
        _stub(monkeypatch, {"BK0832": "ok"}, calls)
        r = sis.backfill_sector_index(db, ["BK0832"], delay=0)
        assert r["skipped"] == 1 and calls == [], "补够了就不该再打网络"

    def test_每只板块独立提交中途掐断不丢已补的(self, db, monkeypatch):
        _stub(monkeypatch, {"BK0001": "ok", "BK0002": "ok", "BK0003": "blocked"})
        r = sis.backfill_sector_index(db, ["BK0001", "BK0002", "BK0003"], delay=0)
        assert r["filled"] == 2 and r["failed"] == ["BK0003"]
        got = {c for (c,) in db.query(SectorIndexDaily.sector_code).distinct().all()}
        assert got == {"BK0001", "BK0002"}, "失败那只不写，成功的两只必须已落库"

    def test_重跑只补没补够的(self, db, monkeypatch):
        _stub(monkeypatch, {"BK0001": "ok", "BK0002": "ok", "BK0003": "blocked"})
        sis.backfill_sector_index(db, ["BK0001", "BK0002", "BK0003"], delay=0)
        clear_cooldown(db, sis.THROTTLE_DOMAIN)
        calls = []
        _stub(monkeypatch, {c: "ok" for c in ("BK0001", "BK0002", "BK0003")}, calls)
        r = sis.backfill_sector_index(db, ["BK0001", "BK0002", "BK0003"], delay=0)
        assert calls == ["BK0003"], f"重跑只该补失败那只，实际打了 {calls}"
        assert r["skipped"] == 2


class TestThrottleClassification:
    """「没拿到」必须能分成两类，否则限速做不对。"""

    def test_没有指数日线的板块不触发退避也不算失败(self, db, monkeypatch):
        """
        退避解决不了"这个板块根本没有指数日线"。旧版把它和被限流混在一起，
        于是连续几个空板块就能把整批回填假停手。
        """
        codes = [f"BK{i:04d}" for i in range(8)]
        calls = []
        _stub(monkeypatch, {c: "no_data" for c in codes}, calls)
        r = sis.backfill_sector_index(db, codes, delay=0, stop_after_failures=3)
        assert r["aborted"] is False, "没数据不是被限流，不该停手"
        assert len(calls) == 8 and r["no_data"] == codes and r["failed"] == []
        assert cooldown_remaining(db, sis.THROTTLE_DOMAIN) is None, "更不该写冷却"

    def test_连续被拦到阈值主动停手并写冷却(self, db, monkeypatch):
        calls = []
        _stub(monkeypatch, {}, calls)          # 全部 blocked
        codes = [f"BK{i:04d}" for i in range(20)]
        r = sis.backfill_sector_index(db, codes, delay=0, stop_after_failures=3)
        assert r["aborted"] is True
        assert len(calls) == 3, f"到阈值就该停，实际打了 {len(calls)} 次"
        assert cooldown_remaining(db, sis.THROTTLE_DOMAIN) is not None

    def test_中间成功会重置连续被拦计数(self, db, monkeypatch):
        codes = [f"BK{i:04d}" for i in range(6)]
        _stub(monkeypatch, {"BK0002": "ok"})
        r = sis.backfill_sector_index(db, codes, delay=0, stop_after_failures=3)
        assert r["filled"] == 1
        assert r["aborted"] is True, "重置后再连续被拦3次仍应停手"


class TestCooldownAcrossRuns:
    """真正打死 IP 的是"跑挂了→立刻重跑"，那个循环跨进程。"""

    def test_冷却期内一个请求都不发(self, db, monkeypatch):
        set_cooldown(db, sis.THROTTLE_DOMAIN, 600)
        calls = []
        _stub(monkeypatch, {"BK0001": "ok"}, calls)
        r = sis.backfill_sector_index(db, ["BK0001"], delay=0)
        assert calls == [], "冷却期内重跑必须被挡下，一个请求都不许发"
        assert r["cooling_down"] is True and r["aborted"] is True

    def test_冷却到期后自动放行(self, db, monkeypatch):
        set_cooldown(db, sis.THROTTLE_DOMAIN, -1)      # 已过期
        calls = []
        _stub(monkeypatch, {"BK0001": "ok"}, calls)
        sis.backfill_sector_index(db, ["BK0001"], delay=0)
        assert calls == ["BK0001"]

    def test_明确要求时可以无视冷却(self, db, monkeypatch):
        set_cooldown(db, sis.THROTTLE_DOMAIN, 600)
        calls = []
        _stub(monkeypatch, {"BK0001": "ok"}, calls)
        sis.backfill_sector_index(db, ["BK0001"], delay=0, ignore_cooldown=True)
        assert calls == ["BK0001"], "人明确说要打就放行，但那是显式选择不是默认"


class TestRequestBudget:

    def test_单次运行有请求硬上限(self, db, monkeypatch):
        """无人值守跑一夜不该打出几百次。剩下的下次重跑，反正可重入。"""
        codes = [f"BK{i:04d}" for i in range(50)]
        calls = []
        _stub(monkeypatch, {c: "ok" for c in codes}, calls)
        r = sis.backfill_sector_index(db, codes, delay=0, max_requests=10)
        assert len(calls) == 10 and r["aborted"] is True and r["filled"] == 10


class TestAdaptiveRateLimiter:

    def test_被拦时指数退避(self):
        lim = AdaptiveRateLimiter(base_delay=2.0, max_delay=90.0)
        for _ in range(3):
            lim.on_outcome(Outcome(kind="blocked"))
        assert lim.delay == 16.0

    def test_退避有上限(self):
        lim = AdaptiveRateLimiter(base_delay=2.0, max_delay=10.0)
        for _ in range(20):
            lim.on_outcome(Outcome(kind="blocked"))
        assert lim.delay == 10.0

    def test_服务端说等多久就等多久(self):
        """Retry-After 是对方明说的配额，不许用更短的间隔盖过它。"""
        lim = AdaptiveRateLimiter(base_delay=2.0, max_delay=300.0)
        lim.on_outcome(Outcome(kind="blocked", retry_after=120))
        assert lim.delay == 120.0

    def test_恢复要慢不能一次成功就跳回基准(self):
        """一次成功不代表限流解除，可能只是恰好放行了一个。"""
        lim = AdaptiveRateLimiter(base_delay=2.0)
        for _ in range(3):
            lim.on_outcome(Outcome(kind="blocked"))     # 16.0
        lim.on_outcome(Outcome(kind="ok"))
        assert lim.delay == pytest.approx(12.8)
        for _ in range(20):
            lim.on_outcome(Outcome(kind="ok"))
        assert lim.delay == 2.0, "一直成功最终回到基准，但要慢慢回"

    def test_没数据不触发退避(self):
        lim = AdaptiveRateLimiter(base_delay=2.0)
        lim.on_outcome(Outcome(kind="no_data"))
        assert lim.delay == 2.0

    def test_节拍带抖动(self, monkeypatch):
        """
        每 2.0 秒整打一次本身就是机器指纹。抖动不是装饰。
        """
        slept = []
        monkeypatch.setattr("app.services.rate_limiter.time.sleep", slept.append)
        lim = AdaptiveRateLimiter(base_delay=2.0, jitter=0.35)
        for _ in range(12):
            lim.before_request()
        assert len(set(slept)) > 1, "间隔必须有随机抖动，不能是等差的机械节拍"
        assert all(2.0 * 0.6 <= s <= 2.0 * 1.4 for s in slept)

    def test_第一次请求不等待(self, monkeypatch):
        slept = []
        monkeypatch.setattr("app.services.rate_limiter.time.sleep", slept.append)
        AdaptiveRateLimiter(base_delay=2.0).before_request()
        assert slept == []
