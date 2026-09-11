"""
10 年全量存档的读取（2026-09-11）。

真文件的形态（服务器实测）：按 (代码, 日期) 全局有序、只有 2 个 row group、按代码
分块。测试文件照这个形态造。
"""
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services.fuyao_archive import archive_max_date, read_archive_bars, read_archive_rows
from app.services.fuyao_dump import load_bars

SH = timezone(timedelta(hours=8))
D = [date(2026, 9, 1) + timedelta(days=i) for i in range(10)]   # 测试里当连续交易日用


def _ms(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=SH).timestamp() * 1000)


def _table(rows):
    """rows: [(thscode, date, open, high, low, close, volume)]"""
    return pa.table({
        "thscode": [r[0] for r in rows], "date_ms": [_ms(r[1]) for r in rows],
        "open_price": [r[2] for r in rows], "high_price": [r[3] for r in rows],
        "low_price": [r[4] for r in rows], "close_price": [r[5] for r in rows],
        "volume": [r[6] for r in rows], "turnover": [r[6] * r[5] for r in rows],
    })


def _archive(tmp_path, rows, split=None):
    """按 (代码, 日期) 排序后写成 2 个 row group，跟真存档一个形态。"""
    rows = sorted(rows, key=lambda r: (r[0], r[1]))
    tbl = _table(rows)
    split = len(rows) // 2 if split is None else split
    p = tmp_path / "daily-k.parquet"
    with pq.ParquetWriter(p, tbl.schema) as w:
        w.write_table(tbl.slice(0, split), row_group_size=max(split, 1))
        w.write_table(tbl.slice(split), row_group_size=max(len(rows) - split, 1))
    return p


def _flat(code, closes, days=D):
    return [(code, d, c, c, c, c, 1e6) for d, c in zip(days, closes)]


def test_只读要的票和since之后_第一根丢掉(tmp_path):
    p = _archive(tmp_path, _flat("600984.SH", [10 + i * 0.1 for i in range(10)])
                 + _flat("000001.SZ", [5.0] * 10))
    bars = read_archive_bars({"600984": False}, D[3], p)
    assert list(bars) == ["600984"], "没要的票不能读进来"
    assert [b.date for b in bars["600984"]] == D[4:], "since 那天是第一行，没有前收，要丢掉"


def test_跟10日dump走同一套转换(tmp_path):
    """同样的原始行，从 10 日 dump 读和从存档读，得到的 K 线必须一模一样。"""
    rows = _flat("600984.SH", [4.49, 4.94, 4.66, 5.13, 5.64])
    p10 = tmp_path / "d10.parquet"
    pq.write_table(_table(rows), p10)
    a = load_bars(p10, {"600984": False})["600984"]
    b = read_archive_bars({"600984": False}, D[0], _archive(tmp_path, rows))["600984"]
    key = lambda x: (x.date, x.close_price, round(x.pct_change, 6), x.is_limit_up,  # noqa: E731
                     x.is_broken_board, x.volume, x.volume_source)
    assert [key(x) for x in a] == [key(x) for x in b]


def test_停牌之后复牌_前收取停牌前最后一个收盘价(tmp_path):
    """A 股涨跌停价按上一个成交日的收盘价算，中间停牌几天不影响。"""
    days = [D[0], D[1], D[6]]                       # D2~D5 停牌，没有行
    rows = [("600984.SH", days[0], 9.5, 9.5, 9.5, 9.5, 1e6),
            ("600984.SH", days[1], 10.0, 10.0, 10.0, 10.0, 1e6),
            ("600984.SH", days[2], 11.0, 11.0, 11.0, 11.0, 1e6)]
    bar = read_archive_bars({"600984": False}, D[0], _archive(tmp_path, rows))["600984"][-1]
    assert bar.date == D[6]
    assert bar.pct_change == pytest.approx(10.0)
    assert bar.is_limit_up and bar.is_one_word_limit_up


def test_按代码分块时跳过不相交的块(tmp_path, monkeypatch):
    """真存档按代码分成两大块；要的票只在后一块，前一块不该解码。"""
    import pyarrow.parquet as pqm
    rows = (_flat("000001.SZ", [5.0] * 5, D[:5]) + _flat("300750.SZ", [9.0] * 5, D[:5])
            + _flat("600519.SH", [7.0] * 5, D[:5]))
    p = _archive(tmp_path, rows, split=5)           # 第 0 块只有 000001，第 1 块是另两只
    seen, orig = [], pqm.ParquetFile.iter_batches

    def spy(self, *a, **k):
        seen.append(tuple(k.get("row_groups") or ()))
        return orig(self, *a, **k)
    monkeypatch.setattr(pqm.ParquetFile, "iter_batches", spy)
    got = read_archive_rows({"600519"}, D[0], p)
    assert seen == [(1,)], "第 0 块的代码区间跟 600519 不相交，不该读"
    assert [r[0] for r in got["600519"]] == D[:5]


def test_无效收盘价整行跳过(tmp_path):
    rows = _flat("600984.SH", [10.0, 10.0, 0.0, 10.0, 10.0], D[:5])
    got = read_archive_rows({"600984"}, D[0], _archive(tmp_path, rows))
    assert D[2] not in [r[0] for r in got["600984"]]


def test_空集合直接返回空(tmp_path):
    assert read_archive_rows(set(), D[0], tmp_path / "不存在.parquet") == {}


def test_存档日期只读footer(tmp_path):
    p = _archive(tmp_path, _flat("600984.SH", [10.0] * 10))
    assert archive_max_date(p) == D[-1]
    assert archive_max_date(tmp_path / "不存在.parquet") is None
