"""
买入识别 · 规则（buy_recog_v2，2026-11-23）。**全是纯函数**：事实进、结论 + 证据出，不碰库、不碰网络。
事实从哪来、怎么核实，在 buy_recognition_service.py。

服务龙头弱转强交易：只回答「此刻该不该去买入检查」，不给买点、空间、止损、仓位——那些由
/pre-trade-check 继续核对。三关：

  市场过关   跌停家数（硬条件）· 最大空头：近 3 日领跌 · 最大空头：封单最重 · 大盘止跌放量 · 高标负反馈收敛
  主线走强   同一轮反弹里板块先于 / 伴随指数启动、同窗口跑赢 · 回踩时相对强度仍在、再上攻 · 成分股有广度
  带队个股   它什么时候先动（可比的启动事件）· 回落有没有承接 · 后来哪几只（同板块、同一轮）跟进

## 每一项四种结论，不混用

  PASS          通过
  BLOCK         拦截：有明确的拦截证据（可信下界 >10 家跌停、最大空头封死 / 回封 / 创新低、高位跌停）
  NOT_MET       条件未满足：数据齐了，但条件没到
  INSUFFICIENT  数据不足：缺数、过期、覆盖不全——**一律不放行**

市场合起来：有 BLOCK 先报 BLOCK，其次 NOT_MET，再次 INSUFFICIENT，全部 PASS 才是「市场过关」。

## 版本

- buy_recog_v1（2026-11-23 上午）：首版。
- buy_recog_v2（2026-11-23 下午，一轮评审之后）：
  · 跌停家数按「可信下界 / 上界」判：下界 >10 直接拦截（不用等全核完），上界 ≤10 才通过；行情过期的票算进上界；
    接口没给总数 = 上界未知；涨跌分布只有沪深两市都返回了才能当上界
  · 最大空头拆成两项独立证据；封单锚分「已锁定 / 可信确认没有 / 待核」，没锁定不再只凭近 3 日领跌股放行
  · 高位跌停先于覆盖检查拦截；两次观测比的是同一批票
  · 分时按交易分钟查新鲜度（午休不算），过期的那路当数据不足
  · 主线「跑赢」改成同一窗口（这一轮的起点到此刻）里比板块和上证的涨幅
  · 带队和跟进只在执行入口指的那个板块里找、跟进要在同一轮里；启动事件要可比；最早的走弱了评接力者
  · 只有连续竞价时段给执行入口

## 阈值

跌停 0–10 / 11–20 / >20 是用户的交易纪律。其余（反弹幅度、回踩深度、守住几分钟……）都是**工程初始值**，
没拿历史回测过——上线后看实战再调，调之前先量（[[feedback-measure-dont-assume]]）。
**不用龙头分 / 情绪分**：自造的黑箱分数，新模块不依赖（排序只用连板高度、近期涨停、流通市值、价格）。
"""
from __future__ import annotations

import statistics
from datetime import datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

RULE_VERSION = "buy_recog_v2"

PASS, BLOCK, NOT_MET, INSUFFICIENT = "PASS", "BLOCK", "NOT_MET", "INSUFFICIENT"
STATUS_ZH = {PASS: "通过", BLOCK: "拦截", NOT_MET: "条件未满足", INSUFFICIENT: "数据不足"}
_SEVERITY = {BLOCK: 0, NOT_MET: 1, INSUFFICIENT: 2, PASS: 3}

# ── 阈值 ────────────────────────────────────────────────────────────────────
LD_PASS_MAX = 10            # 跌停 0~10 家才继续（用户定）
LD_NO_NEW_MAX = 20          # 11~20 不开新仓；>20 停手 · 空仓（用户定）
# —— 以下是工程初始值 ——
BEAR_LEAVE_BARS = 10        # 离开跌停 / 低点后至少走完 10 根分钟，才不算「短暂开板」
BEAR_REBOUND_PCT = 2.0      # 离开后至少反弹 2%（按昨收）
BEAR_PULLBACK_PCT = 0.8     # 反弹后回落至少 0.8% 才算一次回踩
BEAR_HOLD_PCT = 1.0         # 回踩低点比跌停价 / 当日低点高 1% 以上才算守住
BEAR_NEW_LOW_BARS = 3       # 当日低点就在最近 3 根里 = 还在创新低
MAX_SEAL_LOCKS = 5          # 封单锚最多跟踪几只（第一次锁的 + 之后出现的更重的）
IDX_HOLD_BARS = 5           # 指数低点之后至少 5 根没再破
IDX_REBOUND_PCT = 0.3       # 指数离低点至少反弹 0.3%
IDX_MIN_PASS = 2            # 至少两只核心指数
VOL_RATIO_MIN = 1.0         # 同期比 ≥ 1.0 算放量
BREADTH_WEAK = 0.4          # 上涨家数占比 < 40%，指数却守住反弹 = 权重托举
HM_MIN_SAMPLE = 5           # 高标样本至少 5 只拿到行情
HM_MIN_COVERAGE = 0.8       # 且覆盖样本 80%
HM_RED_MIN = 0.5            # 红盘占比
HM_RECHECK_S = 60           # 两次观测的行情时间至少隔 60 秒，第二次仍为正才算「转正且未再走弱」
HM_WORSEN_PP = 0.5          # 中位涨幅比上一次低 0.5 个百分点以上 = 又走弱了
HM_IMPROVE_PP = 1.0         # 比当天最差那次高 1 个百分点 = 修复中
MINUTE_MAX_LAG_MIN = 3      # 1 分钟分时：最后一根走完的比行情时间晚 3 个交易分钟以上 = 过期（午休不算）
AMOUNT_MAX_LAG_MIN = 10     # 同期成交额（新浪 5 分钟一根）：比较时刻比行情时间晚 10 个交易分钟以上 = 过期
QUOTE_MAX_LAG_S = 300       # 个股行情自带时间比行情时间早 5 分钟以上 = 过期
MERGE_OVERLAP = 0.6         # 两个板块今天的活跃成分重合 ≥ 60%（按少的那边算）就归并（只影响展示）
NEW_MIN_ACTIVE = 3          # 新方向：关注板块里今天涨停 + 触板至少 3 只
SEC_MIN_BARS = 10           # 板块 / 指数分时至少对齐 10 分钟才判
SEC_RS_MIN = 1.0            # 这一轮里（同一窗口）板块比上证多涨 ≥ 1 个百分点
SEC_LEAD_SLACK_MIN = 5      # 板块低点不晚于上证低点 5 分钟 = 先于或伴随
SEC_PULLBACK_PP = 0.5       # 板块从高点回落 ≥ 0.5 个百分点才算回踩
SEC_RESTRENGTH = 0.5        # 回踩后收复回落的一半以上 = 再度走强
BREADTH_UP_MIN = 0.55       # 成分股上涨占比
FOLLOW_PCT = 3.0            # 「跟进」= 涨 ≥3% 或封板
FOLLOW_MIN = 3              # 至少 3 只独立成分股跟进
MEMBER_MIN_COVERAGE = 0.6   # 成分股拿到行情的比例
LEAD_PCT = 5.0              # 个股「先动」= 首封 / 首次触板时刻，没有就用第一次涨到 +5% 的时刻
LEADER_FOLLOW_MIN = 2       # 带队后面至少 2 只同板块跟进
LEADER_HOLD_SHARE = 0.5     # 回落后守住涨幅的一半以上 = 有承接

THRESHOLDS = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float))
              and k not in ("PASS", "BLOCK", "NOT_MET", "INSUFFICIENT")}


def item(key: str, title: str, status: str, label: str, reason: str, **extra) -> dict:
    return {"key": key, "title": title, "status": status, "status_zh": STATUS_ZH[status],
            "label": label, "reason": reason, **extra}


def combine(items: Sequence[dict]) -> Tuple[str, Optional[dict]]:
    """有 BLOCK 先报 BLOCK，其次 NOT_MET，再次 INSUFFICIENT；返回 (结论, 决定结论的那一项)。"""
    if not items:
        return INSUFFICIENT, None
    worst = min(items, key=lambda x: _SEVERITY[x["status"]])
    return worst["status"], (None if worst["status"] == PASS else worst)


Pt = Tuple[datetime, float]       # (分钟结束时刻, 价)


def _hm(t: Optional[datetime]) -> Optional[str]:
    return t.strftime("%H:%M") if t else None


def _pct(px: float, base: float) -> float:
    return (px / base - 1) * 100


def max_drawdown(vals: Sequence[float], start: int = 0) -> Tuple[int, int, float]:
    """
    从 start 起最深的一次回落：(峰的位置, 谷的位置, 回落幅度)。峰取到谷为止的累计最高。
    **不能用「全天最高点之后」找回踩**——回踩完又创新高的（最强的那种），最高点之后就没有点了。
    """
    if start >= len(vals):
        return start, start, 0.0
    peak_i, best = start, (start, start, 0.0)
    for j in range(start, len(vals)):
        if vals[j] > vals[peak_i]:
            peak_i = j
        dd = vals[peak_i] - vals[j]
        if dd > best[2]:
            best = (peak_i, j, dd)
    return best


# ── 行情时效：按交易分钟算（午休 11:30–13:00 不算） ─────────────────────────

_AM_OPEN, _AM_CLOSE, _PM_OPEN, _PM_CLOSE = dtime(9, 30), dtime(11, 30), dtime(13, 0), dtime(15, 0)


def trade_minute(t: datetime) -> float:
    """09:30 起走过的交易分钟：上午 0~120，午休停在 120，下午 120~240。"""
    def m(x: dtime) -> float:
        return x.hour * 60 + x.minute + x.second / 60
    x = t.time()
    if x <= _AM_OPEN:
        return 0.0
    if x <= _AM_CLOSE:
        return m(x) - m(_AM_OPEN)
    if x <= _PM_OPEN:
        return 120.0
    return 120.0 + min(m(x), m(_PM_CLOSE)) - m(_PM_OPEN)


def trade_lag_min(last: Optional[datetime], now: Optional[datetime]) -> Optional[float]:
    """last 比 now 晚了多少个交易分钟；不是同一天 = 无穷大。"""
    if last is None or now is None:
        return None
    if last.date() != now.date():
        return float("inf")
    return max(0.0, trade_minute(now) - trade_minute(last))


def minute_staleness(pts: Sequence[Pt], now: Optional[datetime], max_lag: float = MINUTE_MAX_LAG_MIN) -> Optional[str]:
    """分时过期了就返回原因，新鲜返回 None。"""
    if not pts:
        return "没有今天已走完的分时"
    lag = trade_lag_min(pts[-1][0], now)
    if lag is not None and lag > max_lag:
        return f"分时停在 {_hm(pts[-1][0])}，比行情时间 {_hm(now)} 晚 {lag:.0f} 个交易分钟，过期了"
    return None


# ═══ 市场过关 ①：跌停家数 ═══════════════════════════════════════════════════

def limit_down_item(*, pool_ok: bool, pool_error: Optional[str], pool_tc: Optional[int], rows: Sequence[dict],
                    verify: Dict[str, str], updown_ld: Optional[int], updown_complete: bool,
                    market_time: Optional[datetime], source_meta: dict, excluded: Optional[dict] = None,
                    phase_ok: bool = True, phase_note: str = "") -> dict:
    """
    跌停家数是硬条件：「非 ST、沪深、**此刻仍封跌停**」的可信计数。rows = 已去重、已剔除 ST / 北交所的跌停池行；
    verify[code] = sealed（行情核过，此刻仍在跌停）/ left（行情显示已离开跌停价）/ stale（行情过期或不是今天）/
    missing（没核上）。

    按**可信区间 [下界, 上界]** 判：
      下界 = 行情核过仍封跌停的只数（不管池子可不可信，这些票此刻确实在跌停上）；
      上界 = 下界 + 过期 + 没核上 + 接口自报总数里没拿到的那几行；池子本身不可信（没拿到 / 旧交易日名单）或
            接口没给总数 → 上界未知，此时沪深两市涨跌分布都返回了才能拿它（含 ST）当上界。
      下界 >20 → 停手 · 空仓；下界 >10 → 不开新仓；上界 ≤10 才通过；其余数据不足。
    """
    T = "跌停家数"
    sealed = [r for r in rows if verify.get(r["code"]) == "sealed"]
    left = [r for r in rows if verify.get(r["code"]) == "left"]
    stale = [r for r in rows if verify.get(r["code"]) == "stale"]
    unknown = [r for r in rows if verify.get(r["code"]) not in ("sealed", "left", "stale")]
    lo = len(sealed)
    facts: Dict[str, Any] = {"pool_rows": len(rows), "pool_tc": pool_tc, "updown_limit_down": updown_ld,
                             "updown_complete": updown_complete, "sealed_now": lo, "left_pool": len(left),
                             "stale": len(stale), "unverified": len(unknown), **(excluded or {})}
    if not phase_ok:
        return item("limit_down", T, INSUFFICIENT, "未开盘", phase_note, facts=facts, meta=source_meta, stop_all=False)

    issue: Optional[str] = None       # 池子本身不可信 → 上界未知
    if not pool_ok:
        issue = f"跌停池没拿到：{pool_error or '未知原因'}"
    elif rows and len(stale) * 2 >= len(rows):
        issue = f"跌停池里 {len(stale)}/{len(rows)} 只的行情过期或不是今天——多半还是上一交易日的名单"
    elif rows and market_time is not None and any(
            r.get("last_seal_time") and r["last_seal_time"] > market_time.strftime("%H:%M:%S") for r in rows):
        issue = "跌停池里有封板时间晚于此刻行情时间的票——是旧交易日的名单"
    elif pool_tc is None:
        issue = "跌停池接口没给总数，分不清是不是全了" + ("（空池分不清真 0 还是没拿到）" if not rows else "")
    hi: Optional[int] = None
    hi_src = "跌停池"
    if issue is None:
        hi = lo + len(stale) + len(unknown) + max((pool_tc or 0) - len(rows), 0)
    elif updown_complete and updown_ld is not None:
        hi, hi_src = max(updown_ld, lo), "东财涨跌分布（沪深两市都返回，含 ST）"
    facts.update(lower=lo, upper=hi, upper_source=hi_src if hi is not None else None)

    names = ([{**r, "verified": "sealed"} for r in sealed] + [{**r, "verified": "stale"} for r in stale]
             + [{**r, "verified": "missing"} for r in unknown])
    note = []
    if left:
        note.append(f"{len(left)} 只池里还挂着、行情已离开跌停价，不计")
    if stale or unknown:
        note.append(f"{len(stale) + len(unknown)} 只行情过期或没核上，只算进上界")
    if pool_tc is not None and pool_tc > len(rows):
        note.append(f"接口自报 {pool_tc} 只、只拿到 {len(rows)} 行（缺页），差的算进上界")
    if updown_ld is not None:
        note.append(f"东财涨跌分布（含 ST、北交所）{updown_ld} 家" + ("" if updown_complete else "，沪深没返回全，只作对照"))
    tail = ("；" + "；".join(note)) if note else ""
    rng = f"{lo}~{hi if hi is not None else '?'}"
    common = dict(count_range=[lo, hi], facts=facts, meta=source_meta, names=names)

    if lo > LD_NO_NEW_MAX:
        return item("limit_down", T, BLOCK, "停手 · 空仓", f"此刻确认仍封跌停 {lo} 家（>{LD_NO_NEW_MAX}）{tail}",
                    count=lo, stop_all=True, **common)
    if lo > LD_PASS_MAX:
        more = "，可能更多" if hi is None or hi > LD_NO_NEW_MAX else ""
        return item("limit_down", T, BLOCK, "不开新仓", f"此刻确认仍封跌停至少 {lo} 家{more}{tail}",
                    count=lo, stop_all=False, **common)
    if hi is not None and hi <= LD_PASS_MAX:
        if hi == 0 and not rows:
            if updown_ld is not None and updown_ld > LD_PASS_MAX:
                return item("limit_down", T, INSUFFICIENT, "数据不足",
                            f"跌停池 0 只，但东财涨跌分布（含 ST、北交所）有 {updown_ld} 家，分不清是不是跌停池漏了",
                            count=None, stop_all=False, **common)
            return item("limit_down", T, PASS, "0 家", "跌停池此刻没有非 ST 跌停股（接口给了总数 0）",
                        count=0, stop_all=False, **common)
        why = (f"此刻仍封跌停 {lo} 家" + (f"，最多 {hi} 家" if hi > lo else "")
               + (f"（{issue}，上界按{hi_src}）" if issue else ""))
        return item("limit_down", T, PASS, "≤10 家", why + tail, count=lo, stop_all=False, **common)
    reason = issue or f"可信区间 {rng} 家，跨过了 {LD_PASS_MAX} 家这一档"
    if issue and hi is None:
        reason += "；上界未知" + ("（涨跌分布沪深没返回全，不能当上界）" if updown_ld is not None and not updown_complete else "")
    return item("limit_down", T, INSUFFICIENT, "数据不足", f"{reason}；此刻确认 {lo} 家{tail}",
                count=None, stop_all=False, **common)


# ═══ 市场过关 ②：最大空头（两项独立证据） ═══════════════════════════════════

SEALED, RESEALED, NEW_LOW, TESTING, STOPPED, UNKNOWN = "SEALED", "RESEALED", "NEW_LOW", "TESTING", "STOPPED", "UNKNOWN"
BEAR_ZH = {SEALED: "封死跌停", RESEALED: "开板后回封", NEW_LOW: "还在创新低", TESTING: "离开低点，还没回踩确认",
           STOPPED: "止跌", UNKNOWN: "无法判断"}
BEAR_STATUS = {SEALED: BLOCK, RESEALED: BLOCK, NEW_LOW: BLOCK, TESTING: NOT_MET, STOPPED: PASS, UNKNOWN: INSUFFICIENT}


def judge_bear(pts: Sequence[Pt], *, prev_close: Optional[float], ld_price: Optional[float],
               price_now: Optional[float], now: Optional[datetime] = None) -> dict:
    """
    一只空头标的此刻止没止跌。pts = 今天已走完的分钟价；price_now = 此刻行情价。每次刷新从头重算，
    所以止跌之后回封 / 跌破回踩低点会自动撤销。

    碰过跌停：地板 = 跌停价。此刻在跌停上 → 封死（从没离开过）/ 回封（离开过又回去）。
    没碰过跌停：地板 = 当日低点；低点就在最近几分钟、或此刻跌破已走完分钟的低点 → 还在创新低。
    离开地板后要：走完 ≥10 分钟（短暂开板不算）、反弹 ≥2%、回落 ≥0.8%、回落低点比地板高 ≥1%、
    此刻在回落低点之上 → 止跌。任何一步没到 → 「离开低点，还没回踩确认」。
    """
    out: Dict[str, Any] = {"state": UNKNOWN, "evidence": None}
    if not pts or not prev_close:
        out["evidence"] = "没有今天的分时" if not pts else "没有昨收"
        return out
    cur = price_now if price_now is not None else pts[-1][1]
    eps = 0.0051
    touched = [i for i, (_, px) in enumerate(pts) if ld_price is not None and px <= ld_price + eps]
    if ld_price is not None and (touched or cur <= ld_price + eps):
        floor, floor_zh = ld_price, "跌停价"
        if cur <= ld_price + eps:
            first = touched[0] if touched else len(pts)
            left_before = any(px > ld_price + eps for _, px in pts[first:])
            st = RESEALED if left_before else SEALED
            return {"state": st, "floor": floor, "evidence": "此刻在跌停价上" + ("（中途开过板）" if left_before else "")}
        last = touched[-1]
    else:
        floor, floor_zh = min(px for _, px in pts), "当日低点"
        last = max(i for i, (_, px) in enumerate(pts) if px <= floor + 1e-9)
        if cur < floor - eps:
            return {"state": NEW_LOW, "floor": floor, "evidence": f"此刻 {cur:g} 跌破已走完分钟的低点 {floor:g}"}
        if last >= len(pts) - BEAR_NEW_LOW_BARS:
            return {"state": NEW_LOW, "floor": floor, "evidence": f"当日低点 {floor:g} 就在 {_hm(pts[last][0])}，刚出来"}
    after = list(pts[last + 1:])
    t_leave = after[0][0] if after else None
    base = {"floor": floor, "left_at": _hm(t_leave)}
    if len(after) < BEAR_LEAVE_BARS:
        return {**base, "state": TESTING, "evidence": f"{_hm(t_leave) or '刚'}离开{floor_zh}，才走完 {len(after)} 分钟"}
    vals = [px for _, px in after]
    top = max(vals)
    rebound = (top - floor) / prev_close * 100
    base.update(rebound_pct=round(rebound, 2))
    k0 = next((k for k, v in enumerate(vals) if (v - floor) / prev_close * 100 >= BEAR_REBOUND_PCT), None)
    if k0 is None:
        return {**base, "state": TESTING, "evidence": f"离开{floor_zh}后最多反弹 {rebound:.1f}%，不到 {BEAR_REBOUND_PCT:g}%"}
    hi, lo_i, dd = max_drawdown(vals, k0)
    H, L2 = vals[hi], vals[lo_i]
    depth, hold = dd / prev_close * 100, (L2 - floor) / prev_close * 100
    base.update(high=H, high_at=_hm(after[hi][0]))
    if depth < BEAR_PULLBACK_PCT:
        return {**base, "state": TESTING,
                "evidence": f"反弹到 {top:g}（+{rebound:.1f}%）后最多回落 {depth:.1f}%，还没出现像样的回踩"}
    base.update(pullback_low=L2, pullback_at=_hm(after[lo_i][0]), pullback_pct=round(depth, 2), hold_pct=round(hold, 2))
    if hold < BEAR_HOLD_PCT:
        return {**base, "state": TESTING, "evidence": f"回踩低点 {L2:g} 离{floor_zh}只高 {hold:.1f}%，没守稳"}
    if cur <= L2 + 1e-9:
        return {**base, "state": TESTING, "evidence": f"此刻 {cur:g} 回到回踩低点 {L2:g}，还在试"}
    return {**base, "state": STOPPED,
            "evidence": f"{_hm(t_leave)} 离开{floor_zh}，反弹到 {H:g}（+{rebound:.1f}%），回踩 {L2:g} 守住（高于{floor_zh} {hold:.1f}%）"}


def _bear_verdict(key: str, title: str, targets: Sequence[dict], **extra) -> dict:
    """targets 都判过了（t['judge']['state']）：最严的那只决定结论。"""
    worst = min(targets, key=lambda t: _SEVERITY[BEAR_STATUS[t["judge"]["state"]]])
    st = BEAR_STATUS[worst["judge"]["state"]]
    who = lambda t: t.get("name") or t["code"]  # noqa: E731
    if st == PASS:
        reason = "；".join(f"{who(t)}：{t['judge']['evidence']}" for t in targets)
        return item(key, title, PASS, "已止跌", reason, targets=list(targets), **extra)
    label = {BLOCK: BEAR_ZH[worst["judge"]["state"]], NOT_MET: "还没止跌", INSUFFICIENT: "数据不足"}[st]
    tail = "——仍在创新低、强封或开板后回封，就等" if st == BLOCK else ""
    return item(key, title, st, label, f"{who(worst)}：{worst['judge']['evidence']}{tail}", targets=list(targets), **extra)


def bear_3d_item(target: Optional[dict], *, error: Optional[str] = None, notes: Sequence[str] = ()) -> dict:
    """近 3 个已结算交易日累计跌幅最深的那只（target 带 judge）。"""
    T = "最大空头·近 3 日领跌"
    if target is None:
        return item("bear_3d", T, INSUFFICIENT, "数据不足", error or "近 3 日领跌股没算出来", targets=[], notes=list(notes))
    return _bear_verdict("bear_3d", T, [target], notes=list(notes))


SEAL_LOCKED, SEAL_NONE, SEAL_UNVERIFIED, SEAL_UNRECOVERABLE = "locked", "none", "unverified", "unrecoverable"


def bear_seal_item(locks: Sequence[dict], *, anchor: str, pool_trusted_now: bool, none_at: Optional[str] = None,
                   error: Optional[str] = None, notes: Sequence[str] = ()) -> dict:
    """
    封单最重的那只（locks 带 judge）。封单锚三种状态分开说：
      locked        已锁定：逐只判（开板离池照样跟踪）；全止跌但这次跌停池不可信 → 数据不足（没法确认没出现更重的）
      none          可信跌停池快照确认此刻没有封跌停的票（今天也没锁过）→ 通过
      unverified    今天还没有可信快照 → 待核（数据不足），**不能只凭近 3 日领跌股放行**
      unrecoverable 存下的锁定依据读不回来 → 待核
    """
    T = "最大空头·封单最重"
    ex = {"anchor": anchor, "notes": list(notes)}
    if anchor == SEAL_UNRECOVERABLE:
        return item("bear_seal", T, INSUFFICIENT, "封单锚待核",
                    f"今天锁定的封单标的读不回来（{error or '存储读取失败'}），不能当作没有", targets=[], **ex)
    if not locks:
        if anchor == SEAL_NONE:
            return item("bear_seal", T, PASS, "没有封单标的",
                        f"{none_at or '此刻'}可信跌停池快照确认没有封跌停的非 ST 股", targets=[], **ex)
        return item("bear_seal", T, INSUFFICIENT, "封单锚待核",
                    "今天还没有可信的跌停池快照，封单最重的那只还没锁定", targets=[], **ex)
    out = _bear_verdict("bear_seal", T, locks, **ex)
    if out["status"] == PASS and not pool_trusted_now:
        return item("bear_seal", T, INSUFFICIENT, "数据不足",
                    out["reason"] + "；但这次跌停池不可信，没法确认没出现更重的新封单", targets=list(locks), **ex)
    return out


# ═══ 市场过关 ③：大盘止跌放量 ══════════════════════════════════════════════

def judge_index(pts: Sequence[Pt], *, prev_close: Optional[float], price_now: Optional[float]) -> dict:
    """一只指数：低点之后走完 ≥5 分钟没再破，且离低点反弹 ≥0.3% → 低点守住并反弹。"""
    if len(pts) <= IDX_HOLD_BARS or not prev_close:
        return {"ok": None, "evidence": "分时不够" if prev_close else "没有昨收"}
    low = min(px for _, px in pts)
    i_low = max(i for i, (_, px) in enumerate(pts) if px <= low + 1e-9)
    cur = price_now if price_now is not None else pts[-1][1]
    held = len(pts) - 1 - i_low
    rebound = (cur - low) / prev_close * 100
    ev = {"low": low, "low_at": _hm(pts[i_low][0]), "held_bars": held, "rebound_pct": round(rebound, 2),
          "pct": round(_pct(cur, prev_close), 2), "bars_to": _hm(pts[-1][0])}
    if cur < low:
        return {**ev, "ok": False, "evidence": f"此刻跌破 {_hm(pts[i_low][0])} 的低点"}
    if held < IDX_HOLD_BARS:
        return {**ev, "ok": False, "evidence": f"低点 {_hm(pts[i_low][0])} 刚出来（{held} 分钟）"}
    if rebound < IDX_REBOUND_PCT:
        return {**ev, "ok": False, "evidence": f"低点 {_hm(pts[i_low][0])} 守住 {held} 分钟，但只反弹 {rebound:.2f}%"}
    return {**ev, "ok": True, "evidence": f"低点 {_hm(pts[i_low][0])} 守住 {held} 分钟，反弹 {rebound:.2f}%"}


def index_item(indices: Sequence[dict], *, volume: Optional[dict], breadth: Optional[dict],
               phase_ok: bool, phase_note: str, volume_error: Optional[str] = None) -> dict:
    """
    indices: [{code, name, judge}]；volume: {ratio, amount, prev_same, same_as_of, ...}（沪深两市、按 bar 结束时刻对齐、
    已查过新鲜度）或 None；breadth: {up, down} 或 None。至少两只核心指数守住反弹 + 同期比 ≥1 + 上涨占比 ≥40% 才通过；
    指数守住了、上涨家数却不到四成 → 「权重托举」。
    """
    T = "大盘止跌放量"
    extra = {"indices": list(indices), "volume": volume, "breadth": breadth, "volume_error": volume_error}
    if not phase_ok:
        return item("index", T, INSUFFICIENT, "未开盘", phase_note, **extra)
    have = [x for x in indices if x["judge"].get("ok") is not None]
    ok = [x for x in have if x["judge"]["ok"]]
    if len(have) < IDX_MIN_PASS:
        bad = "；".join(f"{x['name']}：{x['judge']['evidence']}" for x in indices if x["judge"].get("ok") is None)
        return item("index", T, INSUFFICIENT, "数据不足", f"核心指数可用的分时只有 {len(have)} 只（{bad}）", **extra)
    if len(ok) < IDX_MIN_PASS:
        return item("index", T, NOT_MET, "未止跌",
                    "低点守住并反弹的只有 " + ("、".join(x["name"] for x in ok) or "0 只") + "，要至少两只", **extra)
    if not volume or volume.get("ratio") is None:
        return item("index", T, INSUFFICIENT, "数据不足", f"同期成交额不可用（{volume_error or '没拿到'}），放量没法判断", **extra)
    if not breadth or not (breadth.get("up") or breadth.get("down")):
        return item("index", T, INSUFFICIENT, "数据不足", "拿不到涨跌家数，分不清是不是权重托举", **extra)
    up_ratio = breadth["up"] / (breadth["up"] + breadth["down"])
    extra["breadth"] = {**breadth, "up_ratio": round(up_ratio, 3)}
    if up_ratio < BREADTH_WEAK:
        return item("index", T, NOT_MET, "权重托举",
                    f"指数守住了，但上涨家数只占 {up_ratio:.0%}——权重撑着，市场继续等", **extra)
    r = volume["ratio"]
    if r < VOL_RATIO_MIN:
        return item("index", T, NOT_MET, "缩量",
                    f"{'、'.join(x['name'] for x in ok)}守住反弹，但成交额只有昨日同期的 {r:.2f} 倍（截至 {volume.get('same_as_of') or '—'}）", **extra)
    return item("index", T, PASS, "止跌放量",
                f"{'、'.join(x['name'] for x in ok)}守住反弹；同期比 {r:.2f}（截至 {volume.get('same_as_of') or '—'}）；上涨占 {up_ratio:.0%}", **extra)


# ═══ 市场过关 ④：高标负反馈收敛 ════════════════════════════════════════════

def highmark_stats(facts: Sequence[dict]) -> dict:
    """facts：样本里每只的此刻事实（live_fact 的字典）。只统计拿到今天、没过期行情的。"""
    got = [f for f in facts if f.get("status") == "ok" and f.get("pct") is not None]
    pcts = [f["pct"] for f in got]
    return {"sample": len(facts), "quoted": len(got),
            "limit_down": sum(1 for f in got if f.get("limit") == "limit_down"),
            "median_pct": round(statistics.median(pcts), 2) if pcts else None,
            "red_ratio": round(sum(1 for p in pcts if p > 0) / len(pcts), 3) if pcts else None,
            "limit_down_names": [f.get("name") or f.get("code") for f in got if f.get("limit") == "limit_down"],
            "pcts": {f["code"]: f["pct"] for f in got if f.get("code")}}


def highmark_item(stats: dict, *, prev: Optional[dict], worst_median: Optional[float],
                  phase_ok: bool, phase_note: str, scope: str) -> dict:
    """
    prev = 行情时间至少早 HM_RECHECK_S 秒的上一次观测，**中位数按两次都拿到行情的同一批票算**：
           {at, median_pct（上一次）, median_now（这一次，同一批）, common}；worst_median = 今天观测到的最差中位涨幅。
    已核实的高位跌停 → 先拦截（不管覆盖够不够）；覆盖不够 → 数据不足（没法证明高位跌停已清零）。
    中位 >0 且红盘 ≥50%：上一次也 >0 且没再走弱 → 通过；否则「转正待确认」。
    仍为负：比当天最差那次高 1 个百分点 → 修复中；否则负反馈未收敛。
    """
    T = "高标负反馈"
    extra = {"stats": {k: v for k, v in stats.items() if k != "pcts"}, "scope": scope, "prev": prev,
             "worst_median": worst_median}
    if not phase_ok:
        return item("highmark", T, INSUFFICIENT, "未开盘", phase_note, **extra)
    n, q = stats["sample"], stats["quoted"]
    if stats["limit_down"]:
        return item("highmark", T, BLOCK, "高位跌停",
                    f"样本里已核实跌停 {stats['limit_down']} 只（{'、'.join(stats['limit_down_names'][:3])}），拿到行情 {q}/{n} 只",
                    **extra)
    if q < HM_MIN_SAMPLE or (n and q / n < HM_MIN_COVERAGE):
        return item("highmark", T, INSUFFICIENT, "样本不足",
                    f"昨日高标样本 {n} 只，拿到行情 {q} 只，覆盖不够——没法证明高位跌停已经清零", **extra)
    med, red = stats["median_pct"], stats["red_ratio"]
    desc = f"样本 {q} 只：中位 {med:+.2f}%，红盘 {red:.0%}，跌停 0 只"
    if med > 0 and red >= HM_RED_MIN:
        if prev is None or prev.get("median_pct") is None:
            return item("highmark", T, NOT_MET, "转正待确认",
                        f"{desc}；要行情时间隔 ≥{HM_RECHECK_S} 秒再观测一次、同一批票没再走弱才算", **extra)
        now_same = prev.get("median_now", med)
        same = f"（同一批 {prev.get('common', q)} 只）"
        if prev["median_pct"] <= 0:
            return item("highmark", T, NOT_MET, "刚转正", f"{desc}；上一次（{prev.get('at')}）中位还是 {prev['median_pct']:+.2f}%{same}", **extra)
        if now_same < prev["median_pct"] - HM_WORSEN_PP:
            return item("highmark", T, NOT_MET, "又在走弱",
                        f"{desc}；同一批票 {now_same:+.2f}%，比上一次（{prev.get('at')}）{prev['median_pct']:+.2f}% 低{same}", **extra)
        return item("highmark", T, PASS, "负反馈收敛",
                    f"{desc}；上一次（{prev.get('at')}）{prev['median_pct']:+.2f}%，同一批没再走弱{same}", **extra)
    if worst_median is not None and med >= worst_median + HM_IMPROVE_PP:
        return item("highmark", T, NOT_MET, "修复中", f"{desc}；比今天最差的 {worst_median:+.2f}% 好了", **extra)
    return item("highmark", T, NOT_MET, "负反馈未收敛", desc, **extra)


# ═══ 主线走强 ══════════════════════════════════════════════════════════════

CONFIRMED, PENDING, CANDIDATE = "CONFIRMED", "PENDING", "CANDIDATE"
MAINLINE_ZH = {CONFIRMED: "主线确认走强", PENDING: "待确认", CANDIDATE: "候选（缺分时证据）",
               NOT_MET: "未走强", INSUFFICIENT: "数据不足"}


def merge_groups(cands: Sequence[dict]) -> List[dict]:
    """
    重叠概念归并（**只影响展示**）：今天的活跃成分（涨停 + 触板）重合 ≥60%（按少的那边算，两边都 ≥2 只）就并成一组。
    代表板块 = 最能解释这轮上涨的那个：活跃只数多 → 活跃占成分比例高 → 行业优先 → 名字。
    判定、带队、跟进、入口的 sector_id **全按代表板块自己的成分**；并进来的板块只列名字、活跃并集只用来排序。
    cands: [{sid, code, name, type, members_n, active: set, origin, state?}]
    """
    n = len(cands)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            a, b = cands[i]["active"], cands[j]["active"]
            if len(a) >= 2 and len(b) >= 2 and len(a & b) / min(len(a), len(b)) >= MERGE_OVERLAP:
                parent[find(i)] = find(j)
    groups: Dict[int, List[dict]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(cands[i])
    out = []
    for gs in groups.values():
        rep = min(gs, key=lambda c: (-len(c["active"]), -(len(c["active"]) / max(c.get("members_n") or 1, 1)),
                                     c.get("type") != "industry", c["name"]))
        origins = {c["origin"] for c in gs}
        out.append({**rep, "merged": [c["name"] for c in gs if c is not rep],
                    "origin": rep["origin"] if rep["origin"] != "new" or origins == {"new"} else
                              ("continuation" if "continuation" in origins else "repair"),
                    "own_active": set(rep["active"]),
                    "active": set().union(*(c["active"] for c in gs))})
    out.sort(key=lambda g: (-len(g["active"]), g["origin"] == "new", g["name"]))
    return out


def sector_series(sec: Sequence[Pt], sec_prev: float, idx: Sequence[Pt], idx_prev: float) -> Optional[dict]:
    """
    板块、上证按分钟对齐（相对昨收的涨幅），对不齐 10 分钟就 None。
    **同一轮**：起点 = 板块、上证两个低点里更早的那个（这一轮反弹从这里开始）；「跑赢」= 起点到此刻板块比上证多涨的
    百分点（同一窗口），不是各自对昨收——高开低走的板块不会因为开盘缺口被当成跑赢。
    回踩 = 板块见底之后最深的那次回落（之后又创新高也算回踩过），回踩期间同窗口相对强度要一直 >0。
    """
    s = {t: _pct(px, sec_prev) for t, px in sec}
    i = {t: _pct(px, idx_prev) for t, px in idx}
    common = sorted(set(s) & set(i))
    if len(common) < SEC_MIN_BARS:
        return None
    sv, iv = [s[t] for t in common], [i[t] for t in common]
    i_sl = max(k for k, v in enumerate(sv) if v <= min(sv) + 1e-9)
    i_il = max(k for k, v in enumerate(iv) if v <= min(iv) + 1e-9)
    w0 = min(i_sl, i_il)
    wrs = [(sv[k] - sv[w0]) - (iv[k] - iv[w0]) for k in range(len(sv))]
    h, p, pullback = max_drawdown(sv, i_sl)
    out = {"sec_pct": round(sv[-1], 2), "idx_pct": round(iv[-1], 2), "rs_now": round(sv[-1] - iv[-1], 2),
           "round_start": _hm(common[w0]), "win_sec": round(sv[-1] - sv[w0], 2), "win_idx": round(iv[-1] - iv[w0], 2),
           "win_rs": round(wrs[-1], 2),
           "sec_low_at": _hm(common[i_sl]), "idx_low_at": _hm(common[i_il]),
           "lead_ok": common[i_sl] <= common[i_il] + timedelta(minutes=SEC_LEAD_SLACK_MIN),
           "high_at": _hm(common[h]), "high_pct": round(sv[h], 2), "day_high_pct": round(max(sv), 2),
           "pullback_pp": round(pullback, 2), "had_pullback": pullback >= SEC_PULLBACK_PP,
           "rs_held": None, "restrength": None, "bars": len(common), "bars_to": _hm(common[-1])}
    if out["had_pullback"]:
        out.update(pullback_at=_hm(common[p]), pullback_pct=round(sv[p], 2),
                   rs_min_in_pullback=round(min(wrs[h:p + 1]), 2), rs_held=min(wrs[h:p + 1]) > 0,
                   restrength=p < len(sv) - 1 and (sv[-1] - sv[p]) >= SEC_RESTRENGTH * pullback)
    return out


def breadth_of(member_facts: Sequence[dict], members_n: int) -> dict:
    got = [f for f in member_facts if f.get("status") == "ok" and f.get("pct") is not None]
    up = sum(1 for f in got if f["pct"] > 0)
    down = sum(1 for f in got if f["pct"] < 0)
    strong = [f for f in got if f.get("limit") == "sealed" or f["pct"] >= FOLLOW_PCT]
    pcts = [f["pct"] for f in got]
    return {"members": members_n, "quoted": len(got), "up": up, "down": down,
            "up_ratio": round(up / (up + down), 3) if up + down else None,
            "median_pct": round(statistics.median(pcts), 2) if pcts else None,
            "strong": len(strong), "strong_names": [f.get("name") or f.get("code") for f in
                                                   sorted(strong, key=lambda f: -f["pct"])[:6]]}


def mainline_judge(*, origin: str, br: dict, series: Optional[dict], series_error: Optional[str],
                   idx_pct_now: Optional[float]) -> Tuple[str, str, str]:
    """
    三项证据（同一轮行情里）：① 先于或伴随指数启动、同一窗口里比上证多涨 ≥1 个百分点；
    ② 回踩过、回踩时同窗口相对强度一直 >0、之后再上攻；③ 成分股上涨占比 ≥55%、≥3 只独立跟进。
    新方向只有一轮脉冲（还没回踩 / 回踩后没再走强）→「新方向待确认」。
    没有板块分时（或过期）：成分股同向快照够强就留作候选，注明缺分时证据。返回 (status, label, reason)。
    """
    if not br["members"] or br["quoted"] < max(3, br["members"] * MEMBER_MIN_COVERAGE):
        return INSUFFICIENT, "数据不足", f"成分股 {br['members']} 只，拿到行情 {br['quoted']} 只"
    b_ok = (br["up_ratio"] or 0) >= BREADTH_UP_MIN and br["strong"] >= FOLLOW_MIN
    b_txt = f"上涨占 {(br['up_ratio'] or 0):.0%}、{br['strong']} 只涨 ≥{FOLLOW_PCT:g}% 或封板"
    if not b_ok:
        return NOT_MET, "广度不够", f"{b_txt}（要 ≥{BREADTH_UP_MIN:.0%}、≥{FOLLOW_MIN} 只）"
    if series is None:
        snap = (br["median_pct"] - idx_pct_now) if br["median_pct"] is not None and idx_pct_now is not None else None
        if snap is not None and snap >= SEC_RS_MIN:
            return CANDIDATE, "候选：缺分时证据", (f"成分股中位 {br['median_pct']:+.2f}%，比上证多 {snap:.2f} 个百分点；{b_txt}；"
                                              f"板块分时不可用（{series_error or '对不齐'}），先后、同一轮和回踩没法判断")
        return NOT_MET, "没跑赢", f"成分股中位 {br['median_pct']}%，上证 {idx_pct_now}%；板块分时不可用"
    s = series
    if s["win_rs"] < SEC_RS_MIN:
        return NOT_MET, "没跑赢", (f"这一轮（{s['round_start']} 起）板块 {s['win_sec']:+.2f}、上证 {s['win_idx']:+.2f} 个百分点，"
                                 f"只多 {s['win_rs']:.2f}")
    if not s["lead_ok"]:
        return NOT_MET, "跟在指数后面", f"板块低点 {s['sec_low_at']} 晚于上证低点 {s['idx_low_at']}，不是先于或伴随"
    new = origin == "new"
    if not s["had_pullback"]:
        return PENDING, ("新方向待确认" if new else "还没回踩"), \
            f"{s['high_at']} 冲到 {s['high_pct']:+.2f}% 后还没回踩，相对强度没经过检验；{b_txt}"
    if not s["rs_held"]:
        return NOT_MET, "回踩时跑输", (f"{s['high_at']} 起回落 {s['pullback_pp']:.2f} 个百分点，期间这一轮的相对强度"
                                    f"最差 {s['rs_min_in_pullback']:+.2f}")
    if not s["restrength"]:
        return PENDING, ("新方向待确认" if new else "回踩守住，未再走强"), \
            f"{s['pullback_at']} 回踩守住（这一轮一直跑赢上证），还没收复回落的一半"
    return CONFIRMED, MAINLINE_ZH[CONFIRMED], (
        f"这一轮 {s['round_start']} 起：板块低点 {s['sec_low_at']}（上证 {s['idx_low_at']}），板块 {s['win_sec']:+.2f}、"
        f"上证 {s['win_idx']:+.2f}，多 {s['win_rs']:.2f} 个百分点；{s['high_at']} 后回踩 {s['pullback_pp']:.2f}、全程跑赢，"
        f"再上攻；{b_txt}")


# ═══ 带队个股 ══════════════════════════════════════════════════════════════

LEADER_ZH = {CONFIRMED: "带队确认", "SAME": "同向（分不出先后）", PENDING: "带队待验证",
             NOT_MET: "带队未确认", "NONE": "暂无确认带队股"}


def lead_time(*, first_time: Optional[str], pts: Optional[Sequence[Pt]], prev_close: Optional[float]) -> Tuple[Optional[str], str]:
    """「它什么时候先动」：首封 / 首次触板时刻；没有就用分时里第一次涨到 +5% 的时刻。返回 (HH:MM, 依据)。"""
    if first_time:
        return first_time[:5], "首封 / 首次触板"
    return pct5_time(pts, prev_close), f"分时第一次 +{LEAD_PCT:g}%"


def pct5_time(pts: Optional[Sequence[Pt]], prev_close: Optional[float]) -> Optional[str]:
    if pts and prev_close:
        for t, px in pts:
            if _pct(px, prev_close) >= LEAD_PCT:
                return _hm(t)
    return None


def seal_state(limit: Optional[str], broken_times: Optional[int], touched: bool) -> str:
    """封板状态单独说：只说明此刻封没封，不等于回落有承接。"""
    if limit == "sealed":
        return f"炸板 {broken_times} 次后回封" if broken_times else "封板中（没开过）"
    if touched:
        return "触板后开板"
    return "没触板"


def support_of(*, limit: Optional[str], broken_times: Optional[int], pts: Optional[Sequence[Pt]],
               prev_close: Optional[float]) -> Tuple[Optional[bool], str]:
    """
    「回落时有没有承接」：
      炸板后回封 → 有（卖压出来过、又被接回去）；
      封着没开过 → 算「此刻封住」，但写明**只说明此刻封着、没经过回落检验**；
      没封 → 看高点后最深一次回落是否守住涨幅一半以上。
    """
    if limit == "sealed":
        return True, (f"炸板 {broken_times} 次后回封——回落被接住了" if broken_times
                      else "封住没开过：只说明此刻封着，没经过回落检验")
    if not pts or not prev_close:
        return None, "没有分时，承接没法判断"
    pcts = [(t, _pct(px, prev_close)) for t, px in pts]
    vals = [v for _, v in pcts]
    if max(vals) <= 0:
        return False, "今天没红过"
    h, lo_i, dd = max_drawdown(vals)
    H, lo = vals[h], vals[lo_i]
    if dd < 1.0 or H <= 0:
        return None, f"{_hm(pcts[-1][0])} 前没有像样的回落（最多 {dd:.1f} 个百分点），承接还没经过检验"
    if lo >= H * LEADER_HOLD_SHARE and vals[-1] > 0:
        return True, f"{_hm(pcts[h][0])} 高点 {H:+.1f}% 回落到 {lo:+.1f}%（{_hm(pcts[lo_i][0])}），守住一半以上"
    return False, f"{_hm(pcts[h][0])} 高点 {H:+.1f}% 回落到 {lo:+.1f}%（{_hm(pcts[lo_i][0])}），没守住"


def _judge_one(c: dict, cands: Sequence[dict], peers: Sequence[dict], weak: set, round_start: Optional[str]) -> dict:
    """一只候选能不能当带队：先后用可比的启动事件、跟进限同板块同一轮。"""
    nm = c["name"]
    others = [p for p in peers if p["code"] != c["code"] and p.get("at") and p["code"] not in weak]
    if not c.get("touch_at"):
        if not c.get("lead_at"):
            return {"status": PENDING, "label": LEADER_ZH[PENDING], "followers": [],
                    "reason": f"{nm}：没有首封 / 触板时刻，分时也没拿到，先后没法判断"}
        return {"status": PENDING, "label": LEADER_ZH[PENDING], "followers": [],
                "reason": f"{nm} {c['lead_at']} 先到 +{LEAD_PCT:g}%，但没触板——跟进按首次触板算，依据不可比"}
    t0 = c["touch_at"]
    # 没触板、只有 +5% 时刻的候选比它早：只有它自己也有 +5% 时刻才比得了
    for x in cands:
        if x is c or x["code"] in weak or x.get("touch_at") or not x.get("pct5_at"):
            continue
        if c.get("pct5_at"):
            if x["pct5_at"] < c["pct5_at"]:
                return {"status": PENDING, "label": LEADER_ZH[PENDING], "followers": [],
                        "reason": f"{x['name']} {x['pct5_at']} 先到 +{LEAD_PCT:g}%，比 {nm}（{c['pct5_at']}）早"}
        elif x["pct5_at"] < t0:
            return {"status": PENDING, "label": LEADER_ZH[PENDING], "followers": [],
                    "reason": f"{x['name']} {x['pct5_at']} 就到 +{LEAD_PCT:g}% 了，比 {nm} 首次触板 {t0} 早；"
                              f"{nm} 的 +{LEAD_PCT:g}% 时刻没取到，依据不可比"}
    same = [p for p in others if p["at"][:5] == t0]
    earlier = [p for p in others if p["at"][:5] < t0]
    in_round = lambda p: round_start is None or p["at"][:5] >= round_start  # noqa: E731
    later = sorted((p for p in others if p["at"][:5] > t0 and in_round(p)), key=lambda p: p["at"])
    out_round = [p for p in others if p["at"][:5] > t0 and not in_round(p)]
    base = {"followers": later[:2], "same_minute": same[:3], "followers_total": len(later),
            "round_start": round_start, "outside_round": len(out_round)}
    if earlier:
        e = min(earlier, key=lambda p: p["at"])
        return {**base, "status": PENDING, "label": LEADER_ZH[PENDING],
                "reason": f"{e['name']} {e['at'][:5]} 比 {nm} {t0} 先触板，候选里没有更早的"}
    if same:
        return {**base, "status": "SAME", "label": LEADER_ZH["SAME"],
                "reason": f"{nm} 和 {'、'.join(p['name'] for p in same[:2])} 都在 {t0} 触板，同一分钟分不出先后"}
    if len(later) < LEADER_FOLLOW_MIN:
        rd = f"（这一轮从 {round_start} 起；之前的 {len(out_round)} 只不算）" if round_start and out_round else ""
        return {**base, "status": PENDING, "label": LEADER_ZH[PENDING],
                "reason": f"{nm} {t0} 先触板，但同板块同一轮里之后跟进的只有 {len(later)} 只{rd}"}
    if c.get("support") is None:
        return {**base, "status": PENDING, "label": LEADER_ZH[PENDING], "reason": f"{nm}：{c['support_txt']}"}
    return {**base, "status": CONFIRMED, "label": LEADER_ZH[CONFIRMED],
            "reason": (f"{nm} {t0}（{c['lead_basis']}）先动；{c['support_txt']}；"
                       f"之后同板块跟进 {'、'.join(p['name'] + ' ' + p['at'][:5] for p in later[:2])} 等 {len(later)} 只")}


def leader_judge(cands: Sequence[dict], peers: Sequence[dict], *, round_start: Optional[str] = None) -> dict:
    """
    cands：候选带队股（都是执行入口那个板块的成分），已按「先动时刻 → 昨日人气核心 → 连板高度 → 近期涨停 →
           流通市值小 → 价格低」排好序，每只带 touch_at（首封 / 首次触板）/ pct5_at（分时 +5%）/ lead_at / lead_basis /
           support(bool|None) / support_txt / weak（已走弱）。
    peers：**同一板块**今天触过板的成员 [{code, name, at}]。round_start = 这一轮的起点（板块分时给的），跟进要在它之后。
    最早的候选走弱了（回落没守住 / 翻绿）→ 跳过它，按它之后的行情重新评估接力者。同一分钟分不出先后只标「同向」。
    """
    if not cands:
        return {"status": "NONE", "label": LEADER_ZH["NONE"], "reason": "板块里没有昨日人气核心、近期涨停或今天率先启动的成员",
                "leader": None, "followers": []}
    weak = {c["code"] for c in cands if c.get("weak")}
    skipped: List[dict] = []
    for c in cands:
        if c["code"] in weak:
            skipped.append(c)
            continue
        res = _judge_one(c, cands, peers, weak, round_start)
        res["leader"] = c
        if skipped:
            res["relay_from"] = [{"code": x["code"], "name": x["name"], "why": x.get("weak_txt")} for x in skipped]
            res["reason"] = f"{'、'.join(x['name'] for x in skipped)} 先动但已走弱，看接力：" + res["reason"]
        return res
    return {"status": NOT_MET, "label": LEADER_ZH[NOT_MET], "leader": cands[0], "followers": [],
            "reason": "候选都走弱了：" + "；".join(f"{x['name']}（{x.get('weak_txt')}）" for x in skipped[:3])}


def pick_entry(market_status: str, mainlines: Sequence[dict], *, phase: str = "trading") -> Tuple[Optional[dict], str, Optional[int]]:
    """
    「进入买入检查」只在四件事同时成立时给：连续竞价时段、市场过关、主线确认走强、该主线有确认的带队股。
    带入带队股的代码和**该主线代表板块的 sector_id**。返回 (入口, 说明, 首屏该显示哪一条主线)——
    首屏的主线、带队股和按钮必须是同一个结果。
    """
    best = next((k for k, o in enumerate(mainlines) if o.get("status") == CONFIRMED
                 and (o.get("leader") or {}).get("status") == CONFIRMED and (o.get("leader") or {}).get("leader")), None)
    focus = best if best is not None else (0 if mainlines else None)
    if market_status != PASS:
        return None, "市场没过关，不提供买入检查入口", focus
    if phase != "trading":
        why = {"lunch": "午间休市", "closing_auction": "尾盘集合竞价", "closed": "已收盘"}.get(phase, "不在连续竞价时段")
        return None, f"{why}：结论停在这一刻的行情，不提供买入检查入口", focus
    if best is None:
        if not any(o.get("status") == CONFIRMED for o in mainlines):
            return None, "没有确认走强的主线", focus
        return None, "主线确认了，但没有确认的带队股", focus
    o = mainlines[best]
    c = o["leader"]["leader"]
    return ({"code": c["code"], "name": c.get("name"), "sector_id": o["sector"]["id"], "sector_name": o["sector"]["name"]},
            f"市场过关、{o['sector']['name']} 主线确认走强、{c.get('name')} 带队确认——去买入检查核对买点、空间、止损和仓位",
            best)


def leader_sort_key(c: dict) -> tuple:
    """先动时刻 → 昨日人气核心 → 连板高度 → 近 20 日涨停 → 流通市值小 → 价格低。不用龙头分。"""
    return (c.get("lead_at") or "99:99", not c.get("in_core"), -(c.get("board") or 0), -(c.get("lu20") or 0),
            c.get("float_mv") if c.get("float_mv") is not None else float("inf"),
            c.get("price") if c.get("price") is not None else float("inf"))
