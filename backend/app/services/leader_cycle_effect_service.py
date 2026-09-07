"""
生命周期口径的赚钱效应 —— **昨天处于某状态的票，今天赚不赚钱**。

## 为什么要换掉旧口径

强势股概览原来那四张卡（昨日涨停龙头 / 震荡龙头 / 走弱龙头 / 破位龙头）分组依据
是 `Stock.phase`，那只是"收盘价在哪条均线下面"的单日快照，说不出结构演化到哪
一步——一只刚断板正在修复的票和一只连跌十天的老龙，都可能被叫做"震荡龙头"。

换成生命周期分组之后，同一张卡回答的是有意义的问题：

    昨天判定为「修复中」的那批，今天中位收益多少？红盘率多少？
    昨天判定为「成功后走弱」的那批呢？

两者的差就是这套状态**当天**有没有区分度。

## 两个时间尺度

    当日 cohort   昨天的状态 → 今天的表现。样本小、噪声大，但**是今天的事实**
    历史前瞻      过去 N 天里所有转入该状态的样本 → 之后 T+1/T+3/T+5 的表现

历史那部分刻意**不做 bootstrap**：那要 1000 次重抽，放进接口太慢。所以它只是
线索不是结论，返回里带 `n`，界面上必须标明。真要下结论用
`scripts/evaluate_lifecycle.py`，那里有区间。

## 纪律

· 只用 settled 的行。盘中价不能进赚钱效应统计——那会让上午的浮动冒充当日结果。
· 样本不足时给 None，不给 0。`n` 一起返回，让看的人自己判断够不够。
· 不产出任何合成分。只给中位数、红盘率、样本数。
"""
from collections import defaultdict
from datetime import date
from statistics import median
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from ..models.leader_cycle import LeaderCycleSnapshot
from ..models.stock import Stock, StockDailySnapshot
from ..services.leader_cycle_state_service import (
    FORMULA_VERSION, replay_price_lifecycle,
)
from ..services.trading_calendar import get_trading_days

# 历史前瞻看几个 horizon（交易日）
HORIZONS = (1, 3, 5)
# 少于这个样本就不给中位数——个位数样本的中位数没有意义
MIN_N = 5


def _states_on(snaps_by_code: Dict[str, list], d: date, cal: List[date]) -> Dict[str, str]:
    """某一天每只票的生命周期状态。**只喂 <= d 的行**，这就是 look-ahead guard。"""
    out: Dict[str, str] = {}
    for code, rows in snaps_by_code.items():
        s = replay_price_lifecycle(rows, d, trading_days=cal)
        # 当日判不出来时用最近一次有效状态——跟界面同一个口径
        st = s.state if s.state not in ("UNKNOWN",) else s.last_valid_state
        if st:
            out[code] = st
    return out


def compute_effect(db: Session, trade_date: Optional[date] = None,
                   history_days: int = 60) -> dict:
    """
    返回 {as_of, prev, formula_version, cohorts, history, notes}。

    `cohorts` 是当日的：昨天状态 → 今天涨跌幅中位数 / 红盘率 / 样本数。
    `history` 是累积的：过去 history_days 里处于该状态的所有股票日 →
    之后 T+1/T+3/T+5 的中位收益。
    """
    # **免责声明永远带上**，包括下面几个早退分支——否则拿到空结果的调用方
    # 恰好看不到那句最该看的话
    base_notes = [
        "历史前瞻只是线索不是结论：没有做同日同池对照，也没有置信区间。"
        "多数状态的区间是跨 0 的——要下结论用 scripts/evaluate_lifecycle.py。",
        "只统计已结算的收盘价，盘中价不计入。",
    ]
    rows = db.query(LeaderCycleSnapshot).all()
    if not rows:
        return {"as_of": None, "prev": None, "cohorts": [], "history": [],
                "formula_version": FORMULA_VERSION,
                "notes": ["暂无生命周期快照", *base_notes]}

    snaps: Dict[str, list] = defaultdict(list)
    for r in rows:
        snaps[r.stock_code].append(r)
    dates = sorted({r.date for r in rows})
    as_of = trade_date or dates[-1]

    cal_all = get_trading_days(db, need_through=as_of) or []
    cal = [d for d in cal_all if d <= as_of]
    notes: List[str] = []
    if not cal:
        # 没日历就没法判"相邻交易日"，状态机会停在原地——如实说，不硬算
        notes.append("拿不到交易日历，生命周期无法推进")
        return {"as_of": as_of, "prev": None, "cohorts": [], "history": [],
                "formula_version": FORMULA_VERSION, "notes": notes + base_notes}

    # 收盘价：只用 settled 的行。盘中价不能进赚钱效应——那会让上午的浮动
    # 冒充当日结果
    sid = {s.id: s.code for s in db.query(Stock).all()}
    px: Dict[str, Dict[date, float]] = defaultdict(dict)
    for r in (db.query(StockDailySnapshot)
              .filter(StockDailySnapshot.close_price.isnot(None),
                      StockDailySnapshot.is_settled.is_(True)).all()):
        code = sid.get(r.stock_id)
        if code:
            px[code][r.date] = r.close_price

    def _ret(code: str, a: date, b: date) -> Optional[float]:
        pa, pb = px.get(code, {}).get(a), px.get(code, {}).get(b)
        return (pb / pa - 1) * 100 if pa and pb and pa > 0 else None

    def _nth(ref: date, n: int) -> Optional[date]:
        try:
            i = cal.index(ref)
        except ValueError:
            return None
        return cal[i + n] if 0 <= i + n < len(cal) else None

    # ── 当日 cohort：昨天的状态 → 今天的表现 ────────────────────────────
    prev = _nth(as_of, -1)
    cohorts = []
    if prev is None:
        notes.append("没有上一个交易日，当日 cohort 无法计算")
    else:
        by_state: Dict[str, List[float]] = defaultdict(list)
        for code, st in _states_on(snaps, prev, cal).items():
            r = _ret(code, prev, as_of)
            if r is not None:
                by_state[st].append(r)
        for st, vals in by_state.items():
            cohorts.append({
                "state": st,
                "count": len(vals),
                # 样本太少就不给中位数——个位数样本的中位数没有意义
                "median_pct_change": round(median(vals), 2) if len(vals) >= 3 else None,
                "red_ratio": round(sum(1 for v in vals if v > 0) / len(vals), 3),
            })
        cohorts.sort(key=lambda c: -c["count"])

    # ── 历史前瞻：过去 N 天所有该状态的股票日 → 之后 T+h ──────────────
    window = [d for d in dates if d <= as_of][-history_days:]
    fwd: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
    for d in window:
        for code, st in _states_on(snaps, d, cal).items():
            for h in HORIZONS:
                nd = _nth(d, h)
                if nd is None:
                    continue          # 窗口没走完，不用"目前为止"顶替
                r = _ret(code, d, nd)
                if r is not None:
                    fwd[st][h].append(r)
    history = []
    for st, per_h in fwd.items():
        item = {"state": st}
        for h in HORIZONS:
            vals = per_h.get(h) or []
            item[f"t{h}"] = round(median(vals), 2) if len(vals) >= MIN_N else None
            item[f"t{h}_n"] = len(vals)
            item[f"t{h}_win"] = (round(sum(1 for v in vals if v > 0) / len(vals), 3)
                                 if len(vals) >= MIN_N else None)
        history.append(item)
    history.sort(key=lambda x: -(x.get("t1_n") or 0))

    return {"as_of": as_of, "prev": prev, "formula_version": FORMULA_VERSION,
            "cohorts": cohorts, "history": history, "notes": notes + base_notes}
