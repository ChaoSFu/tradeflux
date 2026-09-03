"""
破局雷达 / Speculation Regime Radar —— 市场投机高度的演化。

回答的不是"今天哪只股票最强"，而是：**市场原本的高度天花板，是不是正在被打开？**

## 为什么不是只看"最高连板"

一个 8 板 ≠ 市场进入 8 板周期，可能只是一只孤零零的妖股。所以高度前沿曲线下面
必须配梯队分布：高度上移**且**中位梯队变厚，才叫高度扩张。

    H_t  当日最高连板        —— 天花板多高
    F_t  板数 >= H_t - 1 的只数 —— 天花板附近是不是只有一只孤票
    M_t  3 板以上的只数       —— 中高位赚钱效应有没有扩散

## 数据可信度（2026-09-03 用 verify_board_history.py 实测过 66 个交易日）

  · 偏高（危险方向）0 例 —— 原先担心的"盘中价 bug 系统性抬高高度"没有发生
  · 偏低（漏记）    5 例 —— 只会让我们错过突破，不会捏造突破
  · 停牌顺延        1 例 —— 唯一真正向上的错，见下面 _drop_carried_forward

## 停牌顺延：必须在聚合时挡掉，不能只修一次数据

603221 爱丽家居 08-03/04/05 停牌（腾讯三天无 bar），库里却给 08-03 记了 9 板——
那天全市场真实最高只有 6 板。一只停牌的高标会持续"撑住"高度曲线，制造"天花板
还在"的假信号，而这恰恰是判断"高度有没有被打破"时最不能出错的地方。

挡它用的是一条**数据内部的强不变量**，不是拍出来的启发式：

    真连板每天恰好 +1。今天 N 板，上一个交易日必然是 N-1 板。

爱丽家居 07-31 是 9 板、08-03 还是 9 板——同一个数字连续两天，真连板不可能这样。
但这条不变量**只能用来抓"同一个数字连续两天"这一种形态**。生产首测打出 19 条
警告，逐条看下来它把三种完全不同的情况揉成了一种：

  · 同一数字连续两天（000811 2板←2板、603580 3板←3板）→ 真·陈旧值顺延，剔除
  · 上一日为 0（12/19 条）→ **我们没有昨天那条记录**。可能是那天它不在候选池，
    也可能是 is_limit_up 漏记（verify_board_history 实测有 5 例）。这是"不知道"
    不是"错"，剔除等于静默删掉真实的高板——07-17 002677 3板就这么被误删了，
    而 verify 脚本恰恰说 07-17 库里偏低漏记，我把漏记做得更严重了
  · 数字下降（002354 2板←3板）→ 另一类异常，成因不明，先如实报出来不动它

所以只对第一种下手，另外两种记进 notes 但**保留数据**。宁可让一条可疑记录留在
图上并标注出来，也不能悄悄删掉一批真实高板——后者会让高度曲线系统性偏低，而
那正是这张图要回答的问题本身。

## 口径（页面必须写明，否则会被当成全市场高度）

两个选股 prompt 都写了「非ST」，所以这里的市场高度**不含 ST 股**。ST 是 5% 板，
跟主板 10%/20% 不是同一个游戏，排除它对"投机高度"说得通，但不能不说。
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from ..models.stock import Stock, StockDailySnapshot

# 梯队热力图最高展示到几板。超过的归进最后一档，避免一只妖股把整张表拉宽
LADDER_MAX = 8
# 高度上沿的回看窗口（交易日）
FRONTIER_WINDOW = 20


@dataclass
class HeightPoint:
    date: str
    height: int                 # H_t 当日最高连板
    frontier: Optional[int]     # 近 FRONTIER_WINDOW 日（不含当日）的最高连板 = 上沿
    is_breakout: bool           # 当日高度 > 上沿 → 首次突破
    near_top_count: int         # F_t 板数 >= H_t - 1 的只数
    multi_board_count: int      # M_t 3 板以上只数
    limit_up_count: int         # 当日涨停总数（分母，看高度时要知道基数）
    ladder: Dict[str, int] = field(default_factory=dict)   # {"1": 48, "2": 14, ...}


def _drop_carried_forward(
    by_date: Dict[date, Dict[str, int]], days: List[date],
) -> tuple[Dict[date, Dict[str, int]], List[str]]:
    """
    只剔除"连板数与上一交易日完全相同"的记录——停牌顺延陈旧值的确切指纹。
    其余异常如实记录但保留数据。理由见模块 docstring。
    """
    warns: List[str] = []
    prev_day: Optional[date] = None
    cleaned: Dict[date, Dict[str, int]] = {}
    for d in days:
        cur = by_date.get(d, {})
        if prev_day is None:
            # 序列第一天没有上一交易日，这条不变量无从应用。边界处"不知道"不等于
            # "有问题"，不能因为缺上下文就指控它
            cleaned[d] = dict(cur)
            prev_day = d
            continue
        prev = by_date.get(prev_day, {})
        keep: Dict[str, int] = {}
        for code, bc in cur.items():
            pv = prev.get(code)
            if bc >= 2 and pv is not None and pv == bc:
                # 唯一确定的错：真连板每天恰好+1，同一个数字连续两天不可能
                warns.append(f"{d} {code} {bc}板：与上一交易日同为 {pv} 板，"
                             f"连板数未递增（停牌顺延陈旧值），已剔除")
                continue
            keep[code] = bc
            if bc >= 2 and pv is None:
                warns.append(f"{d} {code} {bc}板：上一交易日无涨停记录，"
                             f"无法验证连板链（可能漏记或当日不在候选池）——保留")
            elif bc >= 2 and pv is not None and pv + 1 != bc:
                warns.append(f"{d} {code} {bc}板：上一交易日为 {pv} 板，"
                             f"未按+1递增（成因不明）——保留")
        cleaned[d] = keep
        prev_day = d
    return cleaned, warns


def compute_height_series(db: Session, days: int = 60) -> tuple[List[HeightPoint], List[str]]:
    """
    高度前沿曲线 + 连板梯队。返回 (序列, 警告)。

    只读 StockDailySnapshot，零外部请求。board_count 实测可回溯到 2026-06-02。
    """
    all_days = [r[0] for r in (
        db.query(StockDailySnapshot.date)
        .filter(StockDailySnapshot.is_limit_up.is_(True))
        .distinct().order_by(StockDailySnapshot.date.desc()).limit(days + FRONTIER_WINDOW).all()
    )]
    if not all_days:
        return [], ["没有任何涨停快照，无法计算市场高度"]
    all_days.sort()

    rows = (
        db.query(StockDailySnapshot.date, Stock.code,
                 StockDailySnapshot.board_count, StockDailySnapshot.is_limit_up)
        .join(Stock, Stock.id == StockDailySnapshot.stock_id)
        .filter(StockDailySnapshot.date >= all_days[0],
                StockDailySnapshot.is_limit_up.is_(True))
        .all()
    )
    by_date: Dict[date, Dict[str, int]] = {d: {} for d in all_days}
    lu_count: Dict[date, int] = {d: 0 for d in all_days}
    for d, code, bc, _lu in rows:
        if d not in by_date:
            continue
        lu_count[d] += 1
        # **board_count 缺失就是不知道，不能按 1 板算**。dump 回填的历史行只写
        # K线原始字段（含涨跌停标志），不写连板数，那些行的 board_count 是 0。
        # 第一版写的是 `bc if bc and bc > 0 else 1`——把"不知道"当成了"1板"，
        # 于是 06-02 之前那些只有涨跌停标志的日子全被算成"最高1板"，早期上沿
        # 恒为 1~2，06-03/04/05/08 连续四天被标成"突破"。
        # 这是本仓库反复踩的"用一个数字表达不知道"，这次犯在自己手上。
        if bc and bc > 0:
            by_date[d][code] = bc

    # 完全没有连板数据的交易日（只有涨跌停标志的回填行）整天剔除：它不是
    # "那天最高0板"，是"那天我们不知道"。留着会把上沿基线拉到地板上
    usable = [d for d in all_days if by_date.get(d)]
    dropped_days = len(all_days) - len(usable)
    all_days = usable
    if not all_days:
        return [], ["加载到的交易日都没有连板数据，无法计算市场高度"]

    by_date, warns = _drop_carried_forward(by_date, all_days)
    if dropped_days:
        warns.insert(0, f"{dropped_days} 个交易日没有任何连板数据（历史回填行不写 "
                        f"board_count），整天不参与计算，也不作为上沿基线")

    out: List[HeightPoint] = []
    for i, d in enumerate(all_days):
        counts = by_date.get(d, {})
        height = max(counts.values(), default=0)
        ladder: Dict[str, int] = {}
        for bc in counts.values():
            key = str(min(bc, LADDER_MAX)) + ("+" if bc > LADDER_MAX else "")
            ladder[key] = ladder.get(key, 0) + 1
        # 上沿只看**之前**那些天，含当日就永远不可能"突破"自己。
        # **窗口不满 FRONTIER_WINDOW 天就没有上沿**——这不是保守，是不知道：
        # 用 2 天算出来的"20日上沿"必然低得离谱，于是最早那些天全被标成"突破"。
        # 生产首测就是这么翻车的：06-03/04/05/08 连续四天 is_breakout=true，
        # 那不是行情，是滚动窗口还没攒够数据的伪影。宁可线短一截、开头不给结论，
        # 也不能拿一个假上沿去判"天花板被打破了"——这张图的全部意义就在这个判定上。
        win = all_days[max(0, i - FRONTIER_WINDOW):i]
        frontier = (max((max(by_date.get(w, {}).values(), default=0) for w in win),
                        default=0)
                    if len(win) >= FRONTIER_WINDOW else None)
        out.append(HeightPoint(
            date=str(d), height=height,
            frontier=frontier or None,
            is_breakout=bool(frontier and height > frontier),
            near_top_count=sum(1 for v in counts.values() if height and v >= height - 1),
            multi_board_count=sum(1 for v in counts.values() if v >= 3),
            limit_up_count=lu_count.get(d, 0),
            ladder=ladder,
        ))
    # 前 FRONTIER_WINDOW 天只是用来给后面的日子当上沿基线，本身没有上沿，不展示
    return out[-days:], warns
