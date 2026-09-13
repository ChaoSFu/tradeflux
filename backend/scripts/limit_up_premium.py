"""
股性 · 涨停次日溢价：每只票「有记录的每次涨停，次一交易日的平均涨跌幅」，算一遍写回 Stock，
并打印分布。日更收盘那一跑会自动算；这个命令用来上线后先补一次，或者核对。

    cd /opt/code/tradeflux/backend
    .venv/bin/python -m scripts.limit_up_premium            # 算 + 写回
    .venv/bin/python -m scripts.limit_up_premium --dry-run  # 只看分布，不写库

口径见 app/services/limit_up_premium_service.py。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.stock import Stock
from app.services.limit_up_premium_service import (
    compute_limit_up_premium, refresh_limit_up_premium,
)
from app.services.trading_calendar import get_trading_days


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="只算、只打印，不写库")
    a = ap.parse_args()

    db = SessionLocal()
    try:
        cal = get_trading_days(db) or None
        r = (compute_limit_up_premium(db, cal=cal) if a.dry_run
             else refresh_limit_up_premium(db, cal=cal))
        if r["as_of"] is None:
            print("库里没有日快照")
            return 1
        by = r["by_stock"]
        print(f"截至 {r['as_of']}（日历：{'交易日历' if r['calendar'] == 'trading_calendar' else '快照日期凑的'}）")
        print(f"有样本的股票 {len(by)} 只，样本共 {r['samples']} 次｜"
              f"次日停牌或缺快照没算 {r['skipped_gap']} 次｜次日还没收盘没算 {r['skipped_live']} 次")
        for k in (1, 3, 5, 10):
            print(f"  样本 ≥{k} 次：{sum(1 for _, n in by.values() if n >= k)} 只")
        solid = sorted(((avg, n, sid) for sid, (avg, n) in by.items() if n >= 3), reverse=True)
        if solid:
            vals = sorted(v for v, _, _ in solid)
            q = lambda p: vals[min(len(vals) - 1, int(len(vals) * p))]  # noqa: E731
            print(f"  样本 ≥3 次的平均溢价分布：p10 {_pct(q(.1))}  p50 {_pct(q(.5))}  p90 {_pct(q(.9))}")
            names = dict((i, f"{c} {n}") for i, c, n in db.query(Stock.id, Stock.code, Stock.name)
                         .filter(Stock.id.in_([sid for _, _, sid in solid[:10] + solid[-10:]])))
            print("  溢价最高（样本 ≥3 次）：" + "；".join(
                f"{names.get(sid, sid)} {_pct(avg)}（{n} 次）" for avg, n, sid in solid[:10]))
            print("  溢价最低（样本 ≥3 次）：" + "；".join(
                f"{names.get(sid, sid)} {_pct(avg)}（{n} 次）" for avg, n, sid in solid[-10:][::-1]))
        if a.dry_run:
            print("--dry-run：未写库")
        else:
            print(f"已写回 Stock：{r['updated']} 只的值有变化")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
