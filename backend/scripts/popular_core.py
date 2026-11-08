"""
人气核心池（每日复盘 V2）的命令行。平时不用手动跑：大市值快照写好后会自动冻结当天的池子。

    cd /opt/code/tradeflux/backend
    .venv/bin/python -m scripts.popular_core audit                 # Phase 0：各召回来源的覆盖情况（只读）
    .venv/bin/python -m scripts.popular_core build --date 2026-11-06            # 现算看看，不写库
    .venv/bin/python -m scripts.popular_core build --date 2026-11-06 --apply    # 冻结这一天
"""
import argparse
import os
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.models.stock import Stock, StockDailySnapshot  # noqa: E402
from app.services import popular_core_service as pc  # noqa: E402
from app.services.trading_calendar import get_trading_days  # noqa: E402


def audit(db, days: int) -> None:
    from app.models.large_cap import LargeCapSyncBatch
    from app.models.leader_cycle import LeaderCycleSnapshot
    from app.models.limit_up_detail import LimitUpDailyDetail
    from app.models.turnover_pool import TurnoverPoolDaily
    cal = get_trading_days(db) or []
    recent = cal[-days:]
    fm = {d for (d,) in db.query(LargeCapSyncBatch.trade_date).filter(
        LargeCapSyncBatch.applied.is_(True), LargeCapSyncBatch.source == "eastmoney_clist")}
    snap = dict(db.query(StockDailySnapshot.date, func.count()).filter(StockDailySnapshot.date.in_(recent))
                .group_by(StockDailySnapshot.date))
    unsettled = dict(db.query(StockDailySnapshot.date, func.count()).filter(
        StockDailySnapshot.date.in_(recent), StockDailySnapshot.is_settled.is_(False)).group_by(StockDailySnapshot.date))
    lc = dict(db.query(LeaderCycleSnapshot.date, func.count()).filter(LeaderCycleSnapshot.date.in_(recent))
              .group_by(LeaderCycleSnapshot.date))
    lu = dict(db.query(LimitUpDailyDetail.trade_date, func.count()).filter(LimitUpDailyDetail.trade_date.in_(recent))
              .group_by(LimitUpDailyDetail.trade_date))
    tv = dict(db.query(TurnoverPoolDaily.date, func.count()).filter(TurnoverPoolDaily.date.in_(recent))
              .group_by(TurnoverPoolDaily.date))
    odd = [d for (d,) in db.query(TurnoverPoolDaily.date).distinct() if cal and d not in set(cal) and d >= cal[0]]
    print(f"近 {len(recent)} 个交易日         全市场快照  股票日快照(未结算)  生命周期  涨停明细  成交额前列")
    for d in recent:
        print(f"  {d}   {'有' if d in fm else '— '}        {snap.get(d, 0):>5} ({unsettled.get(d, 0)})"
              f"          {lc.get(d, 0):>4}      {lu.get(d, 0):>4}      {tv.get(d, 0):>4}")
    if odd:
        print(f"  ⚠️ 成交额前列名单里有非交易日的日期：{', '.join(map(str, sorted(odd)[-5:]))}")
    samples = Counter(min(s, 10) for (s,) in db.query(Stock.limit_up_next_samples).filter(Stock.limit_up_next_samples > 0))
    print("涨停次日均的样本数分布（10=10次及以上）：", dict(sorted(samples.items())))
    by_board = Counter(pc.board_of(c) for (c,) in db.query(Stock.code).join(
        StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id).filter(StockDailySnapshot.date == recent[-1]))
    print(f"{recent[-1]} 已跟踪股票按板块：", dict(by_board))


def main() -> int:
    ap = argparse.ArgumentParser(description="人气核心池")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--days", type=int, default=10)
    b = sub.add_parser("build")
    b.add_argument("--date", required=True)
    b.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    init_db()
    db = SessionLocal()
    try:
        if args.cmd == "audit":
            audit(db, args.days)
            return 0
        d = date.fromisoformat(args.date)
        cal = get_trading_days(db, need_through=d) or []
        latest = db.query(func.max(StockDailySnapshot.date)).scalar()
        if args.apply:
            st = pc.freeze(db, d, cal, latest)
            print(f"✅ {d} 冻结 {st['cores']} 只核心、{st['events']} 个活跃事件")
            sources = st["sources"]
        else:
            res = pc.compute(db, d, cal, latest)
            sources = res["sources"]
            print(f"{d} 现算：{len(res['items'])} 只核心、{len(res['events'])} 个活跃事件（没写库）")
            for i in sorted(res["items"].values(), key=lambda x: x["rank_20d"] or 99)[:10]:
                tags = "、".join(f"{pc.SOURCE_LABEL[s['source']]}{'第' + str(s['rank']) if s['rank'] else ''}"
                                for s in i["selection_sources"])
                print(f"  {i['stock_code']} {i['stock_name']} {i['board']}  {tags}  状态 {i['lifecycle_state']}")
        for k, v in sources.items():
            print(f"  来源 {pc.SOURCE_LABEL[k]:<8} {v['status']:<8} {v['n']:>3} 只  {v['scope']}  {v['note'] or ''}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
