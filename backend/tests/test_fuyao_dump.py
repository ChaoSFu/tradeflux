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
