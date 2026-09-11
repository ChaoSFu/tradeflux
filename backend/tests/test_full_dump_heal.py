"""
用 10 年存档给关注的股票补历史缺口（scripts/full_dump.py heal，2026-09-11）。
"""
import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pytest
import pyarrow.parquet as pq

from app.models.stock import Stock, StockDailySnapshot
from app.services.fuyao_dump import thscode_suffix

_SPEC = importlib.util.spec_from_file_location(
    "_full_dump", Path(__file__).resolve().parents[1] / "scripts" / "full_dump.py")
fd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fd)

SH = timezone(timedelta(hours=8))
TODAY = date(2026, 9, 11)
DAYS = [date(2026, 8, 1) + timedelta(days=i) for i in range(40)]   # 当连续交易日用


def _write_archive(path, codes):
    rows = [(f"{c}.{thscode_suffix(c)}", d, 10.0 + i * 0.01)
            for c in sorted(codes, key=lambda c: f"{c}.{thscode_suffix(c)}")
            for i, d in enumerate(DAYS)]
    ms = [int(datetime(d.year, d.month, d.day, tzinfo=SH).timestamp() * 1000) for _, d, _ in rows]
    tbl = pa.table({"thscode": [r[0] for r in rows], "date_ms": ms,
                    "open_price": [r[2] for r in rows], "high_price": [r[2] for r in rows],
                    "low_price": [r[2] for r in rows], "close_price": [r[2] for r in rows],
                    "volume": [1e6] * len(rows), "turnover": [1e7] * len(rows)})
    pq.write_table(tbl, path, row_group_size=len(DAYS))


@pytest.fixture(autouse=True)
def _effects(monkeypatch):
    """market_effect_daily 有 JSONB，SQLite 建不了——记下 heal 要重算哪些天即可。"""
    calls = []
    monkeypatch.setattr(fd, "refresh_effects", lambda db, dates: calls.append(list(dates)) or len(calls[-1]))
    return calls


def _stock(db, code):
    st = Stock(code=code, name=code, market="SH" if code.startswith("6") else "SZ")
    db.add(st); db.flush()
    return st


def test_先只统计_再写入_再跑一次是0(db, tmp_path):
    st = _stock(db, "600984")
    for d in DAYS[-5:-3]:                           # 最近 5 天里库里已有 2 天
        db.add(StockDailySnapshot(stock_id=st.id, date=d, close_price=1.0, is_settled=True))
    db.flush()
    p = tmp_path / "a.parquet"
    _write_archive(p, ["600984"])

    r = fd.heal(db, days=5, apply=False, archive_path=p, today=TODAY)
    assert r["window"][2] == 5 and r["added"] == 3
    assert db.query(StockDailySnapshot).count() == 2, "只统计不能写库"

    r = fd.heal(db, days=5, apply=True, archive_path=p, today=TODAY)
    assert r["added"] == 3 and db.query(StockDailySnapshot).count() == 5
    old = db.query(StockDailySnapshot).filter(StockDailySnapshot.date == DAYS[-5]).one()
    assert old.close_price == 1.0, "已有行不能被存档覆盖"

    assert fd.heal(db, days=5, apply=True, archive_path=p, today=TODAY)["added"] == 0, \
        "补过的不再补"


def test_只补库里关注的票(db, tmp_path):
    _stock(db, "600984")
    p = tmp_path / "a.parquet"
    _write_archive(p, ["600984", "000001"])         # 存档里有 000001，但库里没关注它
    r = fd.heal(db, days=5, apply=True, archive_path=p, today=TODAY)
    assert r["tracked"] == 1 and r["in_archive"] == 1
    assert set(r["per_stock"]) == {"600984"}


def test_没有存档就直说(db, tmp_path):
    r = fd.heal(db, days=5, archive_path=tmp_path / "不存在.parquet", today=TODAY)
    assert "没有可用存档" in r["error"]


def test_只统计时不动市场效应缓存(db, tmp_path, _effects):
    _stock(db, "600984")
    p = tmp_path / "a.parquet"
    _write_archive(p, ["600984"])
    fd.heal(db, days=5, apply=False, archive_path=p, today=TODAY)
    assert _effects == []


def test_写入后重算窗口里的每一天(db, tmp_path, _effects):
    """
    不只是新补的日子——08-14 之后那段库里早就齐了、只是缓存没动（09-10 缓存 28/48，
    库里其实 48/48），所以窗口里的每一天都要重算。
    """
    _stock(db, "600984")
    p = tmp_path / "a.parquet"
    _write_archive(p, ["600984"])
    r = fd.heal(db, days=5, apply=True, archive_path=p, today=TODAY)
    assert len(_effects) == 1
    assert _effects[0] == DAYS[-5:], "窗口 = 最近 5 个交易日，从旧到新"
    assert r["effects_refreshed"] == 5 and r["effects_error"] is None
