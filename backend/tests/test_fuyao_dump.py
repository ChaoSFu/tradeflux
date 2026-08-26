"""
fuyao 全市场日K dump 解析（2026-08-26新增）。

dump 成为K线主力源之后，这里的解析逻辑决定了全库的涨停判定，所以必须钉住：
它跟腾讯/新浪那两条路走的是**同一个** build_kline_bar，不能因为"这根bar是从
dump来的"得出不同的涨停结论。
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services.fuyao_dump import load_bars, _ms_to_date

SH = timezone(timedelta(hours=8))


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=SH).timestamp() * 1000)


def _write(tmp_path: Path, rows) -> Path:
    """rows: [(thscode, date, open, high, low, close)]"""
    p = tmp_path / "d.parquet"
    pq.write_table(pa.table({
        "thscode": [r[0] for r in rows],
        "date_ms": [_ms(r[1]) for r in rows],
        "open_price": [r[2] for r in rows],
        "high_price": [r[3] for r in rows],
        "low_price": [r[4] for r in rows],
        "close_price": [r[5] for r in rows],
    }), p)
    return p


def test_日期按上海时区解释(tmp_path):
    # 用本机时区解释会在 UTC+0 以西的机器上整体偏一天
    assert _ms_to_date(_ms(date(2026, 8, 26))) == date(2026, 8, 26)


def test_丢弃最早一根因为它没有前收(tmp_path):
    p = _write(tmp_path, [
        ("600984.SH", date(2026, 8, 24), 4.4, 4.5, 4.3, 4.49),
        ("600984.SH", date(2026, 8, 25), 4.6, 4.94, 4.5, 4.94),
        ("600984.SH", date(2026, 8, 26), 4.9, 5.43, 4.6, 4.66),
    ])
    bars = load_bars(p, {"600984": False})["600984"]
    assert [b.date for b in bars] == [date(2026, 8, 25), date(2026, 8, 26)]


def test_涨跌幅由相邻收盘价精确算出(tmp_path):
    p = _write(tmp_path, [
        ("600984.SH", date(2026, 8, 25), 4.6, 4.94, 4.5, 4.94),
        ("600984.SH", date(2026, 8, 26), 4.9, 5.43, 4.6, 4.66),
    ])
    b = load_bars(p, {"600984": False})["600984"][0]
    assert b.date == date(2026, 8, 26)
    assert b.close_price == 4.66
    assert b.pct_change == pytest.approx((4.66 / 4.94 - 1) * 100)


def test_炸板判定跟其它数据源同一套(tmp_path):
    """600984 实盘：08-26 盘中封到涨停 5.43 又打开，收 4.66。必须判成炸板、不是涨停。"""
    p = _write(tmp_path, [
        ("600984.SH", date(2026, 8, 25), 4.6, 4.94, 4.5, 4.94),
        ("600984.SH", date(2026, 8, 26), 4.9, 5.43, 4.6, 4.66),
    ])
    b = load_bars(p, {"600984": False})["600984"][0]
    assert b.is_limit_up is False
    assert b.is_broken_board is True, "最高价触及涨停价但收盘没封住 = 炸板"


def test_真涨停能判出来(tmp_path):
    p = _write(tmp_path, [
        ("600984.SH", date(2026, 8, 24), 4.4, 4.5, 4.3, 4.49),
        ("600984.SH", date(2026, 8, 25), 4.7, 4.94, 4.6, 4.94),   # 4.49*1.10=4.939→4.94
    ])
    b = load_bars(p, {"600984": False})["600984"][0]
    assert b.is_limit_up is True


def test_创业板按20个点判涨停(tmp_path):
    p = _write(tmp_path, [
        ("301117.SZ", date(2026, 8, 25), 10.0, 10.0, 10.0, 10.00),
        ("301117.SZ", date(2026, 8, 26), 11.5, 12.0, 11.0, 12.00),  # +20%
    ])
    b = load_bars(p, {"301117": False})["301117"][0]
    assert b.is_limit_up is True, "创业板20%涨停不能按主板10%判"


def test_换手率一律为None不用0冒充(tmp_path):
    p = _write(tmp_path, [
        ("600984.SH", date(2026, 8, 25), 4.6, 4.94, 4.5, 4.94),
        ("600984.SH", date(2026, 8, 26), 4.9, 5.43, 4.6, 4.66),
    ])
    b = load_bars(p, {"600984": False})["600984"][0]
    assert b.turnover_rate is None, "dump不提供换手率，写None不写0——0是个合法数字，会静默污染评分"


def test_只解析关心的股票不把全市场读进来(tmp_path):
    """用户明确要求：数据不能爆炸，只更新库里已关注的。"""
    p = _write(tmp_path, [
        ("600984.SH", date(2026, 8, 25), 4.6, 4.94, 4.5, 4.94),
        ("600984.SH", date(2026, 8, 26), 4.9, 5.43, 4.6, 4.66),
        ("000001.SZ", date(2026, 8, 25), 10.0, 10.1, 9.9, 10.0),
        ("000001.SZ", date(2026, 8, 26), 10.0, 10.2, 9.9, 10.1),
    ])
    out = load_bars(p, {"600984": False})
    assert set(out) == {"600984"}


def test_无效收盘价整行跳过(tmp_path):
    p = _write(tmp_path, [
        ("600984.SH", date(2026, 8, 24), 4.4, 4.5, 4.3, 0.0),     # 停牌/脏数据
        ("600984.SH", date(2026, 8, 25), 4.6, 4.94, 4.5, 4.94),
        ("600984.SH", date(2026, 8, 26), 4.9, 5.43, 4.6, 4.66),
    ])
    bars = load_bars(p, {"600984": False})["600984"]
    assert [b.date for b in bars] == [date(2026, 8, 26)]


# ── 2026-08-26 生产事故：兜底路径自己崩了 ─────────────────────────────────────
#
# fuyao dump 下载被 S3 中途掐断（收到 360820 字节 / 应为 1077889），daily_update 的
# except 分支本该记个警告然后退回逐股拉取。但那句写的是 log.warning()，而 StepLogger
# 当时只有 info/error —— **错误处理器自己抛 AttributeError**，把一个可兜底的瞬时故障
# 升级成了「拉取K线数据」整步失败、当天日更中止。
#
# 兜底路径写错比没有兜底更糟：它把小故障放大成大故障，而且平时永远测不出来——
# 只有真出事那一次才执行到。所以这里专门测"出事的时候"。

def test_StepLogger_有warning方法(tmp_path, monkeypatch):
    """三处 except 分支都在调它。少一个方法就等于三颗定时炸弹。"""
    import importlib.util
    from pathlib import Path as _P
    spec = importlib.util.spec_from_file_location(
        "_du_log", _P(__file__).resolve().parents[1] / "scripts" / "daily_update.py")
    du = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(du)
    for m in ("info", "warning", "error", "begin", "end"):
        assert callable(getattr(du.StepLogger, m, None)), f"StepLogger 缺 {m}()"


def test_下载不完整必须报错而不是留个截断文件(tmp_path, monkeypatch):
    """
    短读不一定抛异常，也可能安静结束——那样落地的就是截断的 Parquet：轻则解析时
    才炸，重则解析出一部分数据，我们拿半份行情去算涨停。字节数必须自己对。
    """
    import httpx
    from app.services import fuyao_dump as fd

    class _Resp:
        headers = {"content-length": "1077889"}
        def raise_for_status(self): pass
        def iter_bytes(self, n): yield b"x" * 360820      # 只收到三分之一
    class _Ctx:
        def __enter__(self): return _Resp()
        def __exit__(self, *a): return False
    class _Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, *a, **kw): return _Ctx()
    monkeypatch.setattr(fd.httpx, "Client", _Client)

    dest = tmp_path / "d.parquet"
    with pytest.raises(fd.FuyaoError, match="下载不完整"):
        fd._download_once("http://x", dest, 10)


def test_下载失败重试后仍失败则抛FuyaoError并清干净(tmp_path, monkeypatch):
    from app.services import fuyao_dump as fd
    calls = []
    monkeypatch.setattr(fd, "_download_url", lambda k, kind, **kw: calls.append(1) or "http://x")
    def _boom(url, dest, timeout):
        dest.write_bytes(b"half")          # 模拟落了半个文件
        raise fd.FuyaoError("下载不完整：收到 1 字节，应为 2")
    monkeypatch.setattr(fd, "_download_once", _boom)
    monkeypatch.setattr(fd.time, "sleep", lambda *_: None)

    with pytest.raises(fd.FuyaoError):
        with fd.daily_k_dump("k", retries=2) as p:
            pass
    assert len(calls) == 3, "应重试 2 次，且每次都重新取链接（预签名链接只活5分钟）"
    import glob, tempfile
    assert not glob.glob(f"{tempfile.gettempdir()}/fuyao_*.parquet"), "半份文件必须清掉"
