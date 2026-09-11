"""
日更里用 10 年存档给「库里历史不足」的候选票补历史（2026-09-11，用户要求）。

补够 60 根的就走 DB 重建，不用逐股拉 65 天。分组判定只有 _build_klines_from_db 一套。
"""
import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import StockBasicInfo
from app.services.fuyao_dump import thscode_suffix

_SPEC = importlib.util.spec_from_file_location(
    "_du_prefill", Path(__file__).resolve().parents[1] / "scripts" / "daily_update.py")
du = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(du)

SH = timezone(timedelta(hours=8))
T = date(2026, 9, 11)
DAYS = [date(2026, 5, 1) + timedelta(days=i) for i in range(120)]   # 到 08-28，当连续交易日用


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass


def _archive(path, codes, days=DAYS):
    rows = [(f"{c}.{thscode_suffix(c)}", d, 10.0 + i * 0.01)
            for c in sorted(codes, key=lambda c: f"{c}.{thscode_suffix(c)}")
            for i, d in enumerate(days)]
    ms = [int(datetime(d.year, d.month, d.day, tzinfo=SH).timestamp() * 1000) for _, d, _ in rows]
    pq.write_table(pa.table({
        "thscode": [r[0] for r in rows], "date_ms": ms,
        "open_price": [r[2] for r in rows], "high_price": [r[2] for r in rows],
        "low_price": [r[2] for r in rows], "close_price": [r[2] for r in rows],
        "volume": [1e6] * len(rows), "turnover": [1e7] * len(rows)}), path)
    return path


def _info(code):
    return StockBasicInfo(code=code, name=code, market=1 if code.startswith("6") else 0,
                          is_st=False, pct_change=0.0, turnover_rate=0.0)


def _stub(db, code):
    """第 1 步「确定候选股」给新票建的存根：有 stock_id，一条快照都没有。"""
    st = Stock(code=code, name=code, market="SH" if code.startswith("6") else "SZ")
    db.add(st); db.flush()
    return st


def test_补完历史的新票重新分组后走DB重建(db, tmp_path):
    _stub(db, "600984")
    p = _archive(tmp_path / "daily-k.parquet", ["600984"])
    infos = [_info("600984")]
    _, db_group, full_group = du._build_klines_from_db(infos, db, T)
    assert [i.code for i in full_group] == ["600984"], "前提：一条历史都没有，本来要逐股拉"

    added = du._prefill_history_from_archive(db, full_group, T, _Log(), archive_path=p)
    assert added == 65, "跟逐股全量拉的窗口一样长"
    _, db_group, full_group = du._build_klines_from_db(infos, db, T)
    assert [i.code for i in db_group] == ["600984"] and full_group == []
    rows = db.query(StockDailySnapshot).all()
    assert all(r.is_settled for r in rows) and all(r.date < T for r in rows)


def test_同一份存档每只票只试一次(db, tmp_path, monkeypatch):
    """存档里没有的新股、历史不够的次新股，再读一遍也是白花 2 秒 + 121MB。"""
    _stub(db, "600984")
    _stub(db, "688999")                                  # 存档里没有：存档生成后才上市
    p = _archive(tmp_path / "daily-k.parquet", ["600984"])
    reads, orig = [], du.read_archive_bars
    monkeypatch.setattr(du, "read_archive_bars", lambda *a, **k: reads.append(1) or orig(*a, **k))
    du._prefill_history_from_archive(db, [_info("688999")], T, _Log(), archive_path=p)
    du._prefill_history_from_archive(db, [_info("688999")], T, _Log(), archive_path=p)
    assert len(reads) == 1, "第二跑不该再读"


def test_换了存档就重新试(db, tmp_path, monkeypatch):
    _stub(db, "688999")
    p = _archive(tmp_path / "daily-k.parquet", ["600984"])
    du._prefill_history_from_archive(db, [_info("688999")], T, _Log(), archive_path=p)
    _archive(p, ["600984", "688999"], DAYS + [DAYS[-1] + timedelta(days=1)])   # refresh：覆盖日期变了
    added = du._prefill_history_from_archive(db, [_info("688999")], T, _Log(), archive_path=p)
    assert added == 65


def test_没有存档就什么都不做(db, tmp_path):
    _stub(db, "600984")
    assert du._prefill_history_from_archive(db, [_info("600984")], T, _Log(),
                                            archive_path=tmp_path / "不存在.parquet") == 0
    assert db.query(StockDailySnapshot).count() == 0
