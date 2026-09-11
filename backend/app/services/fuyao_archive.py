"""
fuyao 10 年全量日K（`daily-k`）存档（2026-09-11 新增）。

用户提议：下一份全量 dump **长期保存**，需要重建 K 线时先读它；日更照旧用 10 日
dump 和实时接口补最近几天。

## 服务器实测（2026-09-11，scripts/probe_full_dump.py）

· 172MB，下载 341 秒（0.5MB/s），一轮下完，峰值内存 137MB
· 10,275,240 行，2016-09-12 ~ 2026-09-10，5,560 只票，每只中位 2,281 行
· **只有 2 个 row group**（parquet-mr / Spark 写的），最大一块 763 万行；
  按 (代码, 日期) 全局有序
· 流式扫完一遍：**2 秒，RSS 比开始多 121MB**

## 所以只有一种读法

逐页流式读、逐批筛：`ParquetFile(buffer_size=8MB, pre_buffer=False)` +
`iter_batches(use_threads=False)`。**不能 read_table，也不能用 dataset.to_table 的
默认参数**——一块 763 万行，整块解码的上界约 681MB，这台机器可用内存只有 710MB。

读一只票和读两千只票代价差不多（都得扫到那几块，最多 2 秒），所以调用方应该
**一次把要的票都读出来**，别按票循环调用。

## 不负责的事

存档只到它生成的那天。之后几天的缺口归 10 日 dump，当日那一根归实时行情——
跟现在的日更分工一样，这里不重新发明。
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from .eastmoney_fetcher import KLineBar
from .fuyao_dump import _ms_to_date, rows_to_bars, thscode_suffix

KIND_FULL = "daily-k"
ARCHIVE_DIR = Path(__file__).resolve().parents[2] / "data" / "fuyao"
ARCHIVE_PATH = ARCHIVE_DIR / f"{KIND_FULL}.parquet"
META_PATH = ARCHIVE_DIR / f"{KIND_FULL}.meta.json"
_SH_TZ = timezone(timedelta(hours=8))
_BASE_COLS = ["thscode", "date_ms", "open_price", "high_price", "low_price", "close_price"]


def _stat(v):
    """footer 统计值：字符串列有时给 bytes，统一成 str 才能跟代码比较。"""
    return v.decode() if isinstance(v, bytes) else v


def archive_max_date(path: Path = ARCHIVE_PATH) -> Optional[date]:
    """
    存档覆盖到哪一天。**只读 footer 的统计信息，不读数据**。

    不能用 fuyao_dump.dump_max_date：它是 `read_table(["date_ms"]).to_pylist()`，
    在 1027 万行上要吃掉约 470MB。有一块没有统计信息就返回 None——说不准就不猜。
    """
    import pyarrow.parquet as pq
    try:
        pf = pq.ParquetFile(path)
    except Exception:  # noqa: BLE001
        return None
    idx = pf.schema_arrow.get_field_index("date_ms")
    best = None
    for i in range(pf.metadata.num_row_groups):
        st = pf.metadata.row_group(i).column(idx).statistics
        if st is None or not st.has_min_max:
            return None
        best = st.max if best is None else max(best, st.max)
    return _ms_to_date(best) if best is not None else None


def read_archive_rows(codes: Set[str], since: date,
                      path: Path = ARCHIVE_PATH) -> Dict[str, List[tuple]]:
    """
    从存档里流式筛出 codes 这些票、since（含）之后的原始行：
    {6位代码: [(date, open, high, low, close, volume, amount), ...]}，按日期升序。

    返回原始行而不是 KLineBar，是给**一次要读几千只票**的调用方（存档补洞）用的：
    元组几十 MB，全转成 KLineBar 要大好几倍，那种调用方应该分批转换。
    少量的票直接用 read_archive_bars。

    文件不存在 / 读不了 → 抛异常。存档是加速手段不是依赖，由调用方决定怎么退。
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if not codes:
        return {}
    pf = pq.ParquetFile(path, buffer_size=8 << 20, pre_buffer=False)
    names = pf.schema_arrow.names
    has_vol, has_amt = "volume" in names, "turnover" in names
    cols = _BASE_COLS + [c for c, ok in (("volume", has_vol), ("turnover", has_amt)) if ok]
    ths = sorted(f"{c}.{thscode_suffix(c)}" for c in codes)
    value_set = pa.array(ths)
    since_ms = int(datetime(since.year, since.month, since.day, tzinfo=_SH_TZ).timestamp() * 1000)
    ci = pf.schema_arrow.get_field_index("thscode")

    rows: Dict[str, List[tuple]] = {}
    for rgi in range(pf.metadata.num_row_groups):
        st = pf.metadata.row_group(rgi).column(ci).statistics
        # 文件按代码分块：这一块的代码区间跟要的完全不相交，就整块跳过
        if st is not None and st.has_min_max and (
                _stat(st.max) < ths[0] or _stat(st.min) > ths[-1]):
            continue
        for b in pf.iter_batches(batch_size=65536, columns=cols, row_groups=[rgi],
                                 use_threads=False):
            fb = b.filter(pc.and_(pc.is_in(b.column("thscode"), value_set=value_set),
                                  pc.greater_equal(b.column("date_ms"), since_ms)))
            n = fb.num_rows
            if not n:
                continue
            vol = fb.column("volume").to_pylist() if has_vol else [None] * n
            amt = fb.column("turnover").to_pylist() if has_amt else [None] * n
            for t, ms, o, h, lo, cl, v, a in zip(
                    fb.column("thscode").to_pylist(), fb.column("date_ms").to_pylist(),
                    fb.column("open_price").to_pylist(), fb.column("high_price").to_pylist(),
                    fb.column("low_price").to_pylist(), fb.column("close_price").to_pylist(),
                    vol, amt):
                if cl is None or cl <= 0:
                    continue
                rows.setdefault(t.split(".")[0], []).append((_ms_to_date(ms), o, h, lo, cl, v, a))
    for r in rows.values():
        r.sort(key=lambda x: x[0])
    return rows


def read_archive_bars(wanted: Dict[str, bool], since: date,
                      path: Path = ARCHIVE_PATH) -> Dict[str, List[KLineBar]]:
    """
    少量的票：直接给 KLineBar。转换走 fuyao_dump.rows_to_bars——**跟 10 日 dump 同一个
    函数**，涨跌停判定、前收、量额来源标记全都一样。

    since 那天的第一根会被丢掉（筛选窗口里它没有前一根，定不了涨跌停）。要 N 根完整
    的，since 就往前多放几天。
    """
    return rows_to_bars(read_archive_rows(set(wanted), since, path), wanted)
