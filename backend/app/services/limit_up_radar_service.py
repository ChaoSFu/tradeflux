"""
涨停板块雷达聚合（2026-08-25新增）。

这个页面回答的不是"今天有哪些股票涨停"，而是：
  资金今天在哪些**板块**形成了集团进攻，以及这个板块的核心是谁、
  老核心和今天的新涨停有没有形成共振。

所以聚合的输出单位是板块，不是个股。两条设计红线：

1) **Core Recall != Core Classification**
   下面 recall_core_roles() 里那组 OR 条件只负责"把可能是核心的股票捞进视野"，
   不负责宣布谁是龙头。捞进来之后只打可解释的标签（近60日涨停6次 / 板块龙头），
   不算综合分、不排名次。宁可多捞几只让用户自己判断，也不能因为一个阈值把
   真正的板块核心挡在视野外——这是整个功能最重要的取舍。

2) **归组走 StockSectorRelation，不走涨停原因**
   东财的 LIMIT_REASON 是"今天为什么涨"（催化剂），不是"这只股票属于什么板块"。
   "业绩增长+中期分红+光伏" 是三个催化剂，不是三个稳定的板块ID。用自然语言原因
   动态造板块，会得到一堆一次性的、跟系统其他地方对不上的伪板块。

排序全部用明确的词典序，不做线性加权总分——见 sort_sectors / sort_today_stocks
的注释。本轮刻意不引入任何新的黑盒评分。
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from ..models.limit_up_detail import BrokenBoardDailyDetail, LimitUpDailyDetail
from ..models.sector import Sector, StockSectorRelation
from ..models.stock import Stock, StockDailySnapshot

# ── Core Recall 阈值（集中在这里，不散落在逻辑里）────────────────────────────
# 全部是"召回"阈值：满足任意一条就进入视野。刻意放宽，见模块 docstring 红线1。
DEFAULT_CORE_10D_MIN = 2        # 近10日涨停次数
DEFAULT_CORE_20D_MIN = 3        # 近20日涨停次数
DEFAULT_CORE_60D_MIN = 5        # 近60日涨停次数
DEFAULT_CORE_MAX_BOARD_MIN = 3  # 近60日最高连板数

# 角色标签优先级（数字小=优先展示）。纯粹反映"最近有多活跃"，不是强弱排名。
_ROLE_PRIORITY = {
    "CURRENT_CORE": 0,     # 近10日还在涨停 —— 当前正在起作用的核心
    "RECENT_CORE": 1,      # 近20日活跃 / 打出过高连板 —— 近期核心
    "SECTOR_LEADER": 2,    # 板块龙头标记
    "SECTOR_CORE": 3,      # 板块核心标记
    "HISTORICAL_CORE": 4,  # 只有60日窗口才够得着 —— 历史核心/情绪锚
}


@dataclass
class CoreRecall:
    """一只股票被召回成"核心候选"的结果。roles/reasons 全部可解释，没有分数。"""
    roles: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    @property
    def primary_role(self) -> Optional[str]:
        if not self.roles:
            return None
        return min(self.roles, key=lambda r: _ROLE_PRIORITY.get(r, 99))


def recall_core_roles(
    stock: Stock,
    rel: Optional[StockSectorRelation],
    *,
    core_10d_min: int = DEFAULT_CORE_10D_MIN,
    core_20d_min: int = DEFAULT_CORE_20D_MIN,
    core_60d_min: int = DEFAULT_CORE_60D_MIN,
    core_max_board_min: int = DEFAULT_CORE_MAX_BOARD_MIN,
) -> CoreRecall:
    """
    宽召回：满足任意一条就算"值得出现在板块核心区"。**这不是在判定龙头。**

    为什么必须是 OR 而且必须包含长窗口：
      一只经历过完整一轮行情的核心（比如某轮医药行情里连续涨停过的票），进入趋势/
      震荡阶段后可能近10日一次涨停都没有，今天甚至只有 +2%。只看今日涨停 → 它消失；
      只看近10日涨停 → 它还是消失。但它可能仍然是这个板块市场辨识度最高的情绪锚。
      它一旦从页面上消失，用户看到"低位2只首板"就可能误判成"板块正在增强"，而真实
      结构可能是"老核心负反馈 + 低位补涨"——这是完全相反的两件事。
    """
    out = CoreRecall()

    def hit(role: str, reason: str) -> None:
        if role not in out.roles:
            out.roles.append(role)
        out.reasons.append(reason)

    lu10 = stock.limit_up_days_10d or 0
    lu20 = stock.limit_up_days_20d or 0
    lu60 = stock.limit_up_days_60d or 0
    max_board = stock.board_count_60d or 0

    if lu10 >= core_10d_min:
        hit("CURRENT_CORE", f"近10日涨停{lu10}次")
    if lu20 >= core_20d_min:
        hit("RECENT_CORE", f"近20日涨停{lu20}次")
    if max_board >= core_max_board_min:
        hit("RECENT_CORE", f"近60日最高{max_board}连板")
    if lu60 >= core_60d_min:
        hit("HISTORICAL_CORE", f"近60日涨停{lu60}次")
    if rel is not None and rel.is_leader:
        hit("SECTOR_LEADER", "板块龙头")
    if rel is not None and rel.is_core:
        hit("SECTOR_CORE", "板块核心")
    return out


# ── 排序：全部词典序，不加权 ─────────────────────────────────────────────────

_LATE = time(23, 59, 59)   # 缺失时间排最后，不让 None 冒充"很早就封板"


def sort_today_stocks(rows: List[dict]) -> List[dict]:
    """
    板块内今日涨停股排序（需求§14）：
      连板数 DESC → 首封时间 ASC → 最终封板时间 ASC → 封单额 DESC → 近10日涨停 DESC
    连板高的先看，同板位里先封的先看，同时间里封得稳（最终封板早）的先看。
    绝不按股票代码排。
    """
    return sorted(rows, key=lambda r: (
        -(r.get("board_count") or 0),
        r.get("first_limit_time") or _LATE,
        r.get("last_limit_time") or _LATE,
        -(r.get("seal_amount") or 0.0),
        -(r.get("limit_up_days_10d") or 0),
    ))


def sort_core_stocks(rows: List[dict]) -> List[dict]:
    """
    核心锚排序（需求§15）：板块标记优先，其后按历史活跃度从长窗口到短窗口。
    保持可解释——每一项都能直接对应到页面上显示的那行标签，不引入综合分。
    """
    return sorted(rows, key=lambda r: (
        0 if ("SECTOR_LEADER" in r["core_roles"] or "SECTOR_CORE" in r["core_roles"]) else 1,
        -(r.get("limit_up_days_60d") or 0),
        -(r.get("limit_up_days_20d") or 0),
        -(r.get("board_count_60d") or 0),
        -(r.get("leader_score") or 0.0),
    ))


def sort_sectors(sectors: List[dict]) -> List[dict]:
    """
    板块排序（需求§19）：
      今日涨停数 DESC → 最高连板 DESC → 连板股数量 DESC → 最早首封时间 ASC → 总封单额 DESC
    先看谁涨停最多（集团进攻的规模），同规模看谁打出了更高的板（高度），再看有多少
    只在连板（梯队厚度），再看谁先发动，最后看资金排队规模。
    刻意不做 0.4*涨停数+0.3*板高+... 这种加权总分：用户需要能一眼说清"为什么这个
    板块排在前面"，加权分做不到这一点。
    """
    return sorted(sectors, key=lambda s: (
        -s["today_limit_up_count"],
        -s["board_height"],
        -s["continuation_count"],
        s["earliest_limit_time"] or _LATE,
        -(s["total_seal_amount"] or 0.0),
    ))


# ── 主聚合 ───────────────────────────────────────────────────────────────────

def build_radar(
    db: Session,
    trade_date: date,
    *,
    include_core: bool = True,
    group_mode: str = "all_watched_sectors",
    core_10d_min: int = DEFAULT_CORE_10D_MIN,
    core_20d_min: int = DEFAULT_CORE_20D_MIN,
    core_60d_min: int = DEFAULT_CORE_60D_MIN,
    core_max_board_min: int = DEFAULT_CORE_MAX_BOARD_MIN,
    max_sectors: int = 40,
) -> dict:
    """
    组装涨停板块雷达。全部读本地库，不发任何外部请求（外部抓取只在同步那一步）。

    group_mode:
      all_watched_sectors（默认）—— 走 StockSectorRelation，一只股票可以出现在多个
        关注板块里。默认优先 Recall：漏掉真正的核心，比同一只股票出现两次严重得多。
        同一板块内部仍然只出现一次。
      primary —— 只按 Stock.primary_sector_id 归组，一只股票只出现一次。给需要去重
        的场景用。
    """
    details = (
        db.query(LimitUpDailyDetail)
        .filter(LimitUpDailyDetail.trade_date == trade_date).all()
    )
    broken = (
        db.query(BrokenBoardDailyDetail)
        .filter(BrokenBoardDailyDetail.trade_date == trade_date).all()
    )
    if not details and not broken:
        return _empty_result(trade_date)

    detail_by_sid = {d.stock_id: d for d in details}
    broken_sids = {b.stock_id for b in broken}

    # ── 一次性批量取齐所有需要的本地数据（不做 N+1）──────────────────────────
    sectors: List[Sector] = db.query(Sector).filter(Sector.is_watched == True).all()  # noqa: E712
    sector_by_id = {s.id: s for s in sectors}
    watched_ids = set(sector_by_id)
    if not watched_ids:
        return _empty_result(trade_date, warnings=["没有任何关注板块（is_watched），无法按板块聚合"])

    rels: List[StockSectorRelation] = (
        db.query(StockSectorRelation)
        .filter(StockSectorRelation.sector_id.in_(watched_ids)).all()
    )
    rels_by_sector: Dict[int, List[StockSectorRelation]] = {}
    rel_lookup: Dict[Tuple[int, int], StockSectorRelation] = {}
    member_sids: Set[int] = set()
    for r in rels:
        rels_by_sector.setdefault(r.sector_id, []).append(r)
        rel_lookup[(r.stock_id, r.sector_id)] = r
        member_sids.add(r.stock_id)

    # 需要 Stock 的：所有板块成员（核心召回要用滚动指标）+ 今日涨停/炸板股
    need_sids = member_sids | set(detail_by_sid) | broken_sids
    stocks: Dict[int, Stock] = {
        s.id: s for s in db.query(Stock).filter(Stock.id.in_(need_sids)).all()
    } if need_sids else {}

    snaps: Dict[int, StockDailySnapshot] = {
        s.stock_id: s
        for s in db.query(StockDailySnapshot)
        .filter(StockDailySnapshot.date == trade_date,
                StockDailySnapshot.stock_id.in_(need_sids)).all()
    } if need_sids else {}

    # ── 决定每只股票进哪些板块 ────────────────────────────────────────────────
    if group_mode == "primary":
        sids_by_sector: Dict[int, Set[int]] = {}
        for sid, st in stocks.items():
            if st.primary_sector_id in watched_ids:
                sids_by_sector.setdefault(st.primary_sector_id, set()).add(sid)
    else:
        sids_by_sector = {
            sec_id: {r.stock_id for r in rl} for sec_id, rl in rels_by_sector.items()
        }

    out_sectors: List[dict] = []
    for sec_id, sids in sids_by_sector.items():
        sector = sector_by_id.get(sec_id)
        if not sector:
            continue
        card = _build_sector_card(
            sector, sids, detail_by_sid, broken_sids, stocks, snaps, rel_lookup,
            include_core=include_core,
            core_10d_min=core_10d_min, core_20d_min=core_20d_min,
            core_60d_min=core_60d_min, core_max_board_min=core_max_board_min,
        )
        # 今天一只涨停都没有的板块不进雷达——这是"涨停板块雷达"，不是板块列表
        if card["today_limit_up_count"] > 0:
            out_sectors.append(card)

    out_sectors = sort_sectors(out_sectors)[:max_sectors]

    return {
        "trade_date": trade_date.isoformat(),
        "summary": _build_summary(details, broken, out_sectors),
        "sectors": out_sectors,
        "warnings": [],
    }


def _build_sector_card(
    sector: Sector, sids: Set[int],
    detail_by_sid: Dict[int, LimitUpDailyDetail], broken_sids: Set[int],
    stocks: Dict[int, Stock], snaps: Dict[int, StockDailySnapshot],
    rel_lookup: Dict[Tuple[int, int], StockSectorRelation],
    *, include_core: bool,
    core_10d_min: int, core_20d_min: int, core_60d_min: int, core_max_board_min: int,
) -> dict:
    today_rows: List[dict] = []
    core_rows: List[dict] = []
    broken_in_sector = 0

    for sid in sids:
        st = stocks.get(sid)
        if not st:
            continue
        rel = rel_lookup.get((sid, sector.id))
        recall = recall_core_roles(
            st, rel,
            core_10d_min=core_10d_min, core_20d_min=core_20d_min,
            core_60d_min=core_60d_min, core_max_board_min=core_max_board_min,
        )
        detail = detail_by_sid.get(sid)

        if detail is not None:
            # 今日涨停股：同时带上它的核心角色标签，这样"历史核心今天也涨停了"
            # （最强的共振信号）能被一眼看出来，而不是被拆到两个区域里看不出关系
            today_rows.append({
                "code": st.code, "name": st.name,
                "board_count": detail.board_count,
                "limit_stat_days": detail.limit_stat_days,
                "limit_stat_count": detail.limit_stat_count,
                "first_limit_time": detail.first_limit_time,
                "last_limit_time": detail.last_limit_time,
                "seal_amount": detail.seal_amount,
                "broken_times": detail.broken_times,
                "pct_change": detail.pct_change,
                "price": detail.price,
                "turnover_rate": detail.turnover_rate,
                "limit_reason": detail.limit_reason,
                "limit_content": detail.limit_content,
                "limit_up_days_10d": st.limit_up_days_10d,
                "core_roles": recall.roles,
                "core_reasons": recall.reasons,
            })
            continue

        if sid in broken_sids:
            broken_in_sector += 1

        if include_core and recall.roles:
            snap = snaps.get(sid)
            core_rows.append({
                "code": st.code, "name": st.name,
                "core_roles": recall.roles,
                "core_reasons": recall.reasons,
                "primary_role": recall.primary_role,
                "pct_change": snap.pct_change if snap else None,
                "limit_up_days_10d": st.limit_up_days_10d,
                "limit_up_days_20d": st.limit_up_days_20d,
                "limit_up_days_60d": st.limit_up_days_60d,
                "board_count_60d": st.board_count_60d,
                "leader_score": st.leader_score,
                "is_broken_today": sid in broken_sids,
            })

    today_rows = sort_today_stocks(today_rows)
    core_rows = sort_core_stocks(core_rows)

    boards = [r["board_count"] or 0 for r in today_rows]
    continuation = sum(1 for b in boards if b >= 2)
    first_board = sum(1 for b in boards if b == 1)
    ladder: Dict[int, int] = {}
    for b in boards:
        if b:
            ladder[b] = ladder.get(b, 0) + 1

    times = [r["first_limit_time"] for r in today_rows if r["first_limit_time"]]
    seals = [r["seal_amount"] for r in today_rows if r["seal_amount"] is not None]
    core_pcts = [r["pct_change"] for r in core_rows if r["pct_change"] is not None]

    lu_n = len(today_rows)
    seal_rate = round(lu_n / (lu_n + broken_in_sector) * 100, 1) if (lu_n + broken_in_sector) else None

    return {
        "sector_id": sector.id,
        "sector_name": sector.name,
        "sector_phase": sector.phase,
        "today_limit_up_count": lu_n,
        "continuation_count": continuation,
        "first_board_count": first_board,
        "board_height": max(boards) if boards else 0,
        "board_ladder": [{"board": b, "count": c} for b, c in sorted(ladder.items(), reverse=True)],
        "broken_count": broken_in_sector,
        "seal_rate": seal_rate,
        "earliest_limit_time": min(times) if times else None,
        # 封单额只对"东财给了封单额的那些股票"求和；一只都没给时返回 None 而不是
        # 0——0 会被读成"没有资金排队"，跟"不知道"完全是两回事
        "total_seal_amount": sum(seals) if seals else None,
        "seal_amount_known_count": len(seals),
        "core_count": len(core_rows),
        # 核心锚今日平均涨跌幅：判断老核心是正反馈还是负反馈的关键事实
        "core_avg_pct_change": round(sum(core_pcts) / len(core_pcts), 2) if core_pcts else None,
        "core_stocks": core_rows,
        "today_limit_up_stocks": today_rows,
    }


def _build_summary(
    details: List[LimitUpDailyDetail], broken: List[BrokenBoardDailyDetail],
    sectors: List[dict],
) -> dict:
    boards = [d.board_count or 0 for d in details]
    lu_n, bb_n = len(details), len(broken)
    return {
        "limit_up_count": lu_n,
        "continuation_count": sum(1 for b in boards if b >= 2),
        "first_board_count": sum(1 for b in boards if b == 1),
        "board_height": max(boards) if boards else 0,
        "broken_count": bb_n,
        "seal_rate": round(lu_n / (lu_n + bb_n) * 100, 1) if (lu_n + bb_n) else None,
        "active_sector_count": len(sectors),
    }


def _empty_result(trade_date: date, warnings: Optional[List[str]] = None) -> dict:
    return {
        "trade_date": trade_date.isoformat(),
        "summary": {
            "limit_up_count": 0, "continuation_count": 0, "first_board_count": 0,
            "board_height": 0, "broken_count": 0, "seal_rate": None,
            "active_sector_count": 0,
        },
        "sectors": [],
        "warnings": warnings or [],
    }
