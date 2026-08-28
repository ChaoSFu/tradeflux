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


# ── 本地缓存：一天只真正下载成功一次（2026-08-26）──────────────────────────────
#
# 判据**取自文件内容覆盖到哪个交易日**，不是路径日期、更不是当前几点。
# 按时间判断等于假设 fuyao 什么时候重新生成文件，而我们控制不了它的排期；
# 这个仓库已经在"假设别人的行为"上栽过几次（新浪盘中不发当日bar、腾讯盘中发
# 未收盘的bar），所以判据一律自证。

@pytest.fixture
def _clean_cache(monkeypatch, tmp_path):
    from app.services import fuyao_dump as fd
    monkeypatch.setattr(fd, "_CACHE_DIR", tmp_path / "cache")
    return fd


def _seed_cache(fd, kind, rows, meta):
    import json as _j
    fd._CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _write(fd._CACHE_DIR, rows)
    p.rename(fd._data_path(kind))
    fd._meta_path(kind).write_text(_j.dumps(meta), encoding="utf-8")


def test_缓存已覆盖到need_through则零请求(_clean_cache):
    fd = _clean_cache
    _seed_cache(fd, fd.DUMP_KIND_10D,
                [("600984.SH", date(2026, 8, 25), 4.6, 4.94, 4.5, 4.94),
                 ("600984.SH", date(2026, 8, 26), 4.9, 5.43, 4.6, 4.66)],
                {"path_date": "20260826", "size": 999, "max_trade_date": "2026-08-26"})
    called = []
    fd._download_url = lambda *a, **k: called.append(1) or "http://x"
    with fd.daily_k_dump("key", need_through=date(2026, 8, 26)) as p:
        assert p.exists()
    assert called == [], "缓存已覆盖 need_through，一个请求都不该发"
    assert fd.dump_last_access()["mode"] == "covered"


def test_上游没变则复用缓存只花一个字节(_clean_cache, monkeypatch):
    """盘中反复刷新的主路径：dump 还没出当日数据，但上游那份没变，重下也是白下。"""
    fd = _clean_cache
    _seed_cache(fd, fd.DUMP_KIND_10D,
                [("600984.SH", date(2026, 8, 24), 4.4, 4.5, 4.3, 4.49),
                 ("600984.SH", date(2026, 8, 25), 4.6, 4.94, 4.5, 4.94)],
                {"path_date": "20260826", "size": 1077889, "max_trade_date": "2026-08-25"})
    monkeypatch.setattr(fd, "_download_url", lambda *a, **k: "https://x/releases/20260826/f.parquet")
    monkeypatch.setattr(fd, "_remote_size", lambda *a, **k: 1077889)
    dl = []
    monkeypatch.setattr(fd, "_download_once", lambda *a, **k: dl.append(1))
    # 要 08-26 但缓存只到 08-25 —— 仍然复用，因为上游确实还没更新
    with fd.daily_k_dump("key", need_through=date(2026, 8, 26)) as p:
        assert p.exists()
    assert dl == [], "生成日与大小都没变，不该重新下载"
    assert fd.dump_last_access()["mode"] == "unchanged"


def test_上游变了才重新下载并顶掉旧文件(_clean_cache, monkeypatch):
    """收盘后 fuyao 重新生成：大小变了 → 下载一次，旧的被原子替换。"""
    fd = _clean_cache
    _seed_cache(fd, fd.DUMP_KIND_10D,
                [("600984.SH", date(2026, 8, 24), 4.4, 4.5, 4.3, 4.49),
                 ("600984.SH", date(2026, 8, 25), 4.6, 4.94, 4.5, 4.94)],
                {"path_date": "20260826", "size": 900000, "max_trade_date": "2026-08-25"})
    monkeypatch.setattr(fd, "_download_url", lambda *a, **k: "https://x/releases/20260826/f.parquet")
    monkeypatch.setattr(fd, "_remote_size", lambda *a, **k: 1077889)   # 变大了

    def _fake_dl(url, dest, timeout):
        import pyarrow as pa, pyarrow.parquet as pq
        pq.write_table(pa.table({
            "thscode": ["600984.SH"] * 3, "date_ms": [_ms(d) for d in
                (date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26))],
            "open_price": [4.4, 4.6, 4.9], "high_price": [4.5, 4.94, 5.43],
            "low_price": [4.3, 4.5, 4.6], "close_price": [4.49, 4.94, 4.66],
        }), dest)
        return 1077889
    monkeypatch.setattr(fd, "_download_once", _fake_dl)

    with fd.daily_k_dump("key", need_through=date(2026, 8, 26)) as p:
        assert fd.dump_max_date(p) == date(2026, 8, 26)
    assert fd.dump_last_access()["mode"] == "downloaded"
    import json as _j
    assert _j.loads(fd._meta_path(fd.DUMP_KIND_10D).read_text())["max_trade_date"] == "2026-08-26"
    # 第二次：缓存已覆盖 → 零请求
    calls = []
    monkeypatch.setattr(fd, "_download_url", lambda *a, **k: calls.append(1) or "http://x")
    with fd.daily_k_dump("key", need_through=date(2026, 8, 26)):
        pass
    assert calls == [] and fd.dump_last_access()["mode"] == "covered"


def test_下载失败绝不能毁掉手上那份好缓存(_clean_cache, monkeypatch):
    fd = _clean_cache
    _seed_cache(fd, fd.DUMP_KIND_10D,
                [("600984.SH", date(2026, 8, 24), 4.4, 4.5, 4.3, 4.49),
                 ("600984.SH", date(2026, 8, 25), 4.6, 4.94, 4.5, 4.94)],
                {"path_date": "20260826", "size": 900000, "max_trade_date": "2026-08-25"})
    good = fd._data_path(fd.DUMP_KIND_10D).read_bytes()
    monkeypatch.setattr(fd, "_download_url", lambda *a, **k: "https://x/releases/20260827/f.parquet")
    monkeypatch.setattr(fd, "_remote_size", lambda *a, **k: 1077889)
    monkeypatch.setattr(fd, "_download_once",
                        lambda url, dest, timeout: (_ for _ in ()).throw(fd.FuyaoError("下载不完整")))
    monkeypatch.setattr(fd.time, "sleep", lambda *_: None)
    fd.reset_dump_availability()
    # 2026-08-28 起：下载失败不再抛错，而是把手上这份旧的给出去（dump 只管历史
    # 缺口，旧的照样能补）。原来这里断言抛 FuyaoError，那是改动前的行为。
    # 本测试真正要守住的是"失败的下载绝不能毁掉好缓存"，那一条没变。
    with fd.daily_k_dump("key", need_through=date(2026, 8, 27), retries=1) as p:
        assert p.exists()
    assert fd.dump_last_access()["mode"] == "stale"
    assert fd._data_path(fd.DUMP_KIND_10D).read_bytes() == good, "旧缓存必须原样保留"


# ── dump 只管历史，当日那一根走实时行情（2026-08-26 用户指出）─────────────────
#
# 上一版的命中判据是 bars[-1].date == target_date，等于逼 dump 把当日也给齐。
# 后果是**盘中/盘前/周末 dump 完全不起作用**：盘中 dump 末根是昨天 → 0/182 命中 →
# 182 次逐股请求，而 dump 引进来就是为了消灭这 182 次请求。
#
# 用户原话："dump 数据不是为了解决拉历史k线的问题吗？它不解决获取当前实时K线数据
# 的任务，通过其他实时接口获取。" —— 而且 dump 是收盘后生成的，盘中拿它当实时数据
# 一定是错的。所以判据只该问"这段历史缺口能不能接上并补齐"。

def _hit(dump_first, dump_last, hist_last, target):
    """复刻 daily_update 里的 dump 命中判据。"""
    from datetime import timedelta
    if dump_first > hist_last + timedelta(days=1):
        return None                      # 中间有洞，接不上
    if dump_last < hist_last:
        return None                      # 没推进，等于没补
    return "full" if dump_last == target else "history_only"


D = date


def test_盘中dump只到昨天_仍然算命中历史缺口():
    """盘中：dump 末根昨天，库里历史到前天。历史缺口补上了，当日走实时行情。"""
    assert _hit(D(2026, 8, 13), D(2026, 8, 25), D(2026, 8, 24), D(2026, 8, 26)) == "history_only"


def test_盘后dump含当日_历史与当日一并命中():
    assert _hit(D(2026, 8, 13), D(2026, 8, 26), D(2026, 8, 25), D(2026, 8, 26)) == "full"


def test_盘前跑上一交易日_dump正好覆盖():
    """09:27 盘前 target 修正后是上一交易日，dump 手里正好有——上一版这里 0 命中。"""
    assert _hit(D(2026, 8, 13), D(2026, 8, 25), D(2026, 8, 24), D(2026, 8, 25)) == "full"


def test_接不上的缺口不算命中():
    """库里历史停在 7月，dump 只有近10个交易日 —— 中间是个洞，必须退回逐股拉。"""
    assert _hit(D(2026, 8, 13), D(2026, 8, 26), D(2026, 7, 20), D(2026, 8, 26)) is None


def test_dump比库里还旧不算命中():
    """停牌股：库里已经有到 08-25，dump 末根 08-20，用了等于倒退。"""
    assert _hit(D(2026, 8, 6), D(2026, 8, 20), D(2026, 8, 25), D(2026, 8, 26)) is None


# ── 一轮内的熔断（2026-08-28 生产事故）────────────────────────────────────────
#
# 那天 fuyao 整个网络不可达（IPv6 无路由立刻 errno 101，IPv4 连接超时 15 秒），
# 而 dump 在一轮 daily_update 里有三个调用点（K线主步 / 全库历史补全 / 掉出池补结算）。
# 第一个失败后另外两个还各自再试一遍、每次内含 2 次重试，对着一个已知不通的地址
# 反复重连白等了几分钟——而那一跑本来就因为退回逐股拉取慢到 885 秒。

def test_连接层失败会熔断整轮(_clean_cache, monkeypatch):
    import httpx
    fd = _clean_cache
    fd.reset_dump_availability()
    calls = []
    monkeypatch.setattr(fd, "_download_url",
                        lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(
                            httpx.ConnectError("[Errno 101] Network is unreachable")))
    monkeypatch.setattr(fd.time, "sleep", lambda *_: None)

    for _ in range(3):
        with pytest.raises(fd.FuyaoError):
            with fd.daily_k_dump("key", retries=2):
                pass
    assert len(calls) == 3, "只有第一个调用点该真的去连（3次重试），后两个直接跳过"
    assert fd.dump_last_access()["mode"] == "skipped"
    assert "unreachable" in (fd.dump_unavailable_reason() or "")


def test_业务错误不熔断(_clean_cache, monkeypatch):
    """
    key 无效、dump 还没生成之类是"这次拿不到"，不是"这条路不通"——下一个调用点
    换个 kind 或换个时间点可能就好了，不该被一棍子打死。只有连接层失败才熔断。
    """
    fd = _clean_cache
    fd.reset_dump_availability()
    calls = []
    monkeypatch.setattr(fd, "_download_url",
                        lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(
                            RuntimeError("取下载链接失败 code=2003 Invalid or revoked API key")))
    monkeypatch.setattr(fd.time, "sleep", lambda *_: None)
    for _ in range(2):
        with pytest.raises(fd.FuyaoError):
            with fd.daily_k_dump("key", retries=1):
                pass
    assert len(calls) == 4, "两个调用点各自试 2 次，业务错误不熔断"
    assert fd.dump_unavailable_reason() is None


def test_reset清掉上一轮的熔断(_clean_cache, monkeypatch):
    import httpx
    fd = _clean_cache
    fd.reset_dump_availability()
    monkeypatch.setattr(fd, "_download_url",
                        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("unreachable")))
    monkeypatch.setattr(fd.time, "sleep", lambda *_: None)
    with pytest.raises(fd.FuyaoError):
        with fd.daily_k_dump("key", retries=0):
            pass
    assert fd.dump_unavailable_reason() is not None
    fd.reset_dump_availability()
    assert fd.dump_unavailable_reason() is None, "下一轮必须重新试，网络可能已经恢复"


def test_连接超时单独设短(_clean_cache):
    """总超时要照顾下载1MB的耗时不能设小，但"连不上"和"下得慢"是两回事。"""
    fd = _clean_cache
    t = fd._timeouts(180.0)
    assert t.connect == fd._CONNECT_TIMEOUT and t.connect < 10
    assert t.read == 180.0


# ── 下不到新的就用旧的（2026-08-28 用户提出）──────────────────────────────────
#
# 用户原话："fuyao 的接口只要一天成功一次就行吧？dump 成功了就行。为什么会卡住
# 核心更新的流程？"——问到了根子上。
#
# 那天 fuyao 整网不可达，而缓存里明明有覆盖到 08-27 的 dump。当时的逻辑是
# "下载失败 → 抛错"，把那份缓存一起扔了，223 只全部退回逐股拉取，K线步骤从
# 5 秒变成 885 秒。而 **dump 只管历史缺口，当日那一根本来就走实时行情**——
# 库里历史停在 08-27、缓存覆盖到 08-27，接得上，本该 218/218 命中。
# 是把"拿不到最新的"错当成了"什么都没有"。

def test_下载失败但有缓存则用旧的(_clean_cache, monkeypatch):
    import httpx
    from datetime import date as _d
    fd = _clean_cache
    fd.reset_dump_availability()
    _seed_cache(fd, fd.DUMP_KIND_10D,
                [("600984.SH", _d(2026, 8, 26), 4.6, 4.94, 4.5, 4.94),
                 ("600984.SH", _d(2026, 8, 27), 4.9, 5.43, 4.6, 4.66)],
                {"path_date": "20260827", "size": 1, "max_trade_date": "2026-08-27"})
    monkeypatch.setattr(fd, "_download_url",
                        lambda *a, **k: (_ for _ in ()).throw(
                            httpx.ConnectError("[Errno 101] Network is unreachable")))
    monkeypatch.setattr(fd.time, "sleep", lambda *_: None)

    # 要 08-28，缓存只到 08-27，且下不到新的 —— 仍然给出旧缓存
    with fd.daily_k_dump("key", need_through=_d(2026, 8, 28), retries=2) as p:
        assert fd.dump_max_date(p) == _d(2026, 8, 27)
    assert fd.dump_last_access()["mode"] == "stale"


def test_熔断后仍然用旧缓存而不是抛错(_clean_cache, monkeypatch):
    """第二、三个调用点：既不再去连（熔断），也不该白白丢掉手上的缓存。"""
    import httpx
    from datetime import date as _d
    fd = _clean_cache
    fd.reset_dump_availability()
    _seed_cache(fd, fd.DUMP_KIND_10D,
                [("600984.SH", _d(2026, 8, 26), 4.6, 4.94, 4.5, 4.94),
                 ("600984.SH", _d(2026, 8, 27), 4.9, 5.43, 4.6, 4.66)],
                {"path_date": "20260827", "size": 1, "max_trade_date": "2026-08-27"})
    calls = []
    monkeypatch.setattr(fd, "_download_url",
                        lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(
                            httpx.ConnectError("unreachable")))
    monkeypatch.setattr(fd.time, "sleep", lambda *_: None)
    for _ in range(3):
        with fd.daily_k_dump("key", need_through=_d(2026, 8, 28), retries=2) as p:
            assert p.exists()
    assert len(calls) == 3, "只有第一个调用点真去连了3次，后两个熔断"
    assert fd.dump_last_access()["mode"] == "stale"


def test_没有缓存才抛错(_clean_cache, monkeypatch):
    import httpx
    from datetime import date as _d
    fd = _clean_cache
    fd.reset_dump_availability()
    monkeypatch.setattr(fd, "_download_url",
                        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("unreachable")))
    monkeypatch.setattr(fd.time, "sleep", lambda *_: None)
    with pytest.raises(fd.FuyaoError):
        with fd.daily_k_dump("key", need_through=_d(2026, 8, 28), retries=0):
            pass
    assert fd.dump_last_access()["mode"] == "failed"


# ── need_through 的语义：dump 只需覆盖到"最后一个已收盘的交易日"────────────────
#
# 用户 2026-08-27 定的策略原话：
#   "前一天收盘后到当前收盘前，都是可以用一个缓存文件。能用缓存就必须用缓存，
#    没法用缓存的时候就必须下载，下载不成功再走兜底逻辑。"
#
# 早先传的是 target_date（今天），盘中 dump 本来就不可能有今天，条件①永远不满足，
# 于是每次盘中刷新都白白探一次网络——而缓存其实完全够用，因为 dump 只管历史缺口，
# 当日那一根走实时行情。

def test_盘中传上一交易日则命中缓存零请求(_clean_cache, monkeypatch):
    from datetime import date as _d
    fd = _clean_cache
    fd.reset_dump_availability()
    _seed_cache(fd, fd.DUMP_KIND_10D,
                [("600984.SH", _d(2026, 8, 26), 4.6, 4.94, 4.5, 4.94),
                 ("600984.SH", _d(2026, 8, 27), 4.9, 5.43, 4.6, 4.66)],
                {"path_date": "20260827", "size": 1, "max_trade_date": "2026-08-27"})
    called = []
    monkeypatch.setattr(fd, "_download_url", lambda *a, **k: called.append(1) or "http://x")
    # 08-28 盘中：need_through = 上一交易日 08-27，缓存正好覆盖
    with fd.daily_k_dump("key", need_through=_d(2026, 8, 27)) as p:
        assert p.exists()
    assert called == [] and fd.dump_last_access()["mode"] == "covered"


def test_盘后传当天则必须去拿新的(_clean_cache, monkeypatch):
    """收盘之后 dump 该有今天了，缓存只到昨天就不算够用，必须去拿。"""
    from datetime import date as _d
    fd = _clean_cache
    fd.reset_dump_availability()
    _seed_cache(fd, fd.DUMP_KIND_10D,
                [("600984.SH", _d(2026, 8, 26), 4.6, 4.94, 4.5, 4.94),
                 ("600984.SH", _d(2026, 8, 27), 4.9, 5.43, 4.6, 4.66)],
                {"path_date": "20260827", "size": 900000, "max_trade_date": "2026-08-27"})
    called = []
    # 上游那份还没重新生成（生成日和大小都跟缓存一致）→ 问过之后仍然复用缓存
    monkeypatch.setattr(fd, "_download_url",
                        lambda *a, **k: called.append(1) or "https://x/releases/20260827/f.parquet")
    monkeypatch.setattr(fd, "_remote_size", lambda *a, **k: 900000)
    with fd.daily_k_dump("key", need_through=_d(2026, 8, 28)) as p:
        assert p.exists()
    assert called == [1], "缓存不够用，至少要去问一次上游"
    assert fd.dump_last_access()["mode"] == "unchanged"


# ── 旧缓存够不够用（2026-08-28 用户提出）──────────────────────────────────────
#
# 用户："下载失败 → 用手上那份旧的，这个需要校验那份旧的够不够用。"
#
# 正确性上的校验一直都在调用方，而且是**逐股**判的（比全局判更准）：
#   · K线主步：bars[0].date > hist[-1].date+1 → 中间有洞，跳过该股
#             bars[-1].date < hist[-1].date  → 没推进，跳过该股
#   · 掉出池补结算：要求 bars[-1].date == target_date 精确匹配
#   · 全库历史补全：只写 date < target_date 且不存在的行
# 所以陈旧的 dump 不会产生错数据，只会命中率低。
#
# 缺的是"用之前判一下值不值得用"和"用之后说清楚旧了多少"——否则日志显示
# "命中 0/218" 看起来像另一种故障。下面钉住这两条判据本身。

def _bridgeable(dump_first, dump_last, hist_last):
    """复刻 K线主步的逐股命中判据。"""
    from datetime import timedelta as _td
    if dump_first > hist_last + _td(days=1):
        return False          # 中间有洞
    if dump_last < hist_last:
        return False          # 没推进
    return True


class TestStaleUsability:
    D = date

    def test_旧一天的缓存仍然够用(self):
        """08-28 盘后没下成，缓存到 08-27，库里历史也到 08-27 —— 正是自愈那条路。"""
        assert _bridgeable(self.D(2026, 8, 13), self.D(2026, 8, 27), self.D(2026, 8, 27))

    def test_缓存比库里历史还旧则不用(self):
        """停牌股或前几天补过的股票：库里已经到 08-27，缓存只到 08-20，用了等于倒退。"""
        assert not _bridgeable(self.D(2026, 8, 6), self.D(2026, 8, 20), self.D(2026, 8, 27))

    def test_中间有洞则不用(self):
        """库里历史停在 7 月，缓存只有近 10 个交易日 —— 接不上，必须逐股补。"""
        assert not _bridgeable(self.D(2026, 8, 13), self.D(2026, 8, 27), self.D(2026, 7, 20))

    def test_必要条件_缓存比所有股票历史都旧就别解析(self):
        """
        调用方在 load_bars 之前做的那个前置判断：只要没有任何一只股票的历史末日
        <= 缓存覆盖日，这份缓存一根也接不上，解析 1MB parquet 纯属白费。
        这是**必要条件**不是充分条件——具体每只接不接得上仍由上面的逐股判据决定，
        不在两处各算一遍。
        """
        cache_max = self.D(2026, 8, 20)
        hist_ends = [self.D(2026, 8, 27), self.D(2026, 8, 26), self.D(2026, 8, 28)]
        assert not any(h <= cache_max for h in hist_ends), "全比缓存新 → 该跳过解析"
        hist_ends.append(self.D(2026, 8, 18))
        assert any(h <= cache_max for h in hist_ends), "有一只够得着 → 该解析"


def test_stale访问会带出旧到什么程度(_clean_cache, monkeypatch):
    """日志要能说出"旧缓存覆盖至X、本需覆盖至Y"，只报"命中0"会把人带偏。"""
    import httpx
    from datetime import date as _d
    fd = _clean_cache
    fd.reset_dump_availability()
    _seed_cache(fd, fd.DUMP_KIND_10D,
                [("600984.SH", _d(2026, 8, 26), 4.6, 4.94, 4.5, 4.94),
                 ("600984.SH", _d(2026, 8, 27), 4.9, 5.43, 4.6, 4.66)],
                {"path_date": "20260827", "size": 1, "max_trade_date": "2026-08-27"})
    monkeypatch.setattr(fd, "_download_url",
                        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("unreachable")))
    monkeypatch.setattr(fd.time, "sleep", lambda *_: None)
    with fd.daily_k_dump("key", need_through=_d(2026, 8, 28), retries=0):
        pass
    la = fd.dump_last_access()
    assert la["mode"] == "stale"
    assert la["stale_through"] == "2026-08-27" and la["need_through"] == "2026-08-28"
