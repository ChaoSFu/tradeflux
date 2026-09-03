"""
核对 stock_daily_snapshots 里的历史「涨停 / 连板」标记是否可信。**只读，不改数据。**

## 为什么要有这个脚本

破局雷达要用「每日最高连板」画 20 日高度上沿、判定「首次突破」。这条曲线的数据源
是 StockDailySnapshot.board_count，实测能回溯到 2026-06-02（约 65 个交易日）。

问题在于：**盘中价冒充收盘价那个 bug 是 2026-08-26 才修的**，65 天里有 59 天在修
复之前。而这个 bug 的偏向不是随机的——

    盘中封涨停 → is_limit_up=True 写进快照 → 实际收盘炸板 → 连板计数没断，继续累加

也就是说污染方向是**系统性高估最高连板**。拿一条可能被高估的曲线去画上沿、再判定
"突破"，等于把错误直接喂给结论。先证明观测可信，再用它。

## 方法

把库里 board_count >= N 的 (日期, 股票) 全取出来，用 **主链路同一条取数路径**
（fetch_klines_batch → build_kline_bar）重新拉一遍历史，逐日对账。

不自己写涨停判定，也不自己写 market 映射——2026-09-02 刚踩过：补结算手写的
`0 if market == "SH" else 1` 是反的，SH 股被拼成 sz600xxx 全部取不到数。校验脚本
如果也手写一遍，会得出"全市场历史都对不上"这种由脚本自身 bug 造成的假结论。

## 三类分歧要分开，不能混成一个"污染率"

  CONTAMINATED  重拉与**第二个源**都说库里错了
                → 库里那个 is_limit_up 确实不对
  SOURCE_CONFLICT 两个数据源自己就对不上
                → 2026-09-03 首跑吃过这个亏：脚本报 603065 宿迁联盛 06-11
                  "+8.04%，非涨停"，判定库里污染；直接问腾讯却是"收12.36 前收11.24
                  = +9.96%"，涨停，跟库里一致。两个源对同一天同一只股票差了 1.9 个
                  百分点，而涨停判定完全押在这个数上。
                  fetch_klines_batch 把股票轮流分给腾讯/新浪两组，**分组取决于列表
                  顺序**，所以记录集一变、取数源就变，同一天的结论会翻转——两次运行
                  报出来的"污染"明细居然不一样，就是这么来的。
                  单次重拉分不清"库里错"还是"这个源错"。有分歧就必须问第二个源，
                  两个源自己打架时如实报冲突，不能算成库里的污染
  REPRICED      重拉的收盘价与库里差异明显
                → 腾讯 K 线是 qfq 前复权，期间发生除权会把整段历史重新缩放，
                  绝对价对不上是必然的，不算污染。单独列出，人工确认
  SUSPENDED     历史日重拉不到 bar，但库里有记录且带连板数
                → **停牌日被顺延了陈旧的 board_count**。2026-09-03 首跑就抓到：
                  603221 爱丽家居 08-03/04/05 三天无 bar（停牌），库里却给 08-03
                  记了 9 板——那天全市场真实最高只有 6 板，这个 9 是凭空的。
                  一只停牌的高标会一直"撑住"高度曲线，制造"高度维持"的假信号，
                  对破局雷达是致命的，必须单独列出来修
  PENDING       最新那一两天重拉不到
                → 数据源当天的日 K 还没发布，正常，不是问题
  UNSETTLED     该交易日尚未收盘
                → **整天跳过，不参与对账**。盘中快照记"当前涨停"是正确行为，
                  收盘后才会被覆盖成终值；拿没定盘的一天去对账必然误报。
                  2026-09-03 盘中跑就中招了：600892 大晟文化被判 CONTAMINATED
                  （库记涨停=True、两源都说 False +9.35% 炸板），可那一刻它本来
                  就还没定盘，两边说的是同一件事的不同时刻。
                  收没收盘一律用 bar_is_settled + 市场时间判，不自己写第二套。

## 已知局限（必须写在这里，免得把结论用过头）

1. 只核对**库里认为有 >=N 连板**的那些股票。如果库里**漏记**了某只真实高板股，
   这个脚本查不出来——它只能证伪"高估"，不能证伪"低估"。而已知 bug 的方向是高估，
   所以这一遍先够用。
2. 重算的"真实最高连板"同样只在这个采样集合内取最大值，是**下界**不是精确值。
3. 候选池两个 prompt 都写了「非ST」，所以全链路的市场高度**不含 ST 股**。
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import (
    StockBasicInfo, fetch_klines_batch, market_int, get_limit_pct,
    bar_is_settled, probe_market_now, SH_TZ, _fetch_kline_tencent,
)

# 收盘价差多少算"被重新缩放过"（除权），而不是"同一天的数对不上"。
# 用相对差：低价股 0.01 元的取整差不该被判成除权。
_REPRICE_TOL = 0.02      # 2%

# 裁决用的第二意见缓存：同一只股票只问一次腾讯，别为它每条分歧都打一次网络
_SECOND_CACHE: dict = {}


def _second_opinion(code: str, market: int, is_st: bool, d: date):
    """点名问腾讯要这一天的 bar，用于裁决批量重拉与库里的分歧。拿不到返回 None。"""
    if code not in _SECOND_CACHE:
        try:
            bars = _fetch_kline_tencent(code, market, 120, is_st,
                                        get_limit_pct(code, is_st), 15)
        except Exception:  # noqa: BLE001
            bars = []
        _SECOND_CACHE[code] = {b.date: b for b in bars}
    return _SECOND_CACHE[code].get(d)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=70, help="回溯多少个自然日（默认70）")
    ap.add_argument("--min-board", type=int, default=3, help="只核对连板数>=该值的记录（默认3）")
    ap.add_argument("--workers", type=int, default=5, help="K线并发（默认5，跟主链路一致）")
    ap.add_argument("--limit-stocks", type=int, default=0, help="只抽查前N只（0=全部），用于快速试跑")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        since = date.today() - timedelta(days=args.days)
        rows = (
            db.query(StockDailySnapshot.date, Stock.code, Stock.name, Stock.market,
                     Stock.is_st, StockDailySnapshot.board_count,
                     StockDailySnapshot.close_price, StockDailySnapshot.is_limit_up)
            .join(Stock, Stock.id == StockDailySnapshot.stock_id)
            .filter(StockDailySnapshot.date >= since,
                    StockDailySnapshot.board_count >= args.min_board)
            .order_by(StockDailySnapshot.date)
            .all()
        )
        if not rows:
            print(f"近 {args.days} 天没有 board_count >= {args.min_board} 的记录，无可核对")
            return

        codes = {}
        for _, code, name, market, is_st, *_ in rows:
            codes[code] = StockBasicInfo(code=code, name=name,
                                         market=market_int(market, code),
                                         is_st=bool(is_st), pct_change=0.0, turnover_rate=0.0)
        infos = list(codes.values())
        if args.limit_stocks:
            infos = infos[:args.limit_stocks]
            keep = {i.code for i in infos}
            rows = [r for r in rows if r[1] in keep]

        # 尚未收盘的交易日整天剔除：盘中快照记"当前涨停"是对的，不是污染。
        # 收没收盘用市场自己的时间判（比本机时钟可靠，也不用硬编码 15:00 和半日市）
        market_now = probe_market_now()
        if market_now is None:
            market_now = datetime.now(SH_TZ)
            print(f"市场时间探测失败，退回本机时钟 {market_now:%H:%M:%S}")
        unsettled = {d for (d, *_r) in rows if not bar_is_settled(d, market_now)}
        if unsettled:
            skipped = [r for r in rows if r[0] in unsettled]
            rows = [r for r in rows if r[0] not in unsettled]
            print(f"跳过尚未收盘的交易日 {sorted(unsettled)}："
                  f"{len(skipped)} 条不参与对账（盘中快照不算污染）")
            if not rows:
                print("剔除后没有可对账的记录")
                return

        d0, d1 = rows[0][0], rows[-1][0]
        print(f"待核对：{len(rows)} 条记录 / {len(infos)} 只股票 / {d0} ~ {d1}")
        print(f"重新拉取 K 线（days={args.days + 5}，并发 {args.workers}）…")
        fetched = fetch_klines_batch(infos, days=args.days + 5,
                                     max_workers=args.workers, delay_between=0.1)
        got = sum(1 for c in codes if fetched.get(c))
        print(f"拉到 {got}/{len(infos)} 只\n")

        # (code, date) -> KLineBar
        bar_at = {(c, b.date): b for c, bars in fetched.items() for b in bars}
        # 数据源已经发布到哪一天。取全体股票 bar 日期的最大值——几百只一起取最大，
        # 个别停牌不会带偏。晚于它的"拉不到"是尚未发布，不是停牌
        latest_fetched = max((b.date for bars in fetched.values() for b in bars),
                             default=date.min)
        print(f"数据源已发布至 {latest_fetched}\n")

        # ── 逐条对账 ────────────────────────────────────────────────────────
        verdicts = []      # (date, code, name, kind, detail)
        stat = defaultdict(int)
        for d, code, name, _mkt, _st, bc, db_close, db_lu in rows:
            bar = bar_at.get((code, d))
            if bar is None:
                # 分开：最新交易日拉不到是"还没发布"，历史日拉不到是"停牌却有记录"
                if d >= latest_fetched:
                    stat["PENDING"] += 1
                    verdicts.append((d, code, name, "PENDING", f"{bc}板，当日K线尚未发布"))
                else:
                    stat["SUSPENDED"] += 1
                    verdicts.append((d, code, name, "SUSPENDED",
                                     f"{bc}板，该日无bar(停牌)，库里却记了连板数"))
                continue
            if db_close and bar.close_price and \
                    abs(bar.close_price - db_close) / db_close > _REPRICE_TOL:
                stat["REPRICED"] += 1
                verdicts.append((d, code, name, "REPRICED",
                                 f"{bc}板，库{db_close:.2f} vs 重拉{bar.close_price:.2f}"))
                continue
            if bool(db_lu) != bool(bar.is_limit_up):
                # 有分歧就问第二个源来裁决，绝不凭一次重拉就判库里污染（见上面
                # SOURCE_CONFLICT 的说明）。这里直接点名腾讯，不能走 fetch_kline
                # 那条带兜底的链——裁决需要的是"某个**指定**源怎么说"，而不是
                # "任意一个能用的源怎么说"，后者可能又抽到刚才那个有问题的源。
                second = _second_opinion(code, market_int(_mkt, code), bool(_st), d)
                if second is None:
                    stat["SOURCE_CONFLICT"] += 1
                    verdicts.append((d, code, name, "SOURCE_CONFLICT",
                                     f"{bc}板，第二个源也拿不到该日bar，无法裁决"))
                elif bool(second.is_limit_up) == bool(db_lu):
                    stat["SOURCE_CONFLICT"] += 1
                    verdicts.append((d, code, name, "SOURCE_CONFLICT",
                                     f"{bc}板，库记涨停={bool(db_lu)}；"
                                     f"批量源说{bar.is_limit_up}({bar.pct_change:+.2f}%)，"
                                     f"腾讯说{second.is_limit_up}({second.pct_change:+.2f}%)"
                                     f" → 两源打架，库里与腾讯一致"))
                else:
                    stat["CONTAMINATED"] += 1
                    verdicts.append((d, code, name, "CONTAMINATED",
                                     f"{bc}板，库记涨停={bool(db_lu)}，两源都说"
                                     f"{second.is_limit_up} 收{second.close_price:.2f} "
                                     f"({second.pct_change:+.2f}%)"
                                     + ("，炸板" if second.is_broken_board else "")))
                continue
            stat["OK"] += 1

        # ── 用重拉的 bar 重算连板序列，得到"真实最高连板"的下界 ──────────────
        true_streak = {}   # (code, date) -> 连续涨停数
        for code, bars in fetched.items():
            bars = sorted(bars, key=lambda b: b.date)
            run = 0
            for b in bars:
                run = run + 1 if b.is_limit_up else 0
                true_streak[(code, b.date)] = run

        # 显式解包，不用 *_rest 按下标取——第一版就是这么写的，_rest[2] 取到的是
        # is_st 而不是 board_count，于是"库里最高板"整列显示 0
        db_max, re_max = defaultdict(int), defaultdict(int)
        for d, code, _name, _mkt, _st, bc, _close, _lu in rows:
            db_max[d] = max(db_max[d], bc or 0)
            re_max[d] = max(re_max[d], true_streak.get((code, d), 0))

        print("─" * 64)
        print(f"  {'日期':<12}{'库里最高板':>10}{'重算最高板':>12}{'差':>6}")
        print("─" * 64)
        gap_days = 0
        for d in sorted(db_max):
            gap = re_max[d] - db_max[d]
            if gap:
                gap_days += 1
            flag = ("   ← 库里偏高（假高度）" if gap < 0 else
                    "   ← 库里偏低（漏记）" if gap > 0 else "")
            print(f"  {str(d):<12}{db_max[d]:>10}{re_max[d]:>12}{gap:>+6}{flag}")
        print("─" * 64)

        total = sum(stat.values())
        checkable = stat["OK"] + stat["CONTAMINATED"]
        print(f"\n对账结果（共 {total} 条）")
        print(f"  一致            {stat['OK']:>5}")
        print(f"  污染(两源都说库里错) {stat['CONTAMINATED']:>5}")
        print(f"  两源打架(无法裁决)  {stat['SOURCE_CONFLICT']:>5}   ← 不算污染，见 docstring")
        print(f"  除权嫌疑(价被缩放) {stat['REPRICED']:>5}   ← 不计入污染率，需人工确认")
        print(f"  停牌日却有连板记录 {stat['SUSPENDED']:>5}   ← **假高度，必须修**")
        print(f"  当日K线未发布     {stat['PENDING']:>5}   ← 正常，不是问题")
        if checkable:
            print(f"\n  污染率 = {stat['CONTAMINATED']}/{checkable} "
                  f"= {stat['CONTAMINATED'] / checkable * 100:.1f}%（分母只含真正可比的）")
        print(f"  最高板对不上的交易日：{gap_days}/{len(db_max)}")

        bad = [v for v in verdicts if v[3] != "OK"]
        if bad:
            print(f"\n明细（{len(bad)} 条）")
            for d, code, name, kind, detail in bad:
                print(f"  {d}  {code} {name or '':<8} [{kind}] {detail}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
