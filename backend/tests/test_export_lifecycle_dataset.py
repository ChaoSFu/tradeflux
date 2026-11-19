"""导出生命周期数据集（scripts/export_lifecycle_dataset.py）的未来标签与小工具。"""
from datetime import date

from scripts.export_lifecycle_dataset import (
    MARKET_COLUMNS, STOCK_COLUMNS, _fmt, _pct, board_of, effective_state, forward_labels,
)

D = [date(2026, 11, d) for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 16)]   # D[0] 是当天


def _bar(c, o=None, h=None, lo=None, pct=None, **kw):
    return {"open": o if o is not None else c, "high": h if h is not None else c,
            "low": lo if lo is not None else c, "close": c, "pct": pct, **kw}


def _series(closes):
    bars, prev = {}, 10.0
    for d, c in zip(D[1:], closes):
        bars[d] = _bar(c, pct=round((c / prev - 1) * 100, 4))
        prev = c
    return bars


def test_收益按交易日数_从当天收盘起算():
    bars = _series([11, 12, 9, 10, 10.5, 10, 10, 10, 10, 13])
    out = forward_labels(bars, 10.0, D[1:])
    assert out["fut_next_date"] == D[1]
    assert out["fut_ret_1d_pct"] == 10.0
    assert out["fut_ret_3d_pct"] == -10.0
    assert out["fut_ret_5d_pct"] == 5.0
    assert out["fut_ret_10d_pct"] == 30.0
    assert out["fut_max_high_5d_pct"] == 20.0 and out["fut_min_low_5d_pct"] == -10.0
    assert out["fut_adj_suspect"] is False


def test_次日开盘买_用次日开盘价做成本():
    bars = _series([11, 12, 9, 10, 10.5, 10, 10, 10, 10, 13])
    bars[D[1]]["open"] = 10.5
    out = forward_labels(bars, 10.0, D[1:])
    assert out["fut_next_open_gap_pct"] == 5.0
    assert out["fut_ret_open_5d_pct"] == 0.0          # 10.5 → 10.5


def test_第N天没行情就留空_不往前找一根顶替():
    bars = _series([11, 12, 9, 10, 10.5, 10, 10, 10, 10, 13])
    del bars[D[5]]                                      # 第 5 天停牌
    out = forward_labels(bars, 10.0, D[1:])
    assert out["fut_ret_5d_pct"] is None
    assert out["fut_max_high_5d_pct"] is None          # 少一天的极值不是极值
    assert out["fut_ret_3d_pct"] == -10.0


def test_日历还没走到第N天就留空():
    bars = _series([11, 12, 9])
    out = forward_labels(bars, 10.0, D[1:4])
    assert out["fut_ret_3d_pct"] == -10.0
    assert out["fut_ret_5d_pct"] is None and out["fut_ret_10d_pct"] is None


def test_收盘价比值跟涨跌幅对不上_标疑似除权():
    bars = _series([11, 12, 9, 10, 10.5, 10, 10, 10, 10, 13])
    bars[D[3]]["pct"] = 1.0                             # 交易所说涨 1%，收盘价比值却是 -25%
    assert forward_labels(bars, 10.0, D[1:])["fut_adj_suspect"] is True


def test_次日一字涨停买不进():
    bars = _series([11, 12])
    bars[D[1]].update(one_up=True, lu=True)
    out = forward_labels(bars, 10.0, D[1:])
    assert out["fut_next_one_word_limit_up"] is True and out["fut_next_limit_up"] is True


def test_没有收盘价就全部留空():
    out = forward_labels(_series([11, 12]), None, D[1:])
    assert out["fut_ret_1d_pct"] is None and out["fut_next_open_gap_pct"] is None


def test_小工具():
    assert board_of("688001") == "科创板" and board_of("300001") == "创业板" and board_of("603381") == "主板"
    assert _pct(0, 1) is None and _pct(None, 1) is None and _pct(10, 11) == 10.0
    assert effective_state({"lifecycle_state": "UNKNOWN", "last_valid_state": "CROSS_WEAKENING"}) == "CROSS_WEAKENING"
    assert effective_state({"lifecycle_state": "REPAIRING", "last_valid_state": "BROKEN"}) == "REPAIRING"
    assert _fmt(True) == 1 and _fmt(None) == "" and _fmt(["A", "B"]) == "A|B" and _fmt(1.234567) == 1.2346


def test_列名不重复():
    for cols in (STOCK_COLUMNS, MARKET_COLUMNS):
        names = [c for c, _ in cols]
        assert len(names) == len(set(names))
