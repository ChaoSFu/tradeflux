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
