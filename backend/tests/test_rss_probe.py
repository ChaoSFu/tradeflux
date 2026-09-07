"""
内存归因探针。

它存在的理由写在 app/rss_probe.py 的模块注释里：从外面采样猜了四次，四次都错。
这里测的是**它自己不会骗人**——读不出 RSS 时不编一个数，涨得不多时不刷屏。
"""
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import rss_probe as P


@pytest.fixture
def app_with_probe():
    app = FastAPI()
    app.add_middleware(P.RssProbeMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def test_读不出RSS时放行且不记录(app_with_probe, monkeypatch, caplog):
    """非 Linux / /proc 不可读。**不猜一个数**——假的 RSS 比没有更糟。"""
    monkeypatch.setattr(P, "_rss_mb", lambda: None)
    with caplog.at_level(logging.WARNING, logger="tradeflux.rss"):
        assert TestClient(app_with_probe).get("/ping").status_code == 200
    assert not [r for r in caplog.records if "[RSS]" in r.getMessage()]


def test_涨得少不记录(app_with_probe, monkeypatch, caplog):
    vals = iter([100.0, 105.0])
    monkeypatch.setattr(P, "_rss_mb", lambda: next(vals))
    with caplog.at_level(logging.WARNING, logger="tradeflux.rss"):
        TestClient(app_with_probe).get("/ping")
    assert not caplog.records, "5MB 就记一行，日志会被淹掉，真正的跳变反而看不见"


def test_涨得多要记下路径和查询串(app_with_probe, monkeypatch, caplog):
    """**query string 必须带上**：page_size=500 和 page_size=1 是两件事。"""
    vals = iter([100.0, 400.0])
    monkeypatch.setattr(P, "_rss_mb", lambda: next(vals))
    with caplog.at_level(logging.WARNING, logger="tradeflux.rss"):
        TestClient(app_with_probe).get("/ping?page_size=500")
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "+300MB" in msg and "/ping?page_size=500" in msg


def test_读取本身要便宜(monkeypatch):
    """常开的观测必须便宜。需要开关才敢用的观测，出事时永远是关着的。"""
    import time
    if P._rss_mb() is None:
        pytest.skip("非 Linux，没有 /proc/self/statm")
    t0 = time.monotonic()
    for _ in range(1000):
        P._rss_mb()
    assert (time.monotonic() - t0) < 0.5, "1000 次读取应远快于 0.5 秒"


class TestAggregate:
    """
    单行日志只回答"刚才是谁"，聚合回答"长期看谁最贵"。

    2026-09-07 那次整站超时：从外面采样猜了四次都错，探针上线一轮就抓到
    `/leader-cycle/effect` 单次 +405MB —— 而全站其余接口都在 20~40MB。
    有这张表的话，第一分钟就能看出量级差了一个数量级。
    """

    @pytest.fixture(autouse=True)
    def _clean(self):
        P._by_path.clear(); P._recent.clear()
        P._peak_rss = 0.0
        yield
        P._by_path.clear(); P._recent.clear()

    def _hit(self, app, monkeypatch, before, after, url="/ping"):
        # 用完之后一直返回 after —— stats() 自己也要读一次 RSS，
        # 拿 iter 会 StopIteration
        vals = [before, after]
        monkeypatch.setattr(P, "_rss_mb", lambda: vals.pop(0) if vals else after)
        TestClient(app).get(url)

    def test_按路径聚合并记住最贵的一次(self, app_with_probe, monkeypatch):
        self._hit(app_with_probe, monkeypatch, 100.0, 150.0, "/ping?a=1")
        self._hit(app_with_probe, monkeypatch, 150.0, 550.0, "/ping?a=2")
        e = P.stats()["by_path"][0]
        assert e["path"] == "/ping" and e["count"] == 2
        assert e["max_mb"] == 400.0
        assert e["worst_url"] == "/ping?a=2", "最贵那次的完整 URL 要留住"

    def test_明细有界(self, app_with_probe, monkeypatch):
        """**观测组件自己不能变成内存问题。**"""
        for i in range(P.RECENT_MAX + 20):
            self._hit(app_with_probe, monkeypatch, 100.0, 200.0)
        assert len(P.stats()["recent"]) == P.RECENT_MAX

    def test_超过危险线单独报(self, app_with_probe, monkeypatch, caplog):
        """要在开始换页之前就能 grep 到，而不是等整站超时了才去翻。"""
        monkeypatch.setattr(P, "DANGER_MB", 500.0)
        with caplog.at_level(logging.WARNING, logger="tradeflux.rss"):
            self._hit(app_with_probe, monkeypatch, 100.0, 600.0)
        msgs = "\n".join(r.getMessage() for r in caplog.records)
        assert "[RSS][DANGER]" in msgs and "/ping" in msgs
        assert P.stats()["danger_hits"] == 1

    def test_没到阈值不进聚合(self, app_with_probe, monkeypatch):
        self._hit(app_with_probe, monkeypatch, 100.0, 105.0)
        assert P.stats()["by_path"] == []

    def test_拿不到RSS时当前值给None(self, monkeypatch):
        """**不编一个 0。** 0 会被读成"进程只占 0MB"。"""
        monkeypatch.setattr(P, "_rss_mb", lambda: None)
        assert P.stats()["current_rss_mb"] is None
