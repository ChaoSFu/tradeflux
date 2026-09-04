"""
逐日打印一只票的生命周期轨迹 —— 人工核对"它为什么从 A 变成 B"。

只读库里已有的 LeaderCycleSnapshot，**零外部请求**。

    python scripts/debug_lifecycle_sequence.py 603065
    python scripts/debug_lifecycle_sequence.py 603065 --from 2026-07-01

每一行都是那天 replay 出来的结果，右边给出当天用到的原始事实。状态跳变时前面标
`→`。看不懂某天为什么没转移，就看那几列均线和 close 的关系——price_v1 只用这些。

`用?` 那一列是这一行的价格事实**能不能用来推进状态**：需要 data_fresh（bar 是当日
的）且 bar_settled（是收盘终值）。盘中跑出来的行两者一真一假，不可用。
"""
import argparse
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.leader_cycle import LeaderCycleSnapshot
from app.services.leader_cycle_state_service import replay_price_lifecycle


def _f(v, w=7, d=2):
    return f"{v:>{w}.{d}f}" if isinstance(v, (int, float)) else " " * (w - 1) + "—"


def _tri(v):
    """三态：True / False / None(没有可比的历史)"""
    return {True: " 是", False: " 否", None: "  —"}[v]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("code", help="股票代码，如 603065")
    ap.add_argument("--from", dest="d_from", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--to", dest="d_to", help="截止日期 YYYY-MM-DD")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = (db.query(LeaderCycleSnapshot)
                .filter(LeaderCycleSnapshot.stock_code == args.code)
                .order_by(LeaderCycleSnapshot.date).all())
        if not rows:
            print(f"{args.code} 没有任何快照。先跑 backfill_leader_cycle_snapshots.py")
            return
        # 观测日历取**全表**的交易日，不是这只票的——「连续两日」判的是交易日相邻，
        # 而这只票某天没有行本身就是"不相邻"的证据
        cal = sorted({d for (d,) in db.query(LeaderCycleSnapshot.date).distinct()})

        lo = datetime.strptime(args.d_from, "%Y-%m-%d").date() if args.d_from else None
        hi = datetime.strptime(args.d_to, "%Y-%m-%d").date() if args.d_to else date.today()
        show = [r for r in rows if r.date <= hi and (lo is None or r.date >= lo)]
        if not show:
            print("该区间内没有快照")
            return

        print(f"{args.code}  共 {len(rows)} 行快照，显示 {len(show)} 行"
              f"（{show[0].date} ~ {show[-1].date}）\n")
        head = (f"{'日期':<11}{'用?':<4}{'收盘':>8}{'MA5':>8}{'MA10':>8}{'MA20':>8}"
                f"{'MA30':>8}{'新高':>5}{'新低':>5}  {'状态':<16}{'原因'}")
        print(head)
        print("-" * 118)

        prev_state = None
        for r in show:
            # **只喂 <= 当天的行**——这就是 look-ahead guard 本身
            st = replay_price_lifecycle(rows, r.date, trading_days=cal)
            usable = "✓" if (r.data_fresh and r.bar_settled is True
                             and r.latest_close) else "·"
            mark = "→ " if st.state != prev_state else "  "
            print(f"{str(r.date):<11}{usable:<4}{_f(r.latest_close, 8)}"
                  f"{_f(r.ma5, 8)}{_f(r.ma10, 8)}{_f(r.ma20, 8)}{_f(r.ma30, 8)}"
                  f"{_tri(r.new_post_break_high_today):>5}"
                  f"{_tri(r.new_post_break_low_today):>5}  "
                  f"{mark}{st.state:<14}{'、'.join(st.reason_codes)}")
            prev_state = st.state

        last = replay_price_lifecycle(rows, show[-1].date, trading_days=cal)
        print(f"\n最终：{last.state}（{last.state_since_date} 起）"
              f"  口径 {last.formula_version}  评估 {last.evaluation_status}")
        if last.ever_cross_success:
            print(f"曾经穿越成功：{last.first_cross_success_date}")
        for t in last.reasons:
            print(f"  · {t}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
