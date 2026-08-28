"""
同花顺官方 API（fuyao.aicubes.cn）接入（2026-08-26新增）。

两个用途，走两个不同的端点、**刻意用不同的复权口径**：

  · daily-k-10d dump（本模块主体）—— K线主力源，`adjusted=none` 未复权。
    涨停判定按的是当日实际成交价，前复权会让历史涨停价失真。
  · prices/historical（fetch_interval_returns）—— 区间涨幅，`adjust=forward` 前复权。
    "这只票近60日涨了多少"问的是投资者的真实复合收益，必须含除权除息调整。

同一个数据源两种口径不是不一致，是两个问题的正确答案各不相同。

━━ 以下是 dump 部分 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 为什么要有它

我们此前为候选池里每一只股票单独打腾讯/新浪的K线接口，一轮几百次请求。这是
本仓库所有"限流"痛苦的根源，也是 2026-08-26 那次生产事故的直接起因：收盘那一跑
有 19% 的股票K线拉取失败，代码退回"保留上次可信值"，把盘中现价当成了收盘价。

fuyao 的 `daily-k-10d` 把**全市场 5,545 只股票、最近 10 个交易日**装在一个 1MB 的
Parquet 里，一次下载 9 秒。请求数从几百降到 1。

## 刻意的边界（用户 2026-08-26 定的）

1. **一天只下一次**，不做常驻缓存。
2. **只用来更新库里已经关注的股票**，不因为 dump 里有全市场就把快照表撑爆
   （5,545 只 × 每天 vs 现在的几百行）。
3. **一天只真正下载成功一次**：本地缓存一份，按"内容覆盖到哪个交易日"判定是否
   复用（不是按路径日期——路径里的日期是生成日，同一路径的早盘版本可能只到昨天）。
   上游出了新版本才重新下载并顶掉旧的。原来是"用完即删"，用户 2026-08-26 改的：
   盘中手动刷新一天点过 9 次，每次重下 1MB 纯属浪费，而且每多下一次就多一次
   被 S3 中途掐断的机会——那正是当天那场事故的导火索。
4. **新进候选池、库里没有历史的股票走原来的逐股接口**——它们需要 65 天窗口，
   dump 的 10 天不够，而这类股票每天只有个位数到几十只，请求量可控。

第 4 条不是妥协，是 dump 的真实能力边界：10 个交易日刚好覆盖"库里已有历史、
只需补最近几天缺口"这个大头（常态 2-3 天，跨周末/漏跑最多也就几天），补不了
从零重建 65 日窗口。

## 两个必须说清楚的口径问题

**未复权。** dump 的 `adjusted` 列固定是 `none`。这对我们**是好事**：涨停判定
按的是当日实际成交价，前复权反而会让历史涨停价失真（exact_limit_price 一直在跟
这个较劲）。但它跟库里历史快照的口径不完全一致——那些行存的是当时拉到的腾讯
前复权值，本身就是个补丁摞补丁的拼盘（每天写入时的复权基准都不同，从没重整过）。
所以引入 dump 是让最近这段窗口变得**一致且正确**，不是让整条序列变干净。窗口里
有除权事件的股票，MA30/MA60 会有一个小台阶，这是已知的、可接受的。

**没有换手率。** dump 只有 OHLC + volume + turnover(成交额)，没有换手率，
fuyao 的 A股行情快照和估值快照里也都没有（估值只给 PE/PB/PS/PCF，连流通股本
都没有，推都推不出来）。所以这里一律 turnover_rate=None ——"不知道"就写 None，
不用 0 冒充，这是本仓库反复踩过的坑。换手率是另一条独立的线，走腾讯行情。
"""
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import gettempdir
from typing import Dict, Iterator, List, Optional

import httpx

from .eastmoney_fetcher import KLineBar, _is_bj_code, build_kline_bar, get_limit_pct

FUYAO_BASE = "https://fuyao.aicubes.cn"
DUMP_KIND_10D = "daily-k-10d"
_SH_TZ = timezone(timedelta(hours=8))


class FuyaoError(RuntimeError):
    """fuyao 请求/响应层失败。跟"数据本身没有"是两回事，见 fetch_interval_returns。"""


def get_api_key() -> Optional[str]:
    """
    取 API Key。没配就是没启用，返回 None，不报错。

    **必须走 settings 而不是只读 os.environ**：本项目的 .env 是 pydantic-settings
    加载的，它只填充 settings 对象，**不会把值注入 os.environ**。第一版只读
    os.environ，结果用户明明在 .env 里配好了，日志还在打"未配置 FUYAO_API_KEY"。
    os.environ 作为补充保留——systemd 的 Environment= 或 shell export 走的是那条路。
    """
    from ..config import settings
    key = (getattr(settings, "FUYAO_API_KEY", "") or "").strip()
    if not key:
        key = (os.environ.get("FUYAO_API_KEY") or "").strip()
    return key or None


def _download_url(api_key: str, kind: str, timeout: int = 30) -> Optional[str]:
    """
    取 S3 预签名下载链接。链接只活 5 分钟，所以不能缓存，每次下载前重新要。

    fuyao 的 HTTP 状态码恒为 200，业务结果看信封里的 `code` 字段——不能只看
    resp.status_code 就当成功。
    """
    with httpx.Client(timeout=_timeouts(timeout)) as c:
        resp = c.get(f"{FUYAO_BASE}/api/dump/market-dumps/{kind}/download-url",
                     headers={"X-api-key": api_key})
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"取下载链接失败 code={body.get('code')} {body.get('message')}")
    return (body.get("data") or {}).get("presigned_url")


# ── 本地缓存 ─────────────────────────────────────────────────────────────────
#
# 同一个交易日内，dump 的内容**不一定**恒定：路径里的日期是"生成日"，收盘前生成的
# 20260826 版本可能只含到 08-25，收盘后重新生成、路径不变而内容变了。所以缓存不能
# 按日期判定，要按内容。
#
# 缓存策略（用户 2026-08-26 定，取代原来的"用完即删"）：
#   1. 手上这份已经覆盖了要的交易日 → 直接用，**零网络请求**
#   2. 否则花 1 个字节问一下上游文件多大（Range: bytes=0-0 拿 Content-Range 总长）：
#      生成日和大小都没变 ⇒ 上游还是那份，重下也是白下，继续用缓存
#   3. 变了才真下载；下载成功后原子替换，旧的自然被顶掉
#
# 动机是盘中手动刷新：用户一天点过 9 次更新，每次重下 1MB 纯属浪费，而且每多一次
# 下载就多一次被 S3 中途掐断的机会（那正是今天那场事故的导火索）。
_CACHE_DIR = Path(gettempdir()) / "tradeflux_fuyao_dump"

# 上一次取 dump 是怎么解决的。**刻意不按时间判断该不该重下**——那等于假设 fuyao
# 什么时候重新生成文件，而我们控制不了它的排期。这个仓库已经在"假设别人的行为"上
# 栽过好几次（新浪盘中不发当日bar、腾讯盘中发未收盘的bar），所以判据一律取自内容
# 本身。把每次的解决方式记下来，跑几天就能免费得到"各时段 dump 到底什么样"的
# 实证画像，而不是现在拍脑袋。
_LAST_ACCESS: dict = {"mode": None, "at": None, "attempts": 0, "errors": [], "seconds": 0.0}

# 连接阶段单独给一个短超时。总超时（timeout 参数）要照顾下载 1MB 的耗时，不能设小，
# 但**连不上**和**下得慢**是两回事：2026-08-28 生产上 fuyao 整个不可达（IPv6 无路由
# 立刻 errno 101，IPv4 连接超时 15 秒），用总超时去等连接，一次就要几十秒。
_CONNECT_TIMEOUT = 6.0


def _timeouts(total: float) -> "httpx.Timeout":
    return httpx.Timeout(total, connect=_CONNECT_TIMEOUT)


# 本轮 dump 是否已经确认不可用。**同一轮 daily_update 里 dump 有三个调用点**
# （K线主步 / 全库历史补全 / 掉出池补结算），第一个失败之后另外两个还会各自再试一遍、
# 每次内含 2 次重试——对着一个已知不通的地址反复重连，纯浪费。2026-08-28 fuyao 整个
# 网络不可达那次，光这个就白等了几分钟，而那一跑本来就因为退回逐股拉取慢到 885 秒。
#
# 用模块级标记而不是把状态一路传参：三个调用点分布在两个模块、调用链很深，穿参数
# 会污染一串签名；而这个标记的生命周期恰好就是"一轮更新"，由 run_daily_update
# 开头 reset 一次，语义清楚。
_RUN_UNAVAILABLE: dict = {"why": None}


def reset_dump_availability() -> None:
    """每轮 daily_update 开头调一次，清掉上一轮的熔断标记。"""
    _RUN_UNAVAILABLE["why"] = None


def dump_unavailable_reason() -> Optional[str]:
    return _RUN_UNAVAILABLE["why"]


def dump_last_access() -> dict:
    """上次取 dump 的方式：covered=缓存已覆盖所需交易日；unchanged=上游没变；
    downloaded=真下载了一次。给日志用。"""
    return dict(_LAST_ACCESS)


def _mark(mode: str, **kw) -> None:
    _LAST_ACCESS.update(mode=mode, at=datetime.now(_SH_TZ).isoformat(timespec="seconds"), **kw)


def _data_path(kind: str) -> Path:
    return _CACHE_DIR / f"{kind}.parquet"


def _meta_path(kind: str) -> Path:
    return _CACHE_DIR / f"{kind}.meta.json"


def _read_meta(kind: str) -> Optional[dict]:
    try:
        return json.loads(_meta_path(kind).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def dump_max_date(path: Path) -> Optional[date]:
    """
    只读 date_ms 一列，取文件里最新的交易日。

    这是缓存能不能复用的唯一判据——**不看路径里的日期**。路径日期是"生成日"，
    2026-08-26 那份 dump 在 17:34 含当日数据，但同一路径的早盘版本可能只到 08-25，
    仓库为"返回了但缺那一天"这类问题付过的代价已经够多了。
    """
    try:
        import pyarrow.parquet as pq
        t = pq.read_table(path, columns=["date_ms"])
        vals = t.column("date_ms").to_pylist()
        return _ms_to_date(max(vals)) if vals else None
    except Exception:  # noqa: BLE001
        return None


def _remote_size(url: str, timeout: int = 20) -> Optional[int]:
    """
    只下 1 个字节问出文件总长（Content-Range: bytes 0-0/1077889）。

    预签名链接一般只授权 GET，HEAD 会被拒，所以用 Range 请求代替。
    拿不到返回 None，调用方按"判断不了 ⇒ 重新下载"处理，不猜。
    """
    try:
        with httpx.Client(timeout=_timeouts(timeout), follow_redirects=True) as c:
            resp = c.get(url, headers={"Range": "bytes=0-0"})
        cr = resp.headers.get("content-range") or ""
        if "/" in cr:
            total = cr.rsplit("/", 1)[1].strip()
            return int(total) if total.isdigit() else None
    except Exception:  # noqa: BLE001
        return None
    return None


def _download_once(url: str, dest: Path, timeout: int) -> int:
    """
    下载一次并**校验完整性**。返回写入字节数；不完整则抛异常。

    校验是必需的：S3 会在传输中途断开（2026-08-26 生产实测 "peer closed connection
    without sending complete message body (received 360820 bytes, expected 1077889)"）。
    httpx 这种情况会抛 RemoteProtocolError，但**不是每次都抛**——短读也可能安静
    结束，那样落地的就是一个截断的 Parquet，pyarrow 解析时才炸，或者更糟：
    解析出一部分数据，我们拿半份行情去算涨停。所以字节数必须自己对。
    """
    written = 0
    expected = None
    with httpx.Client(timeout=_timeouts(timeout), follow_redirects=True) as c:
        with c.stream("GET", url) as resp:
            resp.raise_for_status()
            cl = resp.headers.get("content-length")
            expected = int(cl) if cl and cl.isdigit() else None
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
                    written += len(chunk)
    if expected is not None and written != expected:
        raise FuyaoError(f"下载不完整：收到 {written} 字节，应为 {expected}")
    if written < 1024:
        raise FuyaoError(f"下载内容过小（{written} 字节），不像是 Parquet")
    return written


def _path_date(url: str) -> Optional[str]:
    m = re.search(r"/releases/(\d{8})/", url)
    return m.group(1) if m else None


@contextmanager
def daily_k_dump(api_key: str, kind: str = DUMP_KIND_10D,
                 require_date: Optional[date] = None,
                 timeout: int = 180, retries: int = 2) -> Iterator[Optional[Path]]:
    """
    取 dump 文件路径，**带本地缓存**：一天只需要真正下载成功一次。

    require_date 传入要用的交易日；缓存已经覆盖它就零请求直接复用。不传则每次都
    去问一下上游有没有变（仍然可能命中缓存，只花 1 个字节）。

    失败抛 FuyaoError，不返回 None——K线还有逐股接口那条完整的老路，调用方兜住即可。
    **但调用方那边的 except 分支必须真的能跑通**：2026-08-26 生产事故就是 except 里
    调了个不存在的 log.warning()，错误处理器自己抛 AttributeError，把可兜底的下载
    中断升级成了整个步骤失败。

    下载走临时文件 + 原子替换：一次失败的下载绝不能毁掉手上那份好的缓存。
    """
    # 本轮已经确认不可用 → 直接抛，不再浪费时间去连（见 _RUN_UNAVAILABLE 注释）。
    # 注意仍然先检查缓存：网络不通不代表手上那份缓存不能用。
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data, meta = _data_path(kind), _read_meta(kind)
    # 重试过程必须留痕：2026-08-26 生产上「拉取K线数据」从 12.1s 跳到 56.7s，
    # 而候选数/分组/命中数一模一样——多出来的 44 秒极可能是 dump 下载被 S3 截断后
    # 重试掉的，但当时重试是静默的，日志里查不出来。诊断不了的耗时等于没有诊断。
    _t0 = time.time()
    _errs: List[str] = []

    # ① 缓存已覆盖要的交易日 → 零请求
    if data.exists() and meta:
        cached_max = meta.get("max_trade_date")
        if require_date and cached_max and cached_max >= require_date.isoformat():
            _mark("covered", attempts=0, errors=[], seconds=0.0)
            yield data
            return

    if _RUN_UNAVAILABLE["why"]:
        # 本轮已判定网络不通。有缓存就直接用旧的（理由同下面 stale 分支），
        # 没有才抛——总之不再去连。
        if data.exists() and meta:
            _mark("stale", attempts=0, errors=[], seconds=0.0)
            yield data
            return
        _mark("skipped", attempts=0, errors=[], seconds=0.0)
        raise FuyaoError(f"本轮已判定不可用，跳过（{_RUN_UNAVAILABLE['why']}）")

    last_err: Optional[str] = None
    for attempt in range(retries + 1):
        try:
            url = _download_url(api_key, kind)   # 每次重新取，预签名链接只活5分钟
            if not url:
                last_err = "接口没有返回下载链接"
            else:
                # ② 生成日和大小都没变 ⇒ 上游还是那份，重下也是白下
                size = _remote_size(url, timeout=20)
                if (data.exists() and meta and size is not None
                        and meta.get("size") == size
                        and meta.get("path_date") == _path_date(url)):
                    _mark("unchanged", attempts=attempt + 1, errors=list(_errs),
                          seconds=round(time.time() - _t0, 1))
                    yield data
                    return
                # ③ 真下载。临时文件 + 原子替换，失败不毁旧缓存
                tmp = data.with_suffix(".parquet.tmp")
                try:
                    written = _download_once(url, tmp, timeout)
                    mx = dump_max_date(tmp)
                    if mx is None:
                        raise FuyaoError("下载完成但解析不出任何交易日，文件可能损坏")
                    os.replace(tmp, data)
                    _meta_path(kind).write_text(json.dumps({
                        "path_date": _path_date(url), "size": written,
                        "max_trade_date": mx.isoformat(),
                        "fetched_at": datetime.now(_SH_TZ).isoformat(),
                    }, ensure_ascii=False), encoding="utf-8")
                finally:
                    tmp.unlink(missing_ok=True)
                _mark("downloaded", attempts=attempt + 1, errors=list(_errs),
                      seconds=round(time.time() - _t0, 1))
                yield data
                return
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {str(e)[:120]}"
            _errs.append(f"第{attempt + 1}次: {last_err}")
        if attempt < retries:
            time.sleep(1.5)
    # 连接层失败（不可达/超时/DNS）才熔断整轮；业务错误（如 key 无效、dump 还没
    # 生成）不熔断——那些是"这次拿不到"，不是"这条路不通"，下一个调用点换个
    # kind 或换个时间点可能就好了。
    if any(k in (last_err or "") for k in
           ("ConnectError", "ConnectTimeout", "ReadTimeout", "Unreachable",
            "unreachable", "NameResolution", "PoolTimeout")):
        _RUN_UNAVAILABLE["why"] = last_err

    # ── 下不到新的，就用手上这份旧的 ────────────────────────────────────────
    # 2026-08-28 生产：fuyao 整网不可达，而缓存里明明有覆盖到 08-27 的 dump。
    # 当时的逻辑是"下载失败 → 抛错"，把那份缓存一起扔了，于是 223 只全部退回
    # 逐股拉取，K线步骤从 5 秒变成 885 秒。
    #
    # 但**dump 只管历史缺口，当日那一根本来就走实时行情**——库里历史停在 08-27、
    # 缓存覆盖到 08-27，接得上，本该 218/218 命中。是我把"拿不到最新的"错当成了
    # "什么都没有"。
    #
    # 这里不判断"旧到什么程度还能用"：那个判断已经在调用方了（dump 最早一根不能
    # 晚于库里最后一根的次日、最后一根不能早于库里最后一根），接不上的股票会自己
    # 退回逐股拉。在这里再设一道"超过N天就不给"的门槛，等于把同一个判断写两遍，
    # 而且会误伤那些历史更旧、旧 dump 反而够用的股票。
    if data.exists() and meta:
        _mark("stale", attempts=retries + 1, errors=list(_errs),
              seconds=round(time.time() - _t0, 1))
        yield data
        return

    _mark("failed", attempts=retries + 1, errors=list(_errs),
          seconds=round(time.time() - _t0, 1))
    raise FuyaoError(last_err or "下载失败")


def dump_cache_info(kind: str = DUMP_KIND_10D) -> Optional[dict]:
    """给日志用：手上这份缓存是什么时候的、覆盖到哪一天。"""
    meta = _read_meta(kind)
    return meta if meta and _data_path(kind).exists() else None

def _ms_to_date(ms: int) -> date:
    """dump 的 date_ms 是 Asia/Shanghai 零点，必须按 +08 解释，别用本机时区。"""
    return datetime.fromtimestamp(ms / 1000, tz=_SH_TZ).date()


def load_bars(path: Path, wanted: Dict[str, bool]) -> Dict[str, List[KLineBar]]:
    """
    从 Parquet 里挑出我们关心的股票，构造 KLineBar。

    wanted: {6位代码: is_st}。只解析这些股票——dump 里有全市场 5,545 只，
    但我们**只更新库里已关注的**（用户明确要求：数据不能爆炸）。

    每根 bar 的涨跌幅由**相邻两天的收盘价**算出，prev_close 直接传给
    build_kline_bar：dump 给的是连续交易日，前收是精确值，比"从 pct 反推前收"
    那条老路更准。因此**最早那一根被丢掉**（它没有前一天，无法定涨跌停），
    10 天变 9 天——对"补最近几天缺口"这个用途绰绰有余。

    走 build_kline_bar 是刻意的：涨停/炸板/一字板的判定全仓库只能有一套，
    不能因为"这根bar是从dump来的"得出跟腾讯那条路不同的涨停结论。
    """
    import pyarrow.parquet as pq

    t = pq.read_table(path, columns=["thscode", "date_ms", "open_price",
                                     "high_price", "low_price", "close_price"])
    rows: Dict[str, List[tuple]] = {}
    for ths, ms, o, h, lo, cl in zip(t.column("thscode").to_pylist(),
                                     t.column("date_ms").to_pylist(),
                                     t.column("open_price").to_pylist(),
                                     t.column("high_price").to_pylist(),
                                     t.column("low_price").to_pylist(),
                                     t.column("close_price").to_pylist()):
        code = ths.split(".")[0]
        if code not in wanted or cl is None or cl <= 0:
            continue
        rows.setdefault(code, []).append((_ms_to_date(ms), o, h, lo, cl))

    out: Dict[str, List[KLineBar]] = {}
    for code, raw in rows.items():
        raw.sort(key=lambda r: r[0])
        is_st = wanted[code]
        limit_pct = get_limit_pct(code, is_st)
        bars: List[KLineBar] = []
        for i in range(1, len(raw)):          # 从第2根起，第1根没有前收
            d, o, h, lo, cl = raw[i]
            prev_close = raw[i - 1][4]
            if not prev_close or prev_close <= 0:
                continue
            bars.append(build_kline_bar(
                dt=d, open_p=o, close_p=cl, high_p=h, low_p=lo,
                pct=(cl / prev_close - 1) * 100,
                turnover=None,                # dump 不提供换手率，见模块 docstring
                is_st=is_st, limit_pct=limit_pct, prev_close=prev_close,
            ))
        if bars:
            out[code] = bars
    return out


# ── 单只历史K线：补区间涨幅 ────────────────────────────────────────────────────

def thscode_suffix(code: str) -> str:
    """
    6位代码 → fuyao 的交易所后缀。

    2026-08-27 生产上 920895 被拼成 `920895.SZ`，fuyao 回 `code=1002 Unknown thscode`。
    起因是调用方拿 Stock.market 当后缀，而那个字段只有 SH/SZ 两个值——_ensure_stock
    里 `"SH" if market == 1 else "SZ"`，北交所全被归成了 SZ。

    判定复用 eastmoney_fetcher._is_bj_code：北交所是什么代码这件事全仓库只该有
    一套判定，涨跌停用的是它（±30%），这里再写一遍迟早对不上。
    """
    if _is_bj_code(code):
        return "BJ"
    return "SH" if code.startswith("6") else "SZ"


def fetch_interval_returns(api_key: str, code: str, market_suffix: str,
                           windows=(10, 20, 60), timeout: int = 15,
                           retries: int = 1) -> dict:
    """
    取单只股票的 N 个交易日复合涨幅，返回 {10: pct, 20: pct, 60: pct}。

    **拿不到数据抛 FuyaoError，不返回 None**（2026-08-26改，我自己刚写的代码就踩了
    同一个坑）：第一版任何失败都返回 None，调用方无从区分"请求失败"和"上市不满N个
    交易日所以真的没有"。生产上 603615 茶花股份补全失败，日志只说"补到21只"，
    查了腾讯才知道它有81根完整历史——是请求挂了，不是数据不够。现在彻底分开：
      · 请求/响应层失败 → 抛 FuyaoError，调用方记日志、可重试
      · 数据不够（bar数 < 窗口+1）→ dict 里该窗口是 None，是事实不是故障

    为什么需要它：涨停板块雷达的区间涨幅列此前**只认**东财核心召回接口的
    INTERVAL_CHG，进不了那份召回名单（356只）的股票整行显示 —。今天首板、
    历史没有涨停记录的票必然进不去——金诚信、江西铜业、迪阿股份都是这种。

    为什么不用 Stock.pct_change_Nd 顶上：那个字段是**日涨幅简单相加**的近似，
    对大涨股严重低估（603580近60日：真实204.85% vs 相加123.14%，差80个百分点）。
    显示一个错40%的数比显示 — 更糟，所以当时宁可空着。现在有了精确来源才补。

    `adjust=forward` 前复权：区间收益问的是"持有这段时间赚了多少"，必须含除权
    除息调整。跟 dump 的未复权是两个问题，见模块 docstring。

    窗口按**交易日**倒数，不是自然日：请求一个足够宽的日历窗口（60交易日≈90个
    自然日，这里按最大窗口的3倍取，够覆盖春节长假），拿回来的 bar 序列自己数。
    数不够就该窗口返回 None——不用"有多少算多少"凑一个偏小的数糊弄。
    """
    max_win = max(windows)
    end = datetime.now(_SH_TZ)
    start = end - timedelta(days=max_win * 3 + 30)
    params = {"thscode": f"{code}.{market_suffix}", "interval": "1d",
              "start": int(start.timestamp() * 1000),
              "end": int(end.timestamp() * 1000), "adjust": "forward"}

    last_err = ""
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=_timeouts(timeout)) as c:
                resp = c.get(f"{FUYAO_BASE}/api/a-share/prices/historical",
                             params=params, headers={"X-api-key": api_key})
            body = resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
        else:
            if body.get("code") == 0:
                break
            last_err = f"code={body.get('code')} {body.get('message')}"
        if attempt < retries:
            time.sleep(0.5)     # 低并发场景下重试一次几乎零成本
    else:
        raise FuyaoError(last_err)

    items = ((body.get("data") or {}).get("item")) or []
    closes = [(it.get("date_ms"), it.get("close_price")) for it in items]
    closes = [(d, c) for d, c in closes if d is not None and c]
    if len(closes) < 2:
        raise FuyaoError(f"返回 {len(closes)} 根有效K线，不足以算任何窗口")
    closes.sort(key=lambda x: x[0])
    latest = closes[-1][1]

    out = {}
    for w in windows:
        # 近N个交易日涨幅 = 最新收盘 / N个交易日前的收盘 - 1
        # closes[-1] 是今天，往前数 w 根即 closes[-1-w]
        if len(closes) < w + 1:
            out[w] = None          # 上市不满N个交易日，就是没有，不凑
            continue
        base = closes[-1 - w][1]
        out[w] = round((latest / base - 1) * 100, 2) if base else None
    return out
