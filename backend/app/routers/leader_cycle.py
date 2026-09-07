"""高标龙头生命周期 —— 事实层接口。"""
import json
import os
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models.leader_cycle import LeaderCycleSnapshot
from ..models.sector import Sector
from ..models.stock import Stock
from ..services.leader_cycle_state_service import (
    CORE_OBSERVATION, FORMULA_VERSION, UNKNOWN, replay_price_lifecycle,
)

# app/routers/x.py → backend/
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

router = APIRouter(prefix="/leader-cycle", tags=["leader-cycle"])


class LeaderCycleItem(BaseModel):
    code: str
    name: Optional[str] = None
    sector_name: Optional[str] = None

    peak_board_count: Optional[int] = None      # 本轮周期最高连板
    board_count_60d: Optional[int] = None       # 60日最高（历史辨识度，另存不混用）
    cycle_start_date: Optional[date] = None
    cycle_peak_date: Optional[date] = None
    break_date: Optional[date] = None           # None = 仍在连板中
    days_since_break: Optional[int] = None

    peak_price: Optional[float] = None
    post_break_high: Optional[float] = None
    post_break_low: Optional[float] = None
    latest_close: Optional[float] = None
    peak_drawdown: Optional[float] = None

    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma30: Optional[float] = None
    ma_window_complete: Optional[bool] = None

    rs_market_10: Optional[float] = None
    rs_market_20: Optional[float] = None
    rs_market_60: Optional[float] = None
    rs_sector_10: Optional[float] = None
    rs_sector_20: Optional[float] = None
    rs_sector_60: Optional[float] = None

    volume: Optional[float] = None
    amount: Optional[float] = None
    turnover_rate: Optional[float] = None

    # ── 变化速度：截面看不出"正在变强"还是"已经强了很久"。RS20=+12 是从 -5 爬
    # 上来还是从 +30 掉下来的，含义完全相反。全部由相邻快照相减，缺一端就是 None
    rs_market_20_delta_1d: Optional[float] = None
    rs_market_20_delta_3d: Optional[float] = None
    rs_sector_20_delta_1d: Optional[float] = None
    dist_to_post_break_high: Optional[float] = None   # 离断板后阶段高点还有几 %
    dist_to_cycle_peak: Optional[float] = None        # 离原周期顶还有几 %
    # 三态：True=创了 / False=比过没创 / None=没有可比的历史（断板当天）
    new_post_break_high_today: Optional[bool] = None
    new_post_break_low_today: Optional[bool] = None
    volume_ratio_5d: Optional[float] = None
    amount_ratio_5d: Optional[float] = None

    # ── Price Lifecycle v1（派生，**不落库**）────────────────────────────────
    # 阈值以后一定会改。把 CROSS_SUCCESS 冻进历史事实表，换口径之后整段历史就
    # 变成旧口径数据，再也回答不了"新口径下当时该是什么状态"。所以每次从事实
    # replay，代价是几十只 × 60 天，可以忽略
    lifecycle_state: Optional[str] = None
    previous_lifecycle_state: Optional[str] = None
    # **最近一次判得出的状态**。盘前更新时 lifecycle_state 会是 UNKNOWN
    # （不能用盘中价推动跨日状态），但界面不该因此把已知的也丢掉——
    # 那时显示这个，并标明是截至哪一天
    last_valid_state: Optional[str] = None
    last_valid_date: Optional[date] = None
    # 在当前状态里待了几个交易日（转入当天 = 0）。按交易日历数，不数快照行数
    days_in_state: Optional[int] = None
    state_since_date: Optional[date] = None
    transitioned_today: bool = False
    lifecycle_formula_version: Optional[str] = None
    # 今天这一步的判定（状态没变时是 HOLD，只说明"今天没事发生"）
    transition_reason_codes: List[str] = []
    transition_reasons: List[str] = []
    # **当初为什么进入当前状态**。状态可能持续几十天，人想知道的是"它为什么在
    # 这儿"，不是"今天没事"——002742 在修复失败里挂着，今天的 reason 只有 HOLD
    entry_reason_codes: List[str] = []
    entry_reasons: List[str] = []
    evaluation_status: Optional[str] = None
    # CROSS_WEAKENING 必须能跟 CROSS_FAILED 区分开：前者是"曾经穿越成功、现在
    # 走弱"，后者是"这次修复就没成过"。交易含义完全不同
    ever_cross_success: bool = False
    first_cross_success_date: Optional[date] = None

    bar_count: Optional[int] = None      # 参与均线计算的 bar 根数
    # data_fresh = 那根 bar 是不是今天的；bar_settled = 那根 bar 是不是收盘终值。
    # 盘中两者会同时为真/假不同步，必须分开给——状态机只在两者都为真时才推进
    data_fresh: Optional[bool] = None
    bar_settled: Optional[bool] = None
    latest_bar_date: Optional[date] = None
    missing_days: Optional[int] = None
    peak_board_confident: Optional[bool] = None


class UnresolvedItem(BaseModel):
    """在强势池里、但本地识别不出 >=4 连板周期的股票。"""
    code: str
    name: Optional[str] = None
    board_count_60d: Optional[int] = None    # 本地重算的 60 日最高连板
    reason: str


class LeaderCycleResponse(BaseModel):
    trade_date: Optional[date] = None
    running: List[LeaderCycleItem]      # 仍在连板中（break_date 为空）
    broken: List[LeaderCycleItem]       # 已断板，按距断板交易日数升序
    # 在池子里但识别不出周期的。**不能让它们静默消失**——它们是东财召回口径与
    # 本地重算的差异，而这轮排查证明这类差异里绝大多数曾经是我们自己算错的
    unresolved: List[UnresolvedItem] = []
    # 覆盖率：每一项都是"这个事实我们掌握了多少"。**必须暴露给前端**——
    # 一个 63% 覆盖率的字段和一个 100% 的字段，读图的人有权知道区别。
    # 分母是**整个强势池**，不是"已识别出周期的那些"：用后者当分母是幸存者偏差，
    # 解析不出周期的股票直接从分母里消失，覆盖率看起来比实际好
    coverage: dict
    scope_note: str


def _lifecycle_fields(snaps, trade_date: date, calendar) -> dict:
    """把 replay 结果摊平成 LeaderCycleItem 的字段。"""
    st = replay_price_lifecycle(snaps, trade_date, trading_days=calendar)
    return {
        "lifecycle_state": st.state,
        "previous_lifecycle_state": st.previous_state,
        "last_valid_state": st.last_valid_state,
        "last_valid_date": st.last_valid_date,
        "days_in_state": st.days_in_state,
        "state_since_date": st.state_since_date,
        "transitioned_today": st.transitioned_today,
        "lifecycle_formula_version": st.formula_version,
        "transition_reason_codes": st.reason_codes,
        "transition_reasons": st.reasons,
        "entry_reason_codes": st.entry_reason_codes,
        "entry_reasons": st.entry_reasons,
        "evaluation_status": st.evaluation_status,
        "ever_cross_success": st.ever_cross_success,
        "first_cross_success_date": st.first_cross_success_date,
    }


@router.get("/effect")
def get_lifecycle_effect(
    trade_date: Optional[date] = Query(None, description="不传=最新有数据的交易日"),
    history_days: int = Query(60, ge=5, le=250),
    db: Session = Depends(get_db),
):
    """
    生命周期口径的赚钱效应：**昨天处于某状态的票，今天赚不赚钱**。

    强势股概览原来那四张卡按 `Stock.phase` 分组，那只是"收盘价在哪条均线下面"的
    单日快照——一只刚断板正在修复的票和一只连跌十天的老龙都可能被叫"震荡龙头"。
    换成生命周期分组后，同一张卡才回答得了有意义的问题。

    另带历史前瞻（过去 N 天该状态之后 T+1/T+3/T+5 的中位收益）。**那部分只是
    线索不是结论**：没做同日同池对照，也没有置信区间，返回的 notes 里写明了。
    """
    from ..services.leader_cycle_effect_service import compute_effect
    return compute_effect(db, trade_date, history_days)


@router.get("/evidence")
def get_lifecycle_evidence():
    """
    转移时点的**前瞻证据**，由 `scripts/evaluate_lifecycle.py --json` 离线生成。

    为什么读文件而不是实时算：那套评估要把每只票的每个交易日 replay 一遍，再按
    「股票×周期」整段 bootstrap 1000 次。不是一个 HTTP 请求能扛的量。

    **没跑过就如实说没跑过。** 返回空的 events 列表看起来像「跑过了，但一条证据
    都没有」——那是两件完全不同的事，界面会照着显示成后者。
    """
    raw = settings.LIFECYCLE_EVIDENCE_PATH
    path = raw if os.path.isabs(raw) else os.path.join(_BACKEND_DIR, raw)
    if not os.path.exists(path):
        return {"available": False, "path": raw,
                "reason": "还没跑过评估：python scripts/evaluate_lifecycle.py --json"}
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError) as e:
        # 文件坏了也别装作没有——「没跑过」和「跑了但产物读不了」要分开
        return {"available": False, "path": raw, "reason": f"产物读不出来：{e}"}

    payload["available"] = True
    payload["stale_formula"] = payload.get("formula_version") != FORMULA_VERSION
    payload["current_formula_version"] = FORMULA_VERSION
    try:
        payload["file_mtime"] = datetime.fromtimestamp(
            os.path.getmtime(path)).isoformat(timespec="seconds")
    except OSError:
        payload["file_mtime"] = None
    return payload


@router.get("", response_model=LeaderCycleResponse)
def get_leader_cycle(
    trade_date: Optional[date] = Query(None, description="不传=最新有数据的交易日"),
    db: Session = Depends(get_db),
):
    """
    高标龙头生命周期：**事实层 + 从事实 replay 出来的状态**。

    `running` / `broken` 仍按纯事实切（`break_date` 有没有），状态另挂在每个
    item 上——`lifecycle_state` 及其入场原因由 `leader_cycle_state_service`
    实时算出，**不写进 LeaderCycleSnapshot**：阈值以后一定会改，冻进事实表就
    再也回答不了「新口径下当时该是什么状态」。

    历史查询传 trade_date=T 时，replay 只读 `date <= T` 的行——这是 look-ahead
    guard，改了 T 之后的数据不能让 T 那天的状态发生变化。
    """
    if trade_date is None:
        trade_date = db.query(LeaderCycleSnapshot.date).order_by(
            LeaderCycleSnapshot.date.desc()).limit(1).scalar()
    if trade_date is None:
        return LeaderCycleResponse(trade_date=None, running=[], broken=[],
                                   coverage={}, scope_note="暂无数据")

    rows = db.query(LeaderCycleSnapshot).filter(
        LeaderCycleSnapshot.date == trade_date).all()
    if not rows:
        return LeaderCycleResponse(trade_date=trade_date, running=[], broken=[],
                                   coverage={}, scope_note="该交易日暂无数据")

    stocks = {s.code: s for s in db.query(Stock).filter(
        Stock.code.in_([r.stock_code for r in rows])).all()}

    # ── 生命周期 replay 用的历史 ──────────────────────────────────────────
    # **只取 date <= trade_date**。这是 look-ahead guard：查历史某天时，改了那天
    # 之后的数据不能让那天的状态发生变化
    hist_rows = (db.query(LeaderCycleSnapshot)
                 .filter(LeaderCycleSnapshot.date <= trade_date).all())
    hist: dict = {}
    for r in hist_rows:
        hist.setdefault(r.stock_code, []).append(r)
    # **交易日历必须来自 trading_calendar，不能用"库里有哪些日期"顶替。**
    # 那是同一个错误模式从"股票数组"上升到"数据库日期集合"：假如 09-02 整个
    # daily_update 挂了、一行快照都没写，日期集合就是 [09-01, 09-03]，两者会被
    # 判成相邻交易日——而它们中间隔着一个真实开市日。
    # **数据库有没有行，永远不能承担 calendar 的职责。**
    try:
        from ..services.trading_calendar import get_trading_days
        _tdays = get_trading_days(db, need_through=trade_date)
        obs_calendar = [d for d in (_tdays or []) if d <= trade_date]
    except Exception:  # noqa: BLE001
        obs_calendar = []
    if not obs_calendar:
        # 拿不到日历时**不退回日期集合**——那正是要防的东西。给空日历，状态机
        # 会因为证明不了相邻而停在原地，界面上看得出来；比悄悄跑出一堆
        # 看似合理的状态安全
        obs_calendar = []
    sec_names = {sid: name for sid, name in db.query(Sector.id, Sector.name).all()}

    items: List[LeaderCycleItem] = []
    for r in rows:
        st = stocks.get(r.stock_code)
        items.append(LeaderCycleItem(
            code=r.stock_code,
            name=st.name if st else None,
            sector_name=(sec_names.get(st.primary_sector_id)
                         if st and st.primary_sector_id else None),
            **_lifecycle_fields(hist.get(r.stock_code, []), trade_date, obs_calendar),
            **{k: getattr(r, k) for k in (
                "peak_board_count", "board_count_60d", "cycle_start_date",
                "cycle_peak_date", "break_date", "days_since_break",
                "peak_price", "post_break_high", "post_break_low", "latest_close",
                "peak_drawdown", "ma5", "ma10", "ma20", "ma30", "ma_window_complete",
                "rs_market_10", "rs_market_20", "rs_market_60",
                "rs_sector_10", "rs_sector_20", "rs_sector_60",
                "volume", "amount", "turnover_rate",
                "rs_market_20_delta_1d", "rs_market_20_delta_3d",
                "rs_sector_20_delta_1d", "dist_to_post_break_high",
                "dist_to_cycle_peak", "new_post_break_high_today",
                "new_post_break_low_today", "data_fresh", "bar_settled",
                "latest_bar_date",
                "volume_ratio_5d", "amount_ratio_5d",
                "bar_count", "missing_days", "peak_board_confident")},
        ))

    running = [i for i in items if i.break_date is None]
    broken = [i for i in items if i.break_date is not None]
    # 连板中：板数高者在前。已断板：离断板越近越靠前——那是结构还没走完的时候
    running.sort(key=lambda i: -(i.peak_board_count or 0))
    broken.sort(key=lambda i: (i.days_since_break if i.days_since_break is not None
                               else 10 ** 6, -(i.peak_board_count or 0)))

    # 分母用整个强势池，不是 len(items)。
    # **2026-09-04 起，识别不出周期的股票也有行**（周期字段整组为 NULL，
    # lifecycle_state = NO_CYCLE），所以 unresolved 现在只剩一种情况：连 K 线
    # 都没有、压根建不出行。它仍然要报出来——静默消失就永远查不出来
    pool = db.query(Stock).filter(Stock.in_strong_pool.is_(True)).all()
    resolved = {i.code for i in items}
    unresolved = sorted(
        (UnresolvedItem(
            code=st.code, name=st.name, board_count_60d=st.board_count_60d,
            reason=("本地没有 K 线，连价格事实都没有"))
         for st in pool if st.code not in resolved),
        key=lambda u: -(u.board_count_60d or 0))

    n = len(pool) or len(items)
    cov = {
        "pool_total": len(pool),
        "cycle_identified": sum(1 for i in items
                                if i.lifecycle_state != "NO_CYCLE"),
        "with_facts": len(items),          # 有价格事实的（含无周期的）
        "cycle_unresolved": (sum(1 for i in items if i.lifecycle_state == "NO_CYCLE")
                             + len(unresolved)),
        "total": n,
        "peak_board_confident": sum(1 for i in items if i.peak_board_confident),
        "ma_window_complete": sum(1 for i in items if i.ma_window_complete),
        "rs_market": sum(1 for i in items if i.rs_market_20 is not None),
        "rs_sector": sum(1 for i in items if i.rs_sector_20 is not None),
        "turnover_rate": sum(1 for i in items if i.turnover_rate is not None),
        "volume": sum(1 for i in items if i.volume is not None),
        "rs_delta": sum(1 for i in items if i.rs_market_20_delta_1d is not None),
        "settled": sum(1 for i in items if i.bar_settled and i.data_fresh),
        "lifecycle_resolved": sum(1 for i in items
                                  if i.lifecycle_state and i.lifecycle_state != UNKNOWN),
        # 含"截至上一个已结算交易日"的——盘前更新时前者会是 0，后者才反映
        # 我们实际掌握多少
        "lifecycle_known": sum(1 for i in items
                               if (i.lifecycle_state and i.lifecycle_state != UNKNOWN)
                               or i.last_valid_state),
    }
    return LeaderCycleResponse(
        trade_date=trade_date, running=running, broken=broken,
        unresolved=unresolved, coverage=cov,
        scope_note="高标池 = 近60个交易日最高连板 ≥ 4；不含 ST、退市整理期、北交所",
    )
