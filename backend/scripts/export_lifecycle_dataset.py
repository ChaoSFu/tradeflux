"""
导出高标池逐日数据集，交给外部分析（AI / 表格）研究「按生命周期怎么买卖更容易赚钱」。

    cd /opt/code/tradeflux/backend
    .venv/bin/python -m scripts.export_lifecycle_dataset                                   # 全部历史
    .venv/bin/python -m scripts.export_lifecycle_dataset --start 2026-07-01 --end 2026-11-18

输出到 backend/data/exports/lifecycle_<起>_<止>/，并打一个同名 .zip：
  stock_days.csv   一行 = 某个交易日高标池里的一只票。当天的生命周期状态与原因、周期事实、当天行情、
                   均线与强弱，以及 fut_ 开头的**未来标签**（之后 N 天的收益、最高最低、次日能不能买进）
  market_days.csv  一行 = 一个交易日。池子大小、各状态只数与新进只数、各状态当天赚钱效应、
                   强势股均涨幅、涨跌停家数、指数涨跌
  README.md        字段字典、状态与原因代码、口径与陷阱——给分析方的说明书

**只读，不写库。** 状态取自 /leader-cycle 同一个函数（按当天回放、只读当天及以前的数据），
所以跟页面上看到的一致；当前规则版本回放全部历史，不是当时页面显示过的值。
不导出情绪分 / 龙头分 / 风险分：那是自造的合成分，不该成为分析的输入。
"""
import argparse
import csv
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HORIZONS = (1, 3, 5, 10)
RANGE_HORIZONS = (5, 10)
# 收盘价比值算出的单日涨幅跟快照里的涨跌幅差这么多，就当作除权或数据问题（收盘价不复权）
ADJ_SUSPECT_PP = 1.5
INDEXES = (("000001", "上证指数"), ("399001", "深证成指"), ("399006", "创业板指"))

STATE_ZH = {
    "STREAKING": "连板中", "BROKEN": "刚断板", "REPAIRING": "修复中", "CROSS_SUCCESS": "穿越成功",
    "CROSS_WEAKENING": "成功后走弱", "CROSS_FAILED": "修复失败", "FADED": "周期结束",
    "UNKNOWN": "数据不足", "NO_CYCLE": "无有效周期",
}
STATES = tuple(STATE_ZH)

STATE_RULES = """\
- 连板中 STREAKING：本轮 ≥4 连板周期还没断板。
- 刚断板 BROKEN：断板当天进入。D+0、D+1 允许观望；到 D+2 还没修复就判修复失败。
- 修复中 REPAIRING：断板后至少 1 天，收盘站上 MA5，且 MA5 比前一交易日高（上行）。
- 穿越成功 CROSS_SUCCESS：修复中创断板后收盘新高，同时站上 MA5、MA10，且 MA5 上行。
- 成功后走弱 CROSS_WEAKENING：穿越成功后收盘跌破 MA10，或连续 2 天收在 MA5 下。重新站上 MA5、MA10 且 MA5 上行，回到穿越成功。
- 修复失败 CROSS_FAILED：修复中收盘跌破 MA10；或创断板后新低且跌破 MA5×97%；或连续 2 天在 MA5 下且 MA5 不上行；或刚断板超期。之后满足修复条件可回到修复中。
- 周期结束 FADED：断板后任一状态，连续 2 天收在 MA30 下，或连续 2 天收在 MA20 下且 MA5<MA10<MA20。对本轮周期是终点，出现新的 ≥4 连板才重置。
- 一天最多推进一步（周期结束例外）；当天行情未结算时不推进，标 UNKNOWN。"""

# (列名, 说明)。CSV 列顺序和 README 字段字典都从这里来，只有一份
STOCK_COLUMNS = [
    ("date", "交易日"),
    ("code", "股票代码"),
    ("name", "名称"),
    ("board", "板块类别：主板 / 创业板 / 科创板（决定涨跌停幅度 10% 或 20%）"),
    ("sector_name", "所属关注板块（主板块）"),
    ("state", "当天生命周期状态（UNKNOWN 已用最近一次有效状态顶上，跟页面「赚钱效应」同口径）"),
    ("state_zh", "state 的中文"),
    ("raw_state", "状态机当天原始输出（可能是 UNKNOWN = 当天事实不足以判断）"),
    ("previous_state", "进入当前状态之前的那个状态"),
    ("transitioned_today", "当天是否刚转入该状态（1/0）"),
    ("days_in_state", "在当前状态里待了几个交易日"),
    ("state_since_date", "进入当前状态的日期"),
    ("transition_reasons", "当天这一步的判定原因代码（| 分隔，含义见 README）"),
    ("entry_reasons", "当初进入当前状态的原因代码"),
    ("evaluation_status", "OK / UNSETTLED / STALE / INSUFFICIENT"),
    ("ever_cross_success", "本轮周期是否曾经穿越成功（1/0）"),
    ("first_cross_success_date", "本轮第一次穿越成功的日期"),
    ("peak_board_count", "本轮周期最高连板数"),
    ("board_count_60d", "近 60 日最高连板数"),
    ("cycle_start_date", "本轮连板起始日"),
    ("cycle_peak_date", "本轮连板最高板那天"),
    ("break_date", "断板日（空 = 仍在连板）"),
    ("days_since_break", "距断板几个交易日（D+N）"),
    ("peak_price", "本轮连板最高收盘价"),
    ("post_break_high", "断板后最高收盘价（截至当天）"),
    ("post_break_low", "断板后最低收盘价（截至当天）"),
    ("peak_drawdown_pct", "当天收盘相对本轮峰值的回撤 %"),
    ("dist_to_post_break_high_pct", "离断板后高点还有几 %"),
    ("dist_to_cycle_peak_pct", "离本轮周期顶还有几 %"),
    ("new_post_break_high_today", "当天创断板后收盘新高（1/0）"),
    ("new_post_break_low_today", "当天创断板后收盘新低（1/0）"),
    ("open", "当天开盘价（不复权）"),
    ("high", "当天最高价"),
    ("low", "当天最低价"),
    ("close", "当天收盘价"),
    ("pct_change", "当天涨跌幅 %（交易所口径，已处理除权）"),
    ("amount", "成交额（元）"),
    ("volume", "成交量（股）"),
    ("turnover_rate", "换手率 %（空 = 数据源没给，不是 0）"),
    ("is_limit_up", "当天收盘涨停（1/0）"),
    ("is_limit_down", "当天收盘跌停（1/0）"),
    ("is_broken_board", "当天炸板：盘中触及涨停但收盘没封住（1/0）"),
    ("is_one_word_limit_up", "一字涨停（1/0）"),
    ("is_one_word_limit_down", "一字跌停（1/0）"),
    ("board_count_now", "当前连续涨停数"),
    ("limit_up_days_10d", "近 10 日涨停天数"),
    ("limit_up_days_20d", "近 20 日涨停天数"),
    ("pct_change_5d", "近 5 日累计涨幅 %"),
    ("pct_change_10d", "近 10 日累计涨幅 %"),
    ("pct_change_20d", "近 20 日累计涨幅 %"),
    ("ma5", "5 日均线"), ("ma10", "10 日均线"), ("ma20", "20 日均线"), ("ma30", "30 日均线"),
    ("close_vs_ma5_pct", "收盘相对 MA5 %"),
    ("close_vs_ma10_pct", "收盘相对 MA10 %"),
    ("close_vs_ma20_pct", "收盘相对 MA20 %"),
    ("close_vs_ma30_pct", "收盘相对 MA30 %"),
    ("ma_window_complete", "均线所需历史是否齐全（1/0）"),
    ("rs_market_10", "相对大盘强弱，10 日（个股涨幅减大盘涨幅，百分点）"),
    ("rs_market_20", "相对大盘强弱，20 日"),
    ("rs_market_60", "相对大盘强弱，60 日"),
    ("rs_sector_10", "相对所属板块强弱，10 日"),
    ("rs_sector_20", "相对所属板块强弱，20 日"),
    ("rs_sector_60", "相对所属板块强弱，60 日"),
    ("rs_market_20_delta_1d", "rs_market_20 比 1 个交易日前的变化"),
    ("rs_market_20_delta_3d", "rs_market_20 比 3 个交易日前的变化"),
    ("rs_sector_20_delta_1d", "rs_sector_20 比 1 个交易日前的变化"),
    ("volume_ratio_5d", "成交量 / 前 5 日均量"),
    ("bar_settled", "当天行情是否已收盘结算（1/0）"),
    # ── 未来标签：只能当结果看，**不能当特征** ──
    ("fut_next_date", "【未来】下一个交易日"),
    ("fut_next_open_gap_pct", "【未来】次日开盘相对当天收盘 %"),
    ("fut_next_one_word_limit_up", "【未来】次日一字涨停，开盘买不进（1/0）"),
    ("fut_next_limit_up", "【未来】次日收盘涨停（1/0）"),
    ("fut_next_limit_down", "【未来】次日收盘跌停（1/0）"),
    *[(f"fut_ret_{h}d_pct", f"【未来】当天收盘买、第 {h} 个交易日收盘卖的收益 %") for h in HORIZONS],
    *[(f"fut_ret_open_{h}d_pct", f"【未来】次日开盘买、第 {h} 个交易日（从当天数）收盘卖的收益 %")
      for h in HORIZONS if h > 1],
    *[(f"fut_max_high_{h}d_pct", f"【未来】之后 {h} 个交易日最高价相对当天收盘 %（最大浮盈）")
      for h in RANGE_HORIZONS],
    *[(f"fut_min_low_{h}d_pct", f"【未来】之后 {h} 个交易日最低价相对当天收盘 %（最大浮亏）")
      for h in RANGE_HORIZONS],
    *[(f"fut_state_{h}d", f"【未来】第 {h} 个交易日的生命周期状态（空 = 那天已不在高标池）")
      for h in HORIZONS],
    ("fut_adj_suspect", "【未来】之后 10 天里收盘价比值跟涨跌幅对不上（疑似除权/数据问题），fut_ret 慎用（1/0）"),
]

MARKET_COLUMNS = [
    ("date", "交易日"),
    ("pool_size", "当天高标池只数（近 60 个交易日最高连板 ≥4，不含 ST、退市整理、北交所）"),
    *[(f"cnt_{s}", f"当天处于「{STATE_ZH[s]}」的只数") for s in STATES if s != "UNKNOWN"],
    *[(f"new_{s}", f"当天刚转入「{STATE_ZH[s]}」的只数") for s in STATES if s not in ("UNKNOWN", "NO_CYCLE")],
    *[(f"eff_{s}_avg_pct", f"昨天处于「{STATE_ZH[s]}」的票今天的平均涨幅 %（页面「逐日赚钱效应」那条线）")
      for s in STATES if s != "UNKNOWN"],
    *[(f"eff_{s}_n", f"上一列的样本数") for s in STATES if s != "UNKNOWN"],
    ("pool_avg_pct", "高标池全部票今天的平均涨幅 %"),
    ("strong_pool_avg_pct", "强势股池当日均涨幅 %（页面「强势股均涨幅」）"),
    ("strong_pool_up", "强势股池上涨家数"),
    ("strong_pool_down", "强势股池下跌家数"),
    ("strong_pool_limit_up", "强势股池涨停家数"),
    ("strong_pool_limit_down", "强势股池跌停家数"),
    ("tracked_limit_up", "跟踪股票（日快照覆盖的票，非 ST）涨停家数，同涨跌停分析页「历史走势」"),
    ("tracked_limit_down", "跟踪股票跌停家数"),
    *[(f"idx_{c}_pct", f"{n}涨跌幅 %") for c, n in INDEXES],
]


# ── 纯函数（有单测）──────────────────────────────────────────────────────────

def board_of(code: str) -> str:
    if code.startswith("68"):
        return "科创板"
    if code.startswith("30"):
        return "创业板"
    return "主板"


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """b 相对 a 的变化 %。任一缺失或 a<=0 → None（不拿 0 冒充）。"""
    if a is None or b is None or a <= 0:
        return None
    return round((b / a - 1) * 100, 4)


def forward_labels(bars: Dict[date, dict], close_t: Optional[float],
                   days_after: Sequence[date]) -> dict:
    """
    当天之后的结果标签。bars = {日期: {open, high, low, close, pct, lu, ld, one_up}}（这只票的日行情），
    days_after = 交易日历上当天之后的交易日（升序）。

    · 第 h 天没行（停牌或缺数）→ 那个 horizon 给空，不往前找一根顶替
    · 最高/最低：窗口里任何一天缺 high/low 就给空——少一天的极值不是极值
    · 交易日历还没走到第 h 天 → 空
    """
    out: dict = {}
    nxt = days_after[0] if days_after else None
    nb = bars.get(nxt) if nxt else None
    out["fut_next_date"] = nxt
    out["fut_next_open_gap_pct"] = _pct(close_t, nb.get("open")) if nb else None
    out["fut_next_one_word_limit_up"] = nb.get("one_up") if nb else None
    out["fut_next_limit_up"] = nb.get("lu") if nb else None
    out["fut_next_limit_down"] = nb.get("ld") if nb else None
    next_open = nb.get("open") if nb else None
    for h in HORIZONS:
        b = bars.get(days_after[h - 1]) if len(days_after) >= h else None
        out[f"fut_ret_{h}d_pct"] = _pct(close_t, b.get("close")) if b else None
        if h > 1:
            out[f"fut_ret_open_{h}d_pct"] = _pct(next_open, b.get("close")) if b else None
    for h in RANGE_HORIZONS:
        win = [bars.get(d) for d in days_after[:h]] if len(days_after) >= h else []
        ok = bool(win) and all(w and w.get("high") is not None and w.get("low") is not None for w in win)
        out[f"fut_max_high_{h}d_pct"] = _pct(close_t, max(w["high"] for w in win)) if ok else None
        out[f"fut_min_low_{h}d_pct"] = _pct(close_t, min(w["low"] for w in win)) if ok else None
    # 收盘价不复权：除权那天收盘价比值会凭空出现一个大跌。拿交易所的涨跌幅对一遍
    suspect, prev = False, close_t
    for d in days_after[:max(HORIZONS)]:
        b = bars.get(d)
        if not b or b.get("close") is None:
            prev = None             # 断了一天就不比了，比不出相邻
            continue
        r = _pct(prev, b["close"])
        if r is not None and b.get("pct") is not None and abs(r - b["pct"]) > ADJ_SUSPECT_PP:
            suspect = True
        prev = b["close"]
    out["fut_adj_suspect"] = suspect
    return out


def effective_state(item: dict) -> Optional[str]:
    st = item.get("lifecycle_state")
    return item.get("last_valid_state") if st == "UNKNOWN" else st


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (list, tuple)):
        return "|".join(map(str, v))
    return v


# ── 取数 ─────────────────────────────────────────────────────────────────────

def _load_bars(db, codes: Sequence[str], lo: date, hi: date) -> Dict[str, Dict[date, dict]]:
    from app.models.stock import Stock, StockDailySnapshot as S
    out: Dict[str, Dict[date, dict]] = defaultdict(dict)
    q = (db.query(Stock.code, S.date, S.open_price, S.high_price, S.low_price, S.close_price, S.pct_change,
                  S.amount, S.volume, S.turnover_rate, S.is_limit_up, S.is_limit_down, S.is_broken_board,
                  S.is_one_word_limit_up, S.is_one_word_limit_down, S.board_count, S.limit_up_days_10d,
                  S.limit_up_days_20d, S.pct_change_5d, S.pct_change_10d, S.pct_change_20d)
         .join(Stock, Stock.id == S.stock_id)
         .filter(Stock.code.in_(list(codes)), S.date >= lo, S.date <= hi))
    for r in q:
        out[r[0]][r[1]] = {
            "open": r[2], "high": r[3], "low": r[4], "close": r[5], "pct": r[6], "amount": r[7],
            "volume": r[8], "turnover": r[9], "lu": r[10], "ld": r[11], "broken": r[12], "one_up": r[13],
            "one_down": r[14], "board_now": r[15], "lu10": r[16], "lu20": r[17], "p5": r[18], "p10": r[19],
            "p20": r[20],
        }
    return out


def _market_extras(db, dates: Sequence[date]) -> Dict[date, dict]:
    from sqlalchemy import case, func
    from app.models.market_index import IndexDailySnapshot
    from app.models.review import DailyReview
    from app.models.stock import Stock, StockDailySnapshot as S
    out: Dict[date, dict] = defaultdict(dict)
    for r in db.query(DailyReview).filter(DailyReview.date.in_(list(dates))):
        out[r.date].update(strong_pool_avg_pct=r.strong_pool_avg_pct, strong_pool_up=r.overall_up_count,
                           strong_pool_down=r.overall_down_count, strong_pool_limit_up=r.overall_limit_up_count,
                           strong_pool_limit_down=r.overall_limit_down_count)
    q = (db.query(S.date, func.sum(case((S.is_limit_up.is_(True), 1), else_=0)),
                  func.sum(case((S.is_limit_down.is_(True), 1), else_=0)))
         .join(Stock, Stock.id == S.stock_id)
         .filter(Stock.is_st.is_(False), S.date.in_(list(dates))).group_by(S.date))
    for d, lu, ld in q:
        out[d].update(tracked_limit_up=int(lu or 0), tracked_limit_down=int(ld or 0))
    for r in db.query(IndexDailySnapshot).filter(
            IndexDailySnapshot.index_code.in_([c for c, _ in INDEXES]), IndexDailySnapshot.date.in_(list(dates))):
        out[r.date][f"idx_{r.index_code}_pct"] = r.pct_change
    return out


def _stock_row(d: date, it: dict, bar: Optional[dict], fut: dict, states_by_date, cal_after) -> dict:
    close = it.get("latest_close") if it.get("latest_close") is not None else (bar or {}).get("close")
    st = effective_state(it)
    b = bar or {}
    row = {
        "date": d, "code": it["code"], "name": it.get("name"), "board": board_of(it["code"]),
        "sector_name": it.get("sector_name"), "state": st, "state_zh": STATE_ZH.get(st or "", st),
        "raw_state": it.get("lifecycle_state"), "previous_state": it.get("previous_lifecycle_state"),
        "transitioned_today": it.get("transitioned_today"), "days_in_state": it.get("days_in_state"),
        "state_since_date": it.get("state_since_date"),
        "transition_reasons": it.get("transition_reason_codes"), "entry_reasons": it.get("entry_reason_codes"),
        "evaluation_status": it.get("evaluation_status"), "ever_cross_success": it.get("ever_cross_success"),
        "first_cross_success_date": it.get("first_cross_success_date"),
        "peak_drawdown_pct": it.get("peak_drawdown"),
        "dist_to_post_break_high_pct": it.get("dist_to_post_break_high"),
        "dist_to_cycle_peak_pct": it.get("dist_to_cycle_peak"),
        "open": b.get("open"), "high": b.get("high"), "low": b.get("low"), "close": close,
        "pct_change": b.get("pct"), "amount": b.get("amount") if b.get("amount") is not None else it.get("amount"),
        "volume": b.get("volume") if b.get("volume") is not None else it.get("volume"),
        "turnover_rate": it.get("turnover_rate") if it.get("turnover_rate") is not None else b.get("turnover"),
        "is_limit_up": b.get("lu"), "is_limit_down": b.get("ld"), "is_broken_board": b.get("broken"),
        "is_one_word_limit_up": b.get("one_up"), "is_one_word_limit_down": b.get("one_down"),
        "board_count_now": b.get("board_now"), "limit_up_days_10d": b.get("lu10"),
        "limit_up_days_20d": b.get("lu20"), "pct_change_5d": b.get("p5"), "pct_change_10d": b.get("p10"),
        "pct_change_20d": b.get("p20"),
        **{f"close_vs_{m}_pct": _pct(it.get(m), close) for m in ("ma5", "ma10", "ma20", "ma30")},
        **fut,
    }
    for k in ("peak_board_count", "board_count_60d", "cycle_start_date", "cycle_peak_date", "break_date",
              "days_since_break", "peak_price", "post_break_high", "post_break_low",
              "new_post_break_high_today", "new_post_break_low_today", "ma5", "ma10", "ma20", "ma30",
              "ma_window_complete", "rs_market_10", "rs_market_20", "rs_market_60", "rs_sector_10",
              "rs_sector_20", "rs_sector_60", "rs_market_20_delta_1d", "rs_market_20_delta_3d",
              "rs_sector_20_delta_1d", "volume_ratio_5d", "bar_settled"):
        row[k] = it.get(k)
    for h in HORIZONS:
        fd = cal_after[h - 1] if len(cal_after) >= h else None
        row[f"fut_state_{h}d"] = (states_by_date.get(fd) or {}).get(it["code"]) if fd else None
    return row


def _last_settled_date(db, LeaderCycleSnapshot) -> date:
    """最后一个已收盘结算的快照日。最新那天如果是盘中跑的（多数行未结算），往前退一天。"""
    from sqlalchemy import func
    for (d,) in (db.query(LeaderCycleSnapshot.date).distinct()
                 .order_by(LeaderCycleSnapshot.date.desc()).limit(5)):
        tot, ok = db.query(func.count(), func.sum(_case_settled(LeaderCycleSnapshot))).filter(
            LeaderCycleSnapshot.date == d).one()
        if tot and (ok or 0) * 2 >= tot:
            return d
    raise SystemExit("最近 5 个快照日都没有收盘结算")


def _case_settled(model):
    from sqlalchemy import case
    return case((model.bar_settled.is_(True), 1), else_=0)


def export(db, start: Optional[date], end: Optional[date], out_dir: Optional[str], log=print) -> str:
    from app.models.leader_cycle import LeaderCycleSnapshot
    from app.routers.leader_cycle import get_leader_cycle
    from app.services.trading_calendar import get_trading_days

    q = db.query(LeaderCycleSnapshot.date).distinct()
    if start:
        q = q.filter(LeaderCycleSnapshot.date >= start)
    if end:
        q = q.filter(LeaderCycleSnapshot.date <= end)
    dates = sorted(d for (d,) in q)
    if not dates:
        raise SystemExit("这个区间里没有生命周期快照")

    items_by_date: Dict[date, List[dict]] = {}
    skipped: List[str] = []
    for i, d in enumerate(dates):
        resp = get_leader_cycle(trade_date=d, db=db)
        its = [x.model_dump() for x in (*resp.running, *resp.broken)]
        settled = sum(1 for x in its if x.get("bar_settled") is True)
        if its and settled * 2 < len(its):
            skipped.append(f"{d}（{len(its) - settled}/{len(its)} 只未收盘结算，盘中跑的，不导出）")
            continue
        items_by_date[d] = its
        if (i + 1) % 20 == 0:
            log(f"  回放到 {d}（{i + 1}/{len(dates)}）")
    days = sorted(items_by_date)
    if not days:
        raise SystemExit("区间里没有已结算的交易日")
    log(f"回放完成：{len(days)} 个交易日，{sum(len(v) for v in items_by_date.values())} 个股票日")

    # 未来标签要用到区间之后的行情。交易日 = 交易日历 ∪ 上证日线日期（上证每个交易日都有一根，
    # 日历盘中不含今天、偶尔漏日子，两者并起来最全）。**只到最后一个已收盘的日子**：
    # 盘中跑过日更的那天，快照里是现价，拿它当 T+1 收盘就是拿上午的价格冒充结果
    from app.models.market_index import IndexDailySnapshot
    last_ok = _last_settled_date(db, LeaderCycleSnapshot)
    try:
        cal = set(get_trading_days(db, need_through=last_ok) or [])
    except Exception:  # noqa: BLE001
        cal = set()
    idx_days = {d for (d,) in db.query(IndexDailySnapshot.date).filter(
        IndexDailySnapshot.index_code == "000001", IndexDailySnapshot.date >= days[0])}
    cal_note = ("交易日历（fuyao）并上证日线日期" if cal else
                "没有交易日历缓存，用上证日线日期" if idx_days else "")
    cal |= idx_days
    if not cal:
        cal = {d for (d,) in db.query(LeaderCycleSnapshot.date).distinct()}
        cal_note = "既没有交易日历也没有上证日线，用生命周期快照的日期代替（漏跑的日子会被当成不存在，未来标签可能错位）"
    cal = sorted(x for x in cal | set(days) if days[0] <= x <= last_ok)
    cal_note += f"；未来标签只用到 {last_ok}（最后一个已收盘的日子）"
    pos = {d: i for i, d in enumerate(cal)}

    codes = sorted({x["code"] for v in items_by_date.values() for x in v})
    bars = _load_bars(db, codes, days[0] - timedelta(days=10), cal[-1])
    states_by_date = {d: {x["code"]: effective_state(x) for x in v} for d, v in items_by_date.items()}

    rows = []
    for d in days:
        after = cal[pos[d] + 1:] if d in pos else []
        for it in items_by_date[d]:
            b = bars.get(it["code"], {})
            close = it.get("latest_close") if it.get("latest_close") is not None else (b.get(d) or {}).get("close")
            fut = forward_labels(b, close, after[:max(HORIZONS)])
            rows.append(_stock_row(d, it, b.get(d), fut, states_by_date, after))

    extras = _market_extras(db, days)
    mrows = []
    for i, d in enumerate(days):
        its = items_by_date[d]
        st = Counter(effective_state(x) for x in its)
        new = Counter(effective_state(x) for x in its if x.get("transitioned_today")
                      and x.get("lifecycle_state") == effective_state(x))
        m = {"date": d, "pool_size": len(its), **extras.get(d, {})}
        for s in STATES:
            m[f"cnt_{s}"] = st.get(s, 0)
            m[f"new_{s}"] = new.get(s, 0)
        prev = days[i - 1] if i and pos.get(days[i - 1]) == pos.get(d, -9) - 1 else None
        if prev:
            g: Dict[str, List[float]] = defaultdict(list)
            for x in items_by_date[prev]:
                r = _pct((bars.get(x["code"], {}).get(prev) or {}).get("close"),
                         (bars.get(x["code"], {}).get(d) or {}).get("close"))
                s = effective_state(x)
                if r is not None and s:
                    g[s].append(r)
                    g["_all"].append(r)
            for s in STATES:
                v = g.get(s)
                m[f"eff_{s}_avg_pct"] = round(sum(v) / len(v), 4) if v else None
                m[f"eff_{s}_n"] = len(v) if v else 0
            m["pool_avg_pct"] = round(sum(g["_all"]) / len(g["_all"]), 4) if g.get("_all") else None
        mrows.append(m)

    out_dir = out_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      "data", "exports", f"lifecycle_{days[0]}_{days[-1]}")
    os.makedirs(out_dir, exist_ok=True)
    _write_csv(os.path.join(out_dir, "stock_days.csv"), STOCK_COLUMNS, rows)
    _write_csv(os.path.join(out_dir, "market_days.csv"), MARKET_COLUMNS, mrows)
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(_readme(days, rows, mrows, skipped, cal_note))
    zip_path = shutil.make_archive(out_dir, "zip", root_dir=os.path.dirname(out_dir),
                                   base_dir=os.path.basename(out_dir))
    log(f"✅ {len(rows)} 行股票日 / {len(mrows)} 个交易日 → {out_dir}")
    log(f"   打包：{zip_path}")
    for s in skipped:
        log(f"   跳过 {s}")
    return zip_path


def _write_csv(path: str, columns, rows) -> None:
    names = [c for c, _ in columns]
    # utf-8-sig：Excel 直接双击打开不乱码
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(names)
        for r in rows:
            w.writerow([_fmt(r.get(c)) for c in names])


def _coverage(rows, columns) -> List[str]:
    out = []
    n = len(rows) or 1
    for c, _ in columns:
        k = sum(1 for r in rows if r.get(c) is not None and r.get(c) != "")
        if k < n:
            out.append(f"| `{c}` | {k / n:.0%} |")
    return out


def _readme(days, rows, mrows, skipped, cal_note) -> str:
    from app.services.leader_cycle_state_service import FORMULA_VERSION, REASON_TEXT
    states = Counter(r["state"] for r in rows)
    lines = [
        "# 高标池生命周期数据集",
        "",
        f"- 区间：{days[0]} ~ {days[-1]}，{len(days)} 个交易日；股票日 {len(rows)} 行，涉及 "
        f"{len({r['code'] for r in rows})} 只",
        f"- 生命周期规则版本：`{FORMULA_VERSION}`（用当前规则回放全部历史，不是当时页面显示过的值）",
        f"- 未来标签用的交易日：{cal_note}",
        "- 各状态股票日数：" + "，".join(f"{STATE_ZH.get(s, s)} {n}" for s, n in states.most_common()),
        *([f"- 跳过的日期：{'；'.join(skipped)}"] if skipped else []),
        "",
        "## 这份数据回答什么",
        "",
        "高标池 = 近 60 个交易日最高连板 ≥4 的票（不含 ST、退市整理期、北交所），每天成员不同。每只票每天有一个",
        "生命周期状态，描述它在「连板 → 断板 → 修复 → 二波 → 走弱 / 结束」这条路上走到了哪一步。",
        "想研究的是：**在什么状态、什么市场环境下买，之后更容易赚钱；什么时候该卖。**",
        "",
        "## 文件",
        "",
        "- `stock_days.csv`：一行 = 一个交易日 × 一只票。主键 (date, code)。",
        "- `market_days.csv`：一行 = 一个交易日。可以按 date 跟 stock_days 关联。",
        "- 涨跌幅、回撤、距离、强弱、fut_ 收益都是**百分数**（3.5 = 3.5%）。布尔列 1/0，空 = 缺失（不是 0）。",
        "",
        "## 状态怎么判（简述）",
        "",
        STATE_RULES,
        "",
        "## 原因代码",
        "",
        "| 代码 | 含义 |",
        "|---|---|",
        *[f"| `{k}` | {v} |" for k, v in REASON_TEXT.items()],
        "",
        "## 必须知道的口径与陷阱",
        "",
        "1. **fut_ 开头的列是未来数据**，只能当结果（标签）用，不能当买卖条件。其余列都是当天收盘时就已知的。",
        "2. **状态没有未来函数**：某天的状态只用到当天及以前的行情。但整套规则是现在的版本，阈值是看过历史后定的，"
        "   存在一定的事后拟合，结论要在之后的新数据上再验证。",
        "3. **涨停买不进**：当天收盘涨停（is_limit_up=1）的票在收盘买不到；次日一字涨停"
        "   （fut_next_one_word_limit_up=1）开盘也买不到。用 fut_ret_open_* 模拟次日开盘买更接近真实。",
        "4. **样本不独立**：同一只票连续很多天都在池子里，同一天的票又同涨同跌。不要把股票日数当独立样本数；"
        "   建议按交易日或按事件聚合，再看有多少个独立日子 / 事件支持结论。",
        "5. **对照组**：高标池整体在多数时期是下跌的。看某个规则「赚不赚钱」时，同时减去同日全池（market_days"
        "   的 pool_avg_pct，或 stock_days 同日中位）看超额，否则只是在跟大盘行情。",
        "6. **收盘价不复权**：fut_adj_suspect=1 的行，之后 10 天里有一天收盘价比值和交易所涨跌幅对不上"
        "   （除权或数据问题），fut_ret 可能失真。",
        "7. **停牌 / 缺数**：第 N 天没有行情时 fut_ret_N 留空，不顶替；fut_state_N 为空表示那天已不在高标池。",
        "8. **区间短**：只覆盖一段行情，情绪周期可能只经历了一两轮。任何阈值都可能只适用于这一段。",
        "9. 没有交易成本、滑点。",
        "",
        "## 缺失率（只列有缺失的列）",
        "",
        "### stock_days.csv",
        "",
        "| 列 | 有值比例 |",
        "|---|---|",
        *_coverage(rows, STOCK_COLUMNS),
        "",
        "### market_days.csv",
        "",
        "| 列 | 有值比例 |",
        "|---|---|",
        *_coverage(mrows, MARKET_COLUMNS),
        "",
        "## 字段字典",
        "",
        "### stock_days.csv",
        "",
        "| 列 | 含义 |",
        "|---|---|",
        *[f"| `{c}` | {d} |" for c, d in STOCK_COLUMNS],
        "",
        "### market_days.csv",
        "",
        "| 列 | 含义 |",
        "|---|---|",
        *[f"| `{c}` | {d} |" for c, d in MARKET_COLUMNS],
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="导出高标池生命周期数据集（只读）")
    ap.add_argument("--start", help="起始日 YYYY-MM-DD，默认最早有快照的那天")
    ap.add_argument("--end", help="截止日 YYYY-MM-DD，默认最新（未收盘的当天自动跳过）")
    ap.add_argument("--out", help="输出目录，默认 backend/data/exports/lifecycle_<起>_<止>")
    args = ap.parse_args()
    import app.models  # noqa: F401  注册全部模型；不调 init_db——它会跑建表/补列，这个脚本只读
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        export(db, date.fromisoformat(args.start) if args.start else None,
               date.fromisoformat(args.end) if args.end else None, args.out)
    finally:
        db.rollback()
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
