"""
生命周期状态的**前瞻收益评估** —— 回答"这套状态到底有没有交易信息增益"。

不改任何规则，只积累证据。在它给出答案之前，争论 MAX_BROKEN_DAYS 是 2 还是 3
都只是换个说法。

## 它回答什么

对每一次**状态转入**（BROKEN→REPAIRING、REPAIRING→CROSS_SUCCESS、…），记录转入
当日收盘之后 T+1/3/5/10 的收益、区间内最大有利/不利偏移（MFE/MAE）、以及是否
再次涨停。然后按事件类型聚合，直接看：

    修复中 到底有没有 Edge？
    第一次转强 是否明显优于长期停留在修复中？
    穿越成功 是确认增强，还是已经太晚？

## 五条纪律，每条都是为了防止这个工具自证

1. **必须有 baseline。** 「修复中 T+5 平均 +3%」单独看毫无意义——同期整个池子
   可能平均 +4%。所以永远同时输出 ALL_STOCK_DAYS 基线，只看**超额**。

2. **窗口不完整就是 None，不进统计。** T+10 还没走完的事件必须排除并计数，
   不能用"目前为止"的收益顶替——那会系统性偏向近期，而近期正好是样本最多的。

3. **T+N 用交易日历数，不是数组下标。** 这个仓库今天为这条栽过三次。

4. **幸存者偏差要喊出来。** 快照是"今天在池子里的股票，当时长什么样"，不是
   历史成分股。当时进不了池的票根本没有行，而它们大概率是走得差的那批。
   **所以这里所有的绝对收益都偏高，只有组间对比和超额有意义。**

5. **不给评分、不给结论。** 只输出分布：样本数、中位数、胜率、MFE/MAE。
   把"有没有 Edge"留给看的人判断——一个自动打分的评估器等于又造一个黑箱。

## 用法

    python scripts/evaluate_lifecycle.py                 # 全部事件
    python scripts/evaluate_lifecycle.py --event REPAIRING
    python scripts/evaluate_lifecycle.py --csv out.csv   # 逐条明细
"""
import argparse
import csv
import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.leader_cycle import LeaderCycleSnapshot
from app.models.stock import Stock, StockDailySnapshot
from app.services.leader_cycle_state_service import (
    FORMULA_VERSION, replay_price_lifecycle,
)
from app.services.trading_calendar import get_trading_days

HORIZONS = (1, 3, 5, 10)

# 界面读的那份产物。跟 app/config.py 的 LIFECYCLE_EVIDENCE_PATH 同一个位置
DEFAULT_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "lifecycle_evidence.json")


class Bars:
    """某只股票的日线，按交易日历索引。前瞻窗口全靠它。"""

    def __init__(self, rows, calendar: List[date]):
        self.close = {r.date: r.close_price for r in rows if r.close_price}
        # ── OHLC **绝不用收盘顶替** ────────────────────────────────────────
        # StockDailySnapshot 的 OHLC 是 2026-08-27 才加的，更早的历史行是 NULL。
        # 首版写成 `r.open_price or r.close_price`，把"不知道盘中最高最低"变成了
        # "盘中最高最低恰好等于收盘"——那是伪造观测，而且会系统性压缩 MFE/MAE。
        #
        # 更糟的是上一条提交信息写着"开盘价单独存，不拿收盘顶替"，而代码里就是
        # 在顶替。**断言一个代码不具备的性质，比单纯的 bug 更坏**：它让人以为
        # 这一层已经查过了。
        self.open = {r.date: r.open_price for r in rows if r.open_price}
        self.high = {r.date: r.high_price for r in rows if r.high_price}
        self.low = {r.date: r.low_price for r in rows if r.low_price}
        self.lu = {r.date: bool(r.is_limit_up) for r in rows}
        self._cal = calendar
        self._pos = {d: i for i, d in enumerate(calendar)}

    def has_hl(self, days: List[date]) -> bool:
        """窗口内每一天都有真实的 high/low。缺一天就算不出可信的 MFE/MAE。"""
        return all(d in self.high and d in self.low for d in days)

    def next_session(self, anchor: date) -> Optional[date]:
        i = self._pos.get(anchor)
        if i is None or i + 1 >= len(self._cal):
            return None
        d = self._cal[i + 1]
        return d if d in self.open else None

    def forward(self, anchor: date, n: int) -> Optional[List[date]]:
        """
        anchor 之后 n 个交易日的日期列表。**窗口不完整返回 None**，不返回残缺的
        前几天——用"目前为止"的收益顶替 T+10，会系统性偏向近期。
        """
        i = self._pos.get(anchor)
        if i is None or i + n >= len(self._cal):
            return None
        days = self._cal[i + 1:i + 1 + n]
        return days if all(d in self.close for d in days) else None


def _ret(a: float, b: float) -> float:
    return (b / a - 1) * 100


def evaluate(db, only_event: Optional[str] = None) -> tuple:
    cal_all = get_trading_days(db, need_through=date.today())
    if not cal_all:
        raise SystemExit("拿不到交易日历。T+N 必须按交易日数，不能用数组下标——"
                         "这个仓库今天为这条栽过三次")

    snaps = defaultdict(list)
    for r in db.query(LeaderCycleSnapshot).all():
        snaps[r.stock_code].append(r)
    if not snaps:
        raise SystemExit("没有快照，先跑 backfill_leader_cycle_snapshots.py")

    dates = sorted({r.date for rows in snaps.values() for r in rows})
    cal = [d for d in cal_all if d <= dates[-1]]

    sid = {s.code: s.id for s in db.query(Stock).all()}
    bars: Dict[str, Bars] = {}
    for code in snaps:
        rows = (db.query(StockDailySnapshot)
                .filter(StockDailySnapshot.stock_id == sid.get(code, -1))
                .order_by(StockDailySnapshot.date).all())
        bars[code] = Bars(rows, cal)

    events, skipped_incomplete = [], 0
    baseline = []
    for code, rows in snaps.items():
        b = bars[code]
        for d in dates:
            s = replay_price_lifecycle(rows, d, trading_days=cal)
            # 每个"有价格事实的交易日"都进基线池——组间比较必须有同期对照
            if d in b.close:
                baseline.append(("ALL_STOCK_DAYS", code, d, s.entry_reason_codes,
                                 (code, None)))
            # **用状态机自己的 transitioned_today / previous_state，不在外面
            # 重造一套 transition 检测**——否则"什么叫状态转移"会有两个定义，
            # 迟早分叉（这个仓库为"同一个事实两套判定"栽过 8 次）。
            if not s.transitioned_today or s.state in ("UNKNOWN", "NO_CYCLE"):
                continue
            if s.previous_state in (None, "UNKNOWN"):
                continue      # 从 UNKNOWN 转出不是转移，是"我们开始有记录了"
            # **事件是 from→to，不是 to。** BROKEN→REPAIRING（第一次转强）和
            # CROSS_FAILED→REPAIRING（失败后再修复）交易含义完全不同；
            # REPAIRING→CROSS_SUCCESS（首次二波突破）和 CROSS_WEAKENING→
            # CROSS_SUCCESS（走弱后恢复）同理。混成一组会把信号稀释掉
            ev = f"{s.previous_state}→{s.state}"
            if only_event is None or only_event in (ev, s.state):
                # cluster = (股票, 周期起点)。**同一只票同一段周期里的多次转移
                # 不是独立样本**：BROKEN→REPAIRING→FAILED→REPAIRING→SUCCESS 全
                # 出自一段行情，收益窗口还高度重叠。bootstrap 要整段一起重抽
                cyc = next((r.cycle_start_date for r in rows if r.date == d), None)
                events.append((ev, code, d, s.entry_reason_codes, (code, cyc)))

    # ── 同日同池对照 ──────────────────────────────────────────────────────
    # **逐事件对照，不是两组中位数相减。** 事件集中在特定时段，而基线摊在全部
    # 3662 个股票日上，两组根本不在同一段行情里——那样算出来的"超额"测的是
    # 行情差异，不是状态的信息量。
    # 每个事件用"当天整个池子的同期收益中位数"做对照，市场本身的涨跌被消掉。
    # **对照组里要剔掉事件股票自己**（leave-one-out）。它自己也在池子里，
    # 把它算进中位数等于用它自己给自己当基准，会把超额往 0 拉。
    # 池子只有几十只时影响不大，但工具做到这一步就该做干净。
    cohort: Dict[date, Dict[int, List[tuple]]] = defaultdict(lambda: defaultdict(list))
    cohort_op: Dict[date, Dict[int, List[tuple]]] = defaultdict(lambda: defaultdict(list))
    for _ev, code, d, _c, _cl in baseline:
        b = bars[code]
        base_px = b.close.get(d)
        if not base_px:
            continue
        nx = b.next_session(d)
        for h in HORIZONS:
            days = b.forward(d, h)
            if days is None:
                continue
            cohort[d][h].append((code, _ret(base_px, b.close[days[-1]])))
            # 可执行口径也要有自己的对照：**基线本身在次日开盘口径下就不是 0**
            # （实测 ALL_STOCK_DAYS T+10 次日开盘 +2.5、收盘 +1.0），
            # 拿事件的绝对开盘收益去跟它的收盘收益比，方向都可能读反
            if nx is not None:
                cohort_op[d][h].append((code, _ret(b.open[nx], b.close[days[-1]])))

    def _measure(sample):
        """给一组 (事件, 代码, 日期, 原因) 算前瞻指标。"""
        out = defaultdict(list)
        nonlocal skipped_incomplete
        for _ev, code, d, _codes, cluster in sample:
            b = bars[code]
            base = b.close.get(d)
            if not base:
                continue
            for h in HORIZONS:
                days = b.forward(d, h)
                if days is None:
                    skipped_incomplete += 1
                    continue          # 窗口没走完，**不进统计**
                r = _ret(base, b.close[days[-1]])
                out[f"ret{h}"].append(r)
                peers = [x for c2, x in (cohort.get(d, {}).get(h) or [])
                         if c2 != code]
                # 同日至少要有 3 只**别的**股票才算得出对照
                if len(peers) >= 3:
                    out[f"exc{h}"].append(r - st.median(peers))
                    out[f"exc{h}_cl"].append(cluster)
                # ── 可执行口径：次日开盘买入 ──────────────────────────────
                # 包含 T+1（开盘买、当天收盘看）——**那恰恰是最贴近实际操作的
                # 一格**：昨天收盘识别出信号，今天买进去，当天什么表现。
                # 首版写了 `if h > 1` 把它跳过了
                nx = b.next_session(d)
                if nx is not None:
                    orr = _ret(b.open[nx], b.close[days[-1]])
                    out[f"op{h}"].append(orr)
                    op_peers = [x for c2, x in (cohort_op.get(d, {}).get(h) or [])
                                if c2 != code]
                    if len(op_peers) >= 3:
                        out[f"opx{h}"].append(orr - st.median(op_peers))
                        out[f"opx{h}_cl"].append(cluster)
                # MFE/MAE 只在窗口内每天都有真实 high/low 时才算
                if b.has_hl(days):
                    out[f"mfe{h}"].append(max(_ret(base, b.high[x]) for x in days))
                    out[f"mae{h}"].append(min(_ret(base, b.low[x]) for x in days))
                if h == 5:
                    out["lu5"].append(1.0 if any(b.lu.get(x) for x in days) else 0.0)
        return out

    # ── Balanced cohort ──────────────────────────────────────────────────
    # T+1 的样本包含近期事件，T+10 只包含更早的——两者市场环境和样本组成不同，
    # 所以"T+1 -0.4 而 T+10 -7.6"**不能**解释成"持有越久越差"。
    # 只有在同一批（能走完 T+10 的）事件上比较，才谈得上时间衰减。
    longest = max(HORIZONS)

    def _complete(sample):
        return [e for e in sample if bars[e[1]].forward(e[2], longest) is not None]

    groups = defaultdict(list)
    for e in events:
        groups[e[0]].append(e)
    groups["ALL_STOCK_DAYS"] = baseline
    full = {k: (len(v), _measure(v)) for k, v in groups.items()}
    bal_src = {k: _complete(v) for k, v in groups.items()}
    balanced = {k: (len(v), _measure(v)) for k, v in bal_src.items() if v}
    return full, balanced, skipped_incomplete, events, dates[-1]


def _fmt(vals: List[float]) -> str:
    return f"{st.median(vals):>+6.1f}" if vals else "    —"


def _rate(vals: List[float]) -> str:
    """
    发生比例。**必须用均值,不能用中位数** —— 0/1 列表的中位数只会是 0/0.5/1，
    首版就是这么把「5日再涨停」印成了一列 100%/0%，而基线显示 0% 更是明显荒谬。
    """
    return f"{sum(vals) / len(vals) * 100:>5.0f}%" if vals else "    —"


def _cluster_bootstrap(vals: List[float], clusters: List[tuple],
                       n_boot: int = 1000, seed: int = 20260906) -> Optional[tuple]:
    """
    按 cluster 重抽的 95% 区间，返回 (中位数下界, 上界, 正超额率下界, 上界)。

    **整段周期一起抽，不是逐个事件抽。** 同一只票同一段周期里
    BROKEN→REPAIRING→FAILED→REPAIRING→SUCCESS 全出自一段行情，收益窗口还高度
    重叠——按独立样本算区间会严重高估把握。实测 n=40 时，即便当成独立样本，
    55% 的粗略区间也已经跨过 50%；按 cluster 抽只会更宽。

    样本或 cluster 太少时返回 None——**给一个假的区间比不给更糟**。
    """
    import random
    by_cluster: Dict[tuple, List[float]] = defaultdict(list)
    for v, c in zip(vals, clusters):
        by_cluster[c].append(v)
    keys = list(by_cluster)
    if len(vals) < 8 or len(keys) < 5:
        return None
    rnd = random.Random(seed)
    meds, rates = [], []
    for _ in range(n_boot):
        pick: List[float] = []
        for _ in range(len(keys)):
            pick.extend(by_cluster[keys[rnd.randrange(len(keys))]])
        if not pick:
            continue
        meds.append(st.median(pick))
        rates.append(sum(1 for x in pick if x > 0) / len(pick) * 100)
    if not meds:
        return None
    meds.sort(); rates.sort()
    lo, hi = int(0.025 * len(meds)), int(0.975 * len(meds)) - 1
    return meds[lo], meds[hi], rates[lo], rates[hi]


def _cell(vals) -> str:
    """中位数 + 有效样本数。**每个指标的 N 都要单独给**——事件数 68 不等于
    每一项都有 68：窗口没走完、OHLC 缺失、同日对照不足，各扣各的。"""
    return f"{st.median(vals):>+6.1f}({len(vals):>3})" if vals else "      —   "


def _print_table(title: str, res: dict, key: str, order: List[str]):
    print(f"\n{title}")
    print(f"{'事件':<28}" + "".join(f"{'T+' + str(h):>12}" for h in HORIZONS))
    for ev in order:
        if ev not in res:
            continue
        _n, m = res[ev]
        print(f"{ev:<28}" + "".join(_cell(m.get(f"{key}{h}", [])) for h in HORIZONS))



def _stat(vals: List[float]) -> Optional[dict]:
    """一格指标。**没有样本就是 None,不是 0**。"""
    if not vals:
        return None
    return {"median": round(st.median(vals), 2), "n": len(vals)}


def _build_payload(full: dict, balanced: dict, skipped: int,
                   order: List[str], as_of: Optional[date]) -> dict:
    """
    把评估结果摊成 JSON。**界面用的是这一份,不是 stdout 的表格**——
    让人照着终端输出往前端里抄数字,抄错了没人会发现,而且第二天就过期了。

    每一格都带自己的 n。事件数 41 不等于每一项都有 41:窗口没走完、
    OHLC 缺失、同日对照不足,各扣各的。
    """
    def one(key: str) -> dict:
        n_total, m = full[key]
        excess = {}
        for h in HORIZONS:
            vals = m.get(f"exc{h}", [])
            cell = _stat(vals)
            if cell is not None:
                pos = sum(1 for x in vals if x > 0) / len(vals)
                cell["pos_rate"] = round(pos, 3)
                ci = _cluster_bootstrap(vals, m.get(f"exc{h}_cl", []))
                # **区间算不出来就是 None,不给一个假的**
                cell["ci"] = [round(ci[0], 1), round(ci[1], 1)] if ci else None
                cell["crosses_zero"] = (ci[0] * ci[1] <= 0) if ci else None
            excess[str(h)] = cell
        bal = balanced.get(key)
        return {
            "event": key,
            "from": key.split("\u2192")[0] if "\u2192" in key else None,
            "to": key.split("\u2192")[1] if "\u2192" in key else None,
            "n_events": n_total,
            "excess": excess,
            # 同一批（能走完 T+10 的）样本——只有这张能谈时间衰减
            "excess_balanced": {str(h): _stat(bal[1].get(f"exc{h}", []))
                                for h in HORIZONS} if bal else None,
            # 次日开盘买入。T+1 那格 = 开盘买、当天收盘卖,最贴近实际操作
            "exec_excess": {str(h): _stat(m.get(f"opx{h}", [])) for h in HORIZONS},
            "mfe5": _stat(m.get("mfe5", [])),
            "mae5": _stat(m.get("mae5", [])),
        }

    return {
        "formula_version": FORMULA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat() if as_of else None,
        "horizons": list(HORIZONS),
        "skipped_incomplete": skipped,
        "events": [one(k) for k in order if k != "ALL_STOCK_DAYS"],
        "baseline": one("ALL_STOCK_DAYS") if "ALL_STOCK_DAYS" in full else None,
        # **免责声明跟数字一起走。** 数字会被复制到界面上,注意事项不会——
        # 除非把它绑在同一个对象里
        "caveats": [
            "同日同池只消掉了市场行情,没有消掉样本选择:快照是「今天在池子里的"
            "股票,当时长什么样」,当时进不了池的票根本没有行。绝对收益偏高,"
            "组间对比也只在「今天的幸存者」这个宇宙内成立。",
            "同一只票、同一段周期会贡献多次事件,收益窗口还高度重叠——有效样本"
            "数远小于打印出来的 n,不要按独立样本算显著性。",
            "95% 区间按「股票×周期」整段重抽 1000 次。跨 0 = 方向可能是噪声,"
            "不管中位数看起来多好看。",
            "这里不给结论也不打分。一个自动判定「有没有 Edge」的评估器,等于又"
            "造一个黑箱。",
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event", help="只看某一种转移，如 BROKEN→REPAIRING 或 REPAIRING")
    ap.add_argument("--min-n", type=int, default=8,
                    help="样本少于这个数的事件不显示（默认8，太少的中位数没有意义）")
    ap.add_argument("--csv", help="逐条明细写到这个文件")
    ap.add_argument("--json", dest="json_out",
                    help="结构化结果写到这个文件，界面读它（默认 "
                         "backend/data/lifecycle_evidence.json）",
                    nargs="?", const="__default__")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        full, balanced, skipped, events, as_of = evaluate(db, args.event)
        print(f"口径 {FORMULA_VERSION}\n")
        print("⚠️ 这里的「同日同池」只消掉了**市场行情**，没有消掉**样本选择**：")
        print("   快照是「今天在池子里的股票，当时长什么样」，当时进不了池的票")
        print("   根本没有行。要彻底解决得能按时点重建历史强势池成分。")
        print("   **所以绝对收益偏高，组间对比也只在「今天的幸存者」这个宇宙内成立。**")

        order = sorted((k for k in full if k != "ALL_STOCK_DAYS"),
                       key=lambda k: -full[k][0])
        order = [k for k in order if full[k][0] >= args.min_n] + ["ALL_STOCK_DAYS"]

        _print_table("同日同池超额（逐事件对照后取中位数，括号内为有效样本）：",
                     full, "exc", order)
        print("  同日不足 3 只同类就没有对照，不计入——所以这里的 N 小于事件总数。")

        _print_table("同一批样本（能走完 T+10 的事件）——只有这张能看时间衰减：",
                     balanced, "exc", order)
        print("  上一张表里 T+1 含近期事件、T+10 只含更早的，两者样本组成不同，")
        print("  『T+1 略负而 T+10 大负』**不能**解释成「持有越久越差」。")

        print("\n正超额占比（超过同日同池中位数的比例，括号内为有效样本）：")
        for ev in order:
            if ev not in full:
                continue
            _n, m = full[ev]
            parts = []
            for h in HORIZONS:
                a = m.get(f"exc{h}", [])
                parts.append(f"T+{h} {sum(1 for x in a if x > 0) / len(a) * 100:>3.0f}%"
                             f"({len(a):>3})" if a else f"T+{h}    —    ")
            print(f"  {ev:<28}" + " ".join(parts))
        print("  50% 附近 = 跟随机没区别。**n≈50 时中位数差 1~2 个点本来就是噪声**，")
        print("  而且同一只票、同一段周期会贡献多次事件，窗口还高度重叠——")
        print("  有效样本数远小于打印出来的 N，不要按独立样本去算显著性。")

        _print_table("可执行超额（次日开盘买入，同样对同日同池；括号内有效样本）：",
                     full, "opx", order)
        print("  T+1 那一格 = 次日开盘买、当天收盘卖，**最贴近实际操作的一格**。")
        print("  为什么必须看超额而不是绝对收益：基线自己在开盘口径下就不是 0")
        print("  （实测 ALL_STOCK_DAYS T+10 开盘 +2.5 / 收盘 +1.0），拿事件的绝对")
        print("  开盘收益去跟收盘收益比，方向都可能读反。")

        # ── 区间：没有它，上面每一个数都会被过度解读 ──────────────────────
        print("\n95% 区间（按「股票×周期」整段重抽 1000 次；跨 0 就是证据不足）：")
        any_ci = False
        for ev in order:
            if ev not in full:
                continue
            _n, m = full[ev]
            parts = []
            for h in HORIZONS:
                ci = _cluster_bootstrap(m.get(f"exc{h}", []),
                                        m.get(f"exc{h}_cl", []))
                if ci is None:
                    parts.append(f"T+{h} 样本不足")
                    continue
                any_ci = True
                mark = "" if ci[0] * ci[1] > 0 else "  跨0"
                parts.append(f"T+{h} [{ci[0]:+.1f},{ci[1]:+.1f}]{mark}")
            print(f"  {ev:<28}" + "  ".join(parts))
        if not any_ci:
            print("  （全部样本或 cluster 太少，算不出区间——不给假区间）")
        print("  同一只票同一段周期里的多次转移不是独立样本，收益窗口还高度重叠。")
        print("  **区间跨 0 = 方向可能是噪声**，不管中位数看起来多好看。")

        print("\nMFE/MAE（5日，括号内为有效样本）：")
        for ev in order:
            if ev not in full:
                continue
            _n, m = full[ev]
            mf, ma = m.get("mfe5", []), m.get("mae5", [])
            print(f"  {ev:<28}MFE {_cell(mf)}   MAE {_cell(ma)}")
        print("  OHLC 是 2026-08-27 才加的字段，更早的行是 NULL。**缺就不算**，")
        print("  不拿收盘顶替——那会把「不知道盘中高低」变成「高低恰好等于收盘」。")

        if skipped:
            print(f"\n窗口未走完而排除的 {skipped} 次测量")
        print("\n**这里不给结论也不打分**——一个自动判定「有没有 Edge」的评估器，"
              "等于又造一个黑箱。")

        if args.csv:
            with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["event", "code", "date", "entry_reasons"])
                for ev, code, d, codes, _cluster in events:
                    w.writerow([ev, code, d, "|".join(codes)])
            print(f"\n明细写入 {args.csv}（{len(events)} 条）")

        if args.json_out:
            path = (DEFAULT_JSON if args.json_out == "__default__"
                    else args.json_out)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            payload = _build_payload(full, balanced, skipped, order, as_of)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"\n结构化结果写入 {path}"
                  f"（{len(payload['events'])} 类事件，界面读它）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
