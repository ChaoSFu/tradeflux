"""
板块趋势 · 主升板块雷达的命令行。只读：不写库、不向外部请求。

    cd /opt/code/tradeflux/backend
    .venv/bin/python -m scripts.sector_mainline                  # 最新状态：分布 + 各状态名单
    .venv/bin/python -m scripts.sector_mainline --date 2026-09-11
    .venv/bin/python -m scripts.sector_mainline --code BK0459    # 一个板块近 10 天每天的四道闸
    .venv/bin/python -m scripts.sector_mainline --days 10        # 最近 10 个交易日每天的状态分布

阈值是用板块指数日线量出来的（见 docs/SECTOR_MAINLINE.md），生态那部分只有生产上的
成分股快照量得了——上线后跑一次 --days 看分布：主升 + 加速每天应该是个位数到十几个。
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.sector import Sector
from app.services import sector_mainline_service as svc

MARK = {"PASS": "✓", "WARN": "△", "FAIL": "✗", "UNKNOWN": "?"}
GATE_ZH = {"trend": "趋势", "rs": "强度", "ecology": "生态", "risk": "风险"}


def _gates(g: dict) -> str:
    return " ".join(f"{GATE_ZH[k]}{MARK[v['status']]}" for k, v in g.items())


def _n(v, fmt="{:+.1f}") -> str:
    return "—" if v is None else fmt.format(v)


def show_latest(db, as_of) -> int:
    r = svc.get_sector_mainline_state(db, as_of=as_of)
    print(f"状态基准：{r['state_date']} 收盘（{r['version']}）")
    for note in r["notes"]:
        print("  ·", note)
    print("  " + "  ".join(f"{svc.STATE_LABELS[s]} {r['counts'][s]}" for s in svc.STATE_ORDER))
    for s in r["sectors"]:
        if s["state"] in (svc.NONE, svc.UNKNOWN):
            continue
        f = s["facts"]
        lu = "/".join("?" if v is None else str(v) for v in f["lu_series"][-3:])
        print(f"  {s['state_label']:<2} {s['code']} {s['name']:<10} {_gates(s['gates'])}  "
              f"RS10 {_n(f['rs10'], '{:+.2f}')}  5日 {_n(f['r5'])}%  偏离 {_n(f['dev20'])}%  "
              f"涨停 {lu}（{svc.LU_TREND_LABELS[f['lu_trend']]}）  最高 {_n(f['height_3d'], '{}')} 板")
        print(f"       {s['state_reason']}")
    unknown = [s for s in r["sectors"] if s["state"] == svc.UNKNOWN]
    if unknown:
        print(f"  未知 {len(unknown)} 个，例如：" + "；".join(
            f"{s['name']}（{s['state_reason']}）" for s in unknown[:3]))
    return 0


def show_code(db, code: str, as_of) -> int:
    r = svc.get_sector_mainline_detail(db, code, as_of=as_of)
    if r is None:
        print(f"{code} 不是关注板块，或者还没有可用的状态基准日")
        return 1
    s = r["sector"]
    print(f"{s['code']} {s['name']}（状态基准 {r['state_date']}）")
    for h in r["history"]:
        print(f"  {h['date']} {h['state_label']:<2} {_gates(h['gates'])}  {h['reason']}")
    print("  今天四道闸：")
    for g, v in s["gates"].items():
        print(f"    {GATE_ZH[g]} {v['status']:<7} {v['reason']}")
    for e in s["evidence"]:
        print("  证据：", e)
    return 0


def show_days(db, n: int) -> int:
    codes = [c for (c,) in db.query(Sector.code).filter(Sector.is_watched.is_(True))]
    cal = svc.trading_calendar(db, codes, date.today())
    for d in cal[-n:]:
        r = svc.get_sector_mainline_state(db, as_of=d)
        main = [s["name"] for s in r["sectors"] if s["state"] in (svc.ACCELERATION, svc.MAIN_RISE)]
        dist = " ".join(f"{svc.STATE_LABELS[s]}{r['counts'][s]}" for s in svc.STATE_ORDER)
        print(f"{d}  {dist}  主线：{'、'.join(main[:10]) or '—'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=date.fromisoformat, help="状态基准日上限（默认最近一个已收盘交易日）")
    ap.add_argument("--code", help="只看一个板块，例如 BK0459")
    ap.add_argument("--days", type=int, help="最近 N 个交易日每天的状态分布")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        if a.code:
            return show_code(db, a.code, a.date)
        if a.days:
            return show_days(db, a.days)
        return show_latest(db, a.date)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
