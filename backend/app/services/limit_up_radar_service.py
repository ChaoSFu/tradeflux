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
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from ..models.limit_up_detail import BrokenBoardDailyDetail, LimitUpDailyDetail
from ..models.sector import Sector, StockSectorRelation
from ..models.stock import Stock, StockDailySnapshot
from .eastmoney_fetcher import CoreRecallDetail
from .limit_up_detail_service import get_core_recall_details, get_radar_scores

# ── Core Recall 阈值（集中在这里，不散落在逻辑里）────────────────────────────
# 全部是"召回"阈值：满足任意一条就进入视野。刻意放宽，见模块 docstring 红线1。
DEFAULT_CORE_10D_MIN = 2        # 近10日涨停次数
DEFAULT_CORE_20D_MIN = 3        # 近20日涨停次数
DEFAULT_CORE_60D_MIN = 5        # 近60日涨停次数
DEFAULT_CORE_MAX_BOARD_MIN = 3  # 近60日最高连板数

# 每个板块最多展示几只核心锚。这是**展示上限**，不是召回上限——core_count 始终是
# 召回到的真实数量。宽召回下一个大行业板块可能召回30+只核心，全部铺在卡片上没法看；
# 排序已经把板块龙头/历史最活跃的排在最前，截断的是长尾。
DEFAULT_MAX_CORE_PER_SECTOR = 8

# 板块入选门槛（2026-08-25 按用户要求新增）：必须**同时**满足涨停只数和连板高度。
# 目的是"只看当前最强的板块和可能成为最强的板块"——生产上活跃板块有上百个，绝大
# 多数是"涨停2只、最高1板"的噪音（互联网金融/零售概念/生物疫苗这类），铺在页面上
# 会把真正在形成集团进攻的板块淹掉。
# 注意这是 AND：涨停多但全是首板（如基础化工涨停7/最高1板）也会被滤掉。这是用户
# 明确要的取舍——首板扎堆更可能是普涨或题材扩散，有高板才说明有资金愿意接力。
# 两个阈值都可以用查询参数放宽，被滤掉的数量会在响应里返回并显示在页面上。
DEFAULT_MIN_LIMIT_UP = 3
DEFAULT_MIN_BOARD_HEIGHT = 3

# 角色标签优先级（数字小=优先展示）。纯粹反映"最近有多活跃"，不是强弱排名。
_ROLE_PRIORITY = {
    "CURRENT_CORE": 0,     # 近10日还在涨停 —— 当前正在起作用的核心
    "RECENT_CORE": 1,      # 近20日活跃 / 打出过高连板 —— 近期核心
    "SECTOR_LEADER": 2,    # 板块龙头标记
    "SECTOR_CORE": 3,      # 板块核心标记
    "HISTORICAL_CORE": 4,  # 只有60日窗口才够得着 —— 历史核心/情绪锚
}


@dataclass
class LimitUpHistory:
    """
    某只股票截至某个交易日的涨停历史（现算，不读 Stock 上的冻结字段）。
    counts: {窗口天数: 该窗口内涨停次数}
    """
    counts: Dict[int, int] = field(default_factory=dict)
    max_consecutive_60d: int = 0


def _trading_days(db: Session, trade_date: date, limit: int = 60) -> List[date]:
    """
    截至 trade_date 的最近 N 个交易日，倒序。

    交易日历从 stock_daily_snapshots 里出现过的 distinct 日期反推——daily_update
    每个交易日会写几百行快照，任何出现在这张表里的日期都是真实交易日。本仓库还
    没有独立交易日历，这跟 daily_update 里 target_date/prev_trading_date 的推导
    方式是同一套（见那边的注释）。
    """
    rows = (
        db.query(StockDailySnapshot.date)
        .filter(StockDailySnapshot.date <= trade_date)
        .distinct()
        .order_by(StockDailySnapshot.date.desc())
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows]


def _business_days_between(a: date, b: date) -> int:
    """a→b 之间的工作日数（粗略，不含节假日）。只用来判断"快照落后了几天"。"""
    if b <= a:
        return 0
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def assess_history_freshness(days: List[date], trade_date: date) -> Tuple[Optional[date], int, List[str]]:
    """
    评估滚动窗口所依据的快照历史有多新，返回 (history_as_of, 落后工作日数, 警告)。

    为什么必须显式检查（2026-08-25 用户提出）：整个"近10/20/60日涨停次数"是按
    stock_daily_snapshots 里出现过的交易日切窗口的。如果 daily_update 已经好几天
    没跑，这个日历本身就是旧的——"近10日"实际变成"截至N天前的10日"，页面照样给出
    一个精确的数字，但那个数字回答的不是用户以为的那个问题。这跟九安医疗那个
    冻结字段的bug是同一类错误：**算得出来 ≠ 算的是对的问题**，而且都不报错。

    盘中的正常情况（今天的 daily_update 还没跑）会落后1个工作日，这是符合预期的：
    窗口自然落在"截至上一个完整交易日"，今天的涨停在 limit_up_daily_details 里
    单独展示，不混进历史窗口。所以只有落后≥2个工作日才告警。
    """
    if not days:
        return None, 0, [
            "本地没有任何历史快照，近10/20/60日涨停次数无法计算，"
            "板块核心锚会大面积漏召回——请先运行一次「每日数据更新」"
        ]
    as_of = days[0]
    lag = _business_days_between(as_of, trade_date)
    warnings: List[str] = []
    if lag >= 2:
        warnings.append(
            f"历史快照最新只到 {as_of}，比 {trade_date} 落后约 {lag} 个交易日："
            f"近10/20/60日涨停次数是按截至 {as_of} 的窗口算的，不是截至 {trade_date}。"
            f"请运行「每日数据更新」后再看核心锚部分。"
        )
    return as_of, lag, warnings


def compute_limit_up_history(
    db: Session, stock_ids: Set[int], trade_date: date, windows: Tuple[int, ...] = (10, 20, 60),
    days: Optional[List[date]] = None,
) -> Dict[int, LimitUpHistory]:
    """
    现算每只股票近 10/20/60 交易日的涨停次数与最高连板数。

    ── 为什么不能直接用 Stock.limit_up_days_10d/20d/60d ──────────────────────
    那几个字段只在 daily_update 处理**候选池内**股票时才重算（强势池∪涨跌停∪
    成交额前列）。一只股票冷却后掉出候选池，这些值就冻结在它最后一次入池那天，
    而且每过一天就更失真一点。

    生产实测（002432 九安医疗，2026-08-25）：页面显示"近10日涨停2次·近20日涨停
    5次·近60日涨停9次"，实际拉K线数出来是 0/1/7。它的快照停在 2026-07-31——正是
    它最后一次涨停那天，那三个数字是7月31日算出来的，冻结了17个交易日。

    这对涨停板块雷达是致命的：冻结值是"股票最热的时候"算的，必然偏高；而 Core
    Recall 恰恰是设计来捞"已经冷却的老核心"，最需要准的那批股票正好是最不准的
    那批。结果是过度召回，而且展示给用户的召回理由是一句关于市场的假陈述。

    ── 为什么从快照重算是可靠的 ──────────────────────────────────────────
    非ST股只要当天涨停，就一定会被涨跌停选股 API 捞进候选池、一定会写当日快照。
    所以"某天没有快照"⇒"那天没涨停"，缺失本身就是有效信息，不需要它当天在池内。
    再并上 limit_up_daily_details（东财涨停池，全市场完整名单，覆盖ST股这个
    选股API的盲区），覆盖面比任何单一来源都全。
    """
    out: Dict[int, LimitUpHistory] = {sid: LimitUpHistory() for sid in stock_ids}
    if not stock_ids:
        return out

    if days is None:
        days = _trading_days(db, trade_date, limit=max(windows))
    if not days:
        return out
    window_start = days[-1]
    # {窗口天数: 该窗口覆盖的交易日集合}
    day_sets = {n: set(days[:n]) for n in windows}

    lu_dates: Dict[int, Set[date]] = {sid: set() for sid in stock_ids}

    # 只按日期窗口 + is_limit_up 过滤，**不带 stock_id IN (...)**：关注板块的成员
    # 加起来是几千只，几千个 id 的 IN 子句比全市场涨停行本身还大。全市场一天涨停
    # 也就几十只，60个交易日总共几千行，全量拉回来在内存里筛更快。
    for sid, d in (
        db.query(StockDailySnapshot.stock_id, StockDailySnapshot.date)
        .filter(StockDailySnapshot.date >= window_start,
                StockDailySnapshot.date <= trade_date,
                StockDailySnapshot.is_limit_up == True)  # noqa: E712
        .all()
    ):
        if sid in lu_dates:
            lu_dates[sid].add(d)

    # 东财涨停池：全市场完整名单，能补上选股API漏掉的ST股
    for sid, d in (
        db.query(LimitUpDailyDetail.stock_id, LimitUpDailyDetail.trade_date)
        .filter(LimitUpDailyDetail.trade_date >= window_start,
                LimitUpDailyDetail.trade_date <= trade_date)
        .all()
    ):
        if sid in lu_dates:
            lu_dates[sid].add(d)

    for sid, dates in lu_dates.items():
        hist = out[sid]
        for n in windows:
            hist.counts[n] = len(dates & day_sets[n])
        # 最高连板：按交易日顺序走一遍，连续命中才算连板（跨越非交易日不算断）
        run = best = 0
        for d in reversed(days):          # days 是倒序，reversed 后是时间正序
            if d in dates:
                run += 1
                best = max(best, run)
            else:
                run = 0
        hist.max_consecutive_60d = best
    return out


def _lu_count(em, hist: Optional[LimitUpHistory], window: int, fallback) -> Optional[int]:
    """展示用的涨停次数。优先级同 recall_core_roles：东财 > 本地重算 > 冻结字段。"""
    if em is not None:
        v = {10: em.limit_up_days_10d, 20: em.limit_up_days_20d, 60: em.limit_up_days_60d}[window]
        if v is not None:
            return v
    return hist.counts.get(window, 0) if hist is not None else fallback


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
    hist: Optional[LimitUpHistory] = None,
    em: Optional[CoreRecallDetail] = None,
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

    hist 传入时用现算的涨停历史（compute_limit_up_history），拿不到才退回 Stock 上
    的字段。**必须优先用现算的**：Stock 上那几个字段只对候选池内的股票是最新的，
    冷却掉出池的股票会冻结在最后一次入池那天，而那正是本函数要捞的目标群体。
    详见 compute_limit_up_history 的注释和那里记录的生产实测案例。
    """
    out = CoreRecall()

    def hit(role: str, reason: str) -> None:
        if role not in out.roles:
            out.roles.append(role)
        out.reasons.append(reason)

    # 优先级：东财条件选股 > 本地快照重算 > Stock 上的冻结字段。
    # 东财是服务端实时算的、跟真实K线逐项核对过全对；本地重算受快照缺口影响会偏低
    # （实测600664少3次）；Stock 上的字段对候选池外的股票是冻结的旧值（就是九安医疗
    # 那个bug）。三者都可能拿不到，所以逐级兜底。
    # 两个来源数的**不是同一个指标**，文案必须跟着变（2026-08-25核实）：
    #   东财 DURATION_LIMIT_UP = 当日曾触及涨停的天数，**含炸板**。
    #     603580 艾艾精工近60日：收盘涨停15天 + 盘中触板未封4天 = 19，东财正好报19。
    #   本地快照重算 = 收盘涨停天数（跟K线口径一致）。
    # 用东财做召回是对的（更宽 ⇒ 召回更全，符合"不能漏掉核心"这条第一原则），
    # 但显示成"近60日涨停19次"会被读成19次收盘涨停，所以标注"曾涨停…含炸板"。
    verb = "涨停"
    if em is not None and em.limit_up_days_60d is not None:
        lu10 = em.limit_up_days_10d or 0
        lu20 = em.limit_up_days_20d or 0
        lu60 = em.limit_up_days_60d or 0
        max_board = em.max_board_60d or 0
        verb = "曾涨停"
    elif hist is not None:
        lu10 = hist.counts.get(10, 0)
        lu20 = hist.counts.get(20, 0)
        lu60 = hist.counts.get(60, 0)
        max_board = hist.max_consecutive_60d
    else:
        lu10 = stock.limit_up_days_10d or 0
        lu20 = stock.limit_up_days_20d or 0
        lu60 = stock.limit_up_days_60d or 0
        max_board = stock.board_count_60d or 0

    suffix = "（含炸板）" if verb == "曾涨停" else ""
    if lu10 >= core_10d_min:
        hit("CURRENT_CORE", f"近10日{verb}{lu10}次{suffix}")
    if lu20 >= core_20d_min:
        hit("RECENT_CORE", f"近20日{verb}{lu20}次{suffix}")
    if max_board >= core_max_board_min:
        hit("RECENT_CORE", f"近60日最高{max_board}连板")
    if lu60 >= core_60d_min:
        hit("HISTORICAL_CORE", f"近60日{verb}{lu60}次{suffix}")
    if rel is not None and rel.is_leader:
        hit("SECTOR_LEADER", "板块龙头")
    if rel is not None and rel.is_core:
        hit("SECTOR_CORE", "板块核心")

    # 东财条件选股兜底：本地重算是从快照数涨停日，而快照只在股票进候选池那天才写，
    # 历史缺口会让重算偏低（生产实测覆盖率94.3%，600664哈药股份近60日真实9次、
    # 快照里只有6次）。偏低就是漏召回，而"不能漏掉板块核心"是这个功能的第一原则。
    # 东财那边是服务端实时算的，不依赖我们的历史完整性。
    # 只在本地一条都没命中时才用它兜底——本地有命中时用本地的具体次数当理由，
    # 那是可验证的事实；这条兜底给不出次数，只能说"东财判定近期活跃"。
    # 强势股池成员无条件召回（2026-08-25）。强势股池本身就是"选龙头"用的，它的
    # prompt 里有一条"近20个交易日涨幅前10"是纯趋势、不含涨停，上面几条按涨停次数
    # 的条件覆盖不到。用本地的 in_strong_pool 保证包含关系，好过在两个自然语言
    # prompt 之间维护同步——那才是真正会漂移的地方（见 CORE_RECALL_KEYWORD 注释）。
    if stock.in_strong_pool and not out.roles:
        hit("RECENT_CORE", "在强势股池内")
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
    板块排序（2026-08-25 按用户要求调整为**高度优先**）：
      最高连板 DESC → 今日涨停数 DESC → 连板股数量 DESC → 最早首封时间 ASC → 总封单额 DESC

    先看板块打出了多高的板，同样高度再比涨停只数。理由：连板高度是板块**情绪级别**
    的直接体现——一个出了5板龙头的板块，即使只有5只涨停，也比10只清一色首板的板块
    更值得先看；首板扎堆更可能是普涨或题材扩散，高板才代表有资金愿意接力。
    （原来是"涨停数优先"，会把一堆首板齐发的板块排在有高位龙头的板块前面。）

    刻意不做 0.4*涨停数+0.3*板高+... 这种加权总分：用户需要能一眼说清"为什么这个
    板块排在前面"，加权分做不到这一点。
    """
    return sorted(sectors, key=lambda s: (
        -s["board_height"],
        -s["today_limit_up_count"],
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
    max_core_per_sector: int = DEFAULT_MAX_CORE_PER_SECTOR,
    min_limit_up: int = DEFAULT_MIN_LIMIT_UP,
    min_board_height: int = DEFAULT_MIN_BOARD_HEIGHT,
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

    # 现算涨停历史（不读 Stock 上的冻结字段，理由见 compute_limit_up_history）。
    # 一次性批量算完所有需要的股票，不在板块循环里逐只查。交易日历只查一次，
    # 顺便评估它有多新——日历本身过期时算出来的窗口是错的，必须显式告知而不是
    # 照样给一个精确但答非所问的数字。
    em_recall = get_core_recall_details(db, trade_date)
    # 页面作用域的龙头分/风险分（刷新按钮现算的，见 refresh_radar_scores）。
    # 优先级高于 Stock 上的值——后者只对候选池内股票是最新的。
    radar_scores = get_radar_scores(db, trade_date)
    trading_days = _trading_days(db, trade_date, limit=60)
    history_as_of, history_lag, freshness_warnings = assess_history_freshness(trading_days, trade_date)
    lu_hist = (compute_limit_up_history(db, need_sids, trade_date, days=trading_days)
               if need_sids else {})

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
            sector, sids, detail_by_sid, broken_sids, stocks, snaps, rel_lookup, lu_hist,
            em_recall, radar_scores,
            include_core=include_core,
            core_10d_min=core_10d_min, core_20d_min=core_20d_min,
            core_60d_min=core_60d_min, core_max_board_min=core_max_board_min,
            max_core_per_sector=max_core_per_sector,
        )
        # 今天一只涨停都没有的板块不进雷达——这是"涨停板块雷达"，不是板块列表
        if card["today_limit_up_count"] > 0:
            out_sectors.append(card)

    # 门槛过滤：必须同时满足涨停只数和连板高度。被滤掉的数量要返回给页面显示，
    # 不能悄悄丢——用户得能看出"是不是把想看的板块也滤掉了"。
    total_with_limit_up = len(out_sectors)
    out_sectors = [
        c for c in out_sectors
        if c["today_limit_up_count"] >= min_limit_up and c["board_height"] >= min_board_height
    ]
    hidden = total_with_limit_up - len(out_sectors)

    out_sectors = sort_sectors(out_sectors)[:max_sectors]

    return {
        "trade_date": trade_date.isoformat(),
        # 滚动窗口实际算到哪一天。盘中今天的 daily_update 还没跑时它会是上一个
        # 交易日，这是符合预期的——今天的涨停在"今日攻击"里单独展示，不混进历史窗口。
        "history_as_of": history_as_of.isoformat() if history_as_of else None,
        "history_lag_days": history_lag,
        "filter_min_limit_up": min_limit_up,
        "filter_min_board_height": min_board_height,
        "hidden_sector_count": hidden,
        "summary": _build_summary(details, broken, out_sectors),
        "sectors": out_sectors,
        "warnings": freshness_warnings,
    }


def _build_sector_card(
    sector: Sector, sids: Set[int],
    detail_by_sid: Dict[int, LimitUpDailyDetail], broken_sids: Set[int],
    stocks: Dict[int, Stock], snaps: Dict[int, StockDailySnapshot],
    rel_lookup: Dict[Tuple[int, int], StockSectorRelation],
    lu_hist: Dict[int, LimitUpHistory],
    em_recall: Dict[str, CoreRecallDetail],
    radar_scores: Dict[str, dict],
    *, include_core: bool,
    core_10d_min: int, core_20d_min: int, core_60d_min: int, core_max_board_min: int,
    max_core_per_sector: int = DEFAULT_MAX_CORE_PER_SECTOR,
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
            st, rel, lu_hist.get(sid), em_recall.get(st.code),
            core_10d_min=core_10d_min, core_20d_min=core_20d_min,
            core_60d_min=core_60d_min, core_max_board_min=core_max_board_min,
        )
        detail = detail_by_sid.get(sid)

        _em = em_recall.get(st.code)
        _sc = radar_scores.get(st.code)
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
                "limit_up_days_10d": _lu_count(_em, lu_hist.get(sid), 10, st.limit_up_days_10d),
                "limit_up_days_20d": _lu_count(_em, lu_hist.get(sid), 20, st.limit_up_days_20d),
                "limit_up_days_60d": _lu_count(_em, lu_hist.get(sid), 60, st.limit_up_days_60d),
                "board_count_60d": (_em.max_board_60d if _em and _em.max_board_60d is not None
                                    else st.board_count_60d),
                "interval_chg_10d": _em.interval_chg_10d if _em else None,
                "interval_chg_20d": _em.interval_chg_20d if _em else None,
                "interval_chg_60d": _em.interval_chg_60d if _em else None,
                "scores_as_of_today": True,   # 今日涨停股必然进候选池，本轮一定算过
                "leader_score": (_sc["ls"] if _sc else st.leader_score),
                "risk_score": (_sc["rs"] if _sc else st.risk_score),
                "core_roles": recall.roles,
                "core_reasons": recall.reasons,
            })
            continue

        if sid in broken_sids:
            broken_in_sector += 1

        if include_core and recall.roles:
            snap = snaps.get(sid)
            em_d = _em
            core_rows.append({
                "code": st.code, "name": st.name,
                "core_roles": recall.roles,
                "core_reasons": recall.reasons,
                "primary_role": recall.primary_role,
                # 今日涨跌幅：当日快照优先；核心锚大多不在候选池、盘中没有快照，
                # 这时用东财选股回传的 CHG 兜底——这正是"老核心正/负反馈"这个
                # 页面最重要结论此前在盘中拿不到的原因
                "pct_change": (snap.pct_change if snap and snap.pct_change is not None
                               else (em_d.pct_change if em_d else None)),
                "limit_up_days_10d": _lu_count(em_d, lu_hist.get(sid), 10, st.limit_up_days_10d),
                "limit_up_days_20d": _lu_count(em_d, lu_hist.get(sid), 20, st.limit_up_days_20d),
                "limit_up_days_60d": _lu_count(em_d, lu_hist.get(sid), 60, st.limit_up_days_60d),
                "board_count_60d": ((em_d.max_board_60d if em_d and em_d.max_board_60d is not None
                                     else (lu_hist[sid].max_consecutive_60d if sid in lu_hist
                                           else st.board_count_60d))),
                # 区间涨幅只用东财的（真实复合区间收益）。Stock.pct_change_Nd 有两个
                # 问题：对候选池外的股票是冻结旧值；而且算法是"日涨幅简单相加"的近似，
                # 大涨股票严重低估（603580近60日：真实204.85% vs 相加123.14%）。
                "interval_chg_10d": em_d.interval_chg_10d if em_d else None,
                "interval_chg_20d": em_d.interval_chg_20d if em_d else None,
                "interval_chg_60d": em_d.interval_chg_60d if em_d else None,
                # 龙头分/风险分是本仓库自己算的，只在 daily_update 处理候选池内股票时
                # 更新。核心锚大多在池外，那里的分数是冻结旧值——用"今天有没有当日
                # 快照"判断它是不是本轮真的算过，没算过就给 None，页面显示 —。
                # 这跟九安医疗那个冻结字段bug是同一类，不能因为字段有值就当它是当期的。
                # 刷新按钮现算的分数优先；没有就退回 Stock，且只在该股今天真的进过
                # 候选池（有当日快照）时才敢用，否则是冻结旧值 → None，页面显示 —
                "scores_as_of_today": bool(_sc or snap),
                "leader_score": (_sc["ls"] if _sc else (st.leader_score if snap else None)),
                "risk_score": (_sc["rs"] if _sc else (st.risk_score if snap else None)),
                "is_broken_today": sid in broken_sids,
            })

    today_rows = sort_today_stocks(today_rows)
    core_rows = sort_core_stocks(core_rows)
    core_total = len(core_rows)
    # 核心锚今日涨跌幅取自当日 StockDailySnapshot。**在当天 daily_update 跑完之前
    # 这个快照还不存在**（盘中手动刷新正是这种情况），此时 pct_change 全是 None，
    # 平均值也只能是 None——不拿昨天的收盘涨幅顶today，那回答不了"老核心今天是
    # 正反馈还是负反馈"这个问题。页面据此显示"待当日数据更新"，不显示成 0.00%。
    core_pct_known = sum(1 for r in core_rows if r["pct_change"] is not None)
    core_rows = core_rows[:max_core_per_sector]     # 展示截断，core_count 仍是真实总数

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
        "core_count": core_total,
        "core_shown_count": len(core_rows),
        "core_pct_known_count": core_pct_known,
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
        "history_as_of": None,
        "history_lag_days": 0,
        "filter_min_limit_up": DEFAULT_MIN_LIMIT_UP,
        "filter_min_board_height": DEFAULT_MIN_BOARD_HEIGHT,
        "hidden_sector_count": 0,
        "summary": {
            "limit_up_count": 0, "continuation_count": 0, "first_board_count": 0,
            "board_height": 0, "broken_count": 0, "seal_rate": None,
            "active_sector_count": 0,
        },
        "sectors": [],
        "warnings": warnings or [],
    }
