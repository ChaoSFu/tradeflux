"""
买入识别 · 规则（buy_recog_v1，2026-11-23）。**全是纯函数**：事实进、结论 + 证据出，不碰库、不碰网络。
事实从哪来、怎么核实，在 buy_recognition_service.py。

服务龙头弱转强交易：只回答「此刻该不该去买入检查」，不给买点、空间、止损、仓位——那些由
/pre-trade-check 继续核对。三关：

  市场过关   跌停家数（硬条件）· 最大空头止跌 · 大盘止跌放量 · 高标负反馈收敛
  主线走强   板块先于 / 伴随指数走强且跑赢 · 回踩后相对强度仍在 · 成分股有广度、多只独立跟进
  带队个股   它什么时候先动 · 回落有没有承接 · 后来哪几只跟进

## 每一项四种结论，不混用

  PASS          通过
  BLOCK         拦截：有明确的拦截证据（跌停 >10、最大空头封死 / 回封 / 创新低、高位跌停）
  NOT_MET       条件未满足：数据齐了，但条件没到
  INSUFFICIENT  数据不足：缺数、过期、覆盖不全——**一律不放行**

市场合起来：有 BLOCK 先报 BLOCK，其次 NOT_MET，再次 INSUFFICIENT，全部 PASS 才是「市场过关」。

## 阈值

跌停 0–10 / 11–20 / >20 是用户的交易纪律。其余（反弹幅度、回踩深度、守住几分钟……）都是**工程初始值**，
没拿历史回测过——上线后看实战再调，调之前先量（[[feedback-measure-dont-assume]]）。
**不用龙头分 / 情绪分**：自造的黑箱分数，新模块不依赖（排序只用连板高度、近期涨停、流通市值、价格）。
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

RULE_VERSION = "buy_recog_v1"

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
IDX_HOLD_BARS = 5           # 指数低点之后至少 5 根没再破
IDX_REBOUND_PCT = 0.3       # 指数离低点至少反弹 0.3%
IDX_MIN_PASS = 2            # 至少两只核心指数
VOL_RATIO_MIN = 1.0         # 同期比 ≥ 1.0 算放量
BREADTH_WEAK = 0.4          # 上涨家数占比 < 40%，指数却守住反弹 = 权重托举
HM_MIN_SAMPLE = 5           # 高标样本至少 5 只拿到行情
HM_MIN_COVERAGE = 0.8       # 且覆盖样本 80%
HM_RED_MIN = 0.5            # 红盘占比
HM_RECHECK_S = 60           # 两次观测至少隔 60 秒，第二次仍为正才算「转正且未再走弱」
HM_WORSEN_PP = 0.5          # 中位涨幅比上一次低 0.5 个百分点以上 = 又走弱了
HM_IMPROVE_PP = 1.0         # 比当天最差那次高 1 个百分点 = 修复中
MERGE_OVERLAP = 0.6         # 两个板块今天的活跃成分重合 ≥ 60%（按少的那边算）就归并
NEW_MIN_ACTIVE = 3          # 新方向：关注板块里今天涨停 + 触板至少 3 只
SEC_MIN_BARS = 10           # 板块 / 指数分时至少对齐 10 分钟才判
SEC_RS_MIN = 1.0            # 板块此刻跑赢上证 ≥ 1 个百分点
SEC_LEAD_SLACK_MIN = 5      # 板块低点不晚于上证低点 5 分钟 = 先于或伴随
SEC_PULLBACK_PP = 0.5       # 板块从高点回落 ≥ 0.5 个百分点才算回踩
SEC_RESTRENGTH = 0.5        # 回踩后收复回落的一半以上 = 再度走强
BREADTH_UP_MIN = 0.55       # 成分股上涨占比
FOLLOW_PCT = 3.0            # 「跟进」= 涨 ≥3% 或封板
FOLLOW_MIN = 3              # 至少 3 只独立成分股跟进
MEMBER_MIN_COVERAGE = 0.6   # 成分股拿到行情的比例
LEAD_PCT = 5.0              # 个股「先动」= 首封 / 首次触板时刻，没有就用第一次涨到 +5% 的时刻
LEADER_FOLLOW_MIN = 2       # 带队后面至少 2 只同题材跟进
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


# ═══ 市场过关 ①：跌停家数 ═══════════════════════════════════════════════════

def limit_down_item(*, phase_ok: bool, phase_note: str, pool_ok: bool, pool_error: Optional[str],
                    pool_tc: Optional[int], rows: Sequence[dict], verify: Dict[str, str],
                    updown_ld: Optional[int], updown_ok: bool, market_time: Optional[datetime],
                    source_meta: dict) -> dict:
    """
    跌停家数是硬条件：「非 ST、沪深、**此刻仍封跌停**」的可靠计数。
    rows = 跌停池行；verify[code] = sealed（行情核过，此刻仍在跌停）/ left（行情显示已离开跌停）/
    stale（行情不是今天）/ missing（没拿到行情）。

    · 跌停池空响应、超时、缺页（tc 比行数多）、行情显示是旧交易日 → 数据不足，不放行。
    · 有几只没核上行情：计数只能给区间 [下限, 上限]，区间跨过 10 / 20 就是数据不足。
    · 东财涨跌分布（含 ST、北交所）是另一口径：只用来对照，以及「它都 ≤10」时兜底（非 ST 一定更少）。
    """
    T = "跌停家数"
    facts: Dict[str, Any] = {"pool_rows": len(rows), "pool_tc": pool_tc, "updown_limit_down": updown_ld}
    if not phase_ok:
        return item("limit_down", T, INSUFFICIENT, "未开盘", phase_note, facts=facts, meta=source_meta)

    def tier(n: int) -> Tuple[str, str]:
        if n > LD_NO_NEW_MAX:
            return BLOCK, "停手 · 空仓"
        if n > LD_PASS_MAX:
            return BLOCK, "不开新仓"
        return PASS, "≤10 家"

    sealed = [r for r in rows if verify.get(r["code"]) == "sealed"]
    left = [r for r in rows if verify.get(r["code"]) == "left"]
    stale = [r for r in rows if verify.get(r["code"]) == "stale"]
    unknown = [r for r in rows if verify.get(r["code"]) in (None, "missing")]
    facts.update(sealed_now=len(sealed), left_pool=len(left), stale=len(stale), unverified=len(unknown))

    # 跌停池本身不可信：没拿到 / 空响应 / 缺页 / 是旧交易日的
    why_bad = None
    if not pool_ok:
        why_bad = f"跌停池没拿到：{pool_error or '未知原因'}"
    elif pool_tc is not None and pool_tc > len(rows):
        why_bad = f"跌停池缺页：接口说有 {pool_tc} 只，只拿到 {len(rows)} 行"
    elif rows and len(stale) * 2 >= len(rows):
        why_bad = f"跌停池里 {len(stale)}/{len(rows)} 只的行情不是今天——多半还是上一交易日的名单"
    elif rows and market_time is not None and any(
            r.get("last_seal_time") and r["last_seal_time"] > market_time.strftime("%H:%M:%S") for r in rows):
        why_bad = "跌停池里有封板时间晚于此刻行情时间的票——是旧交易日的名单"
    if why_bad:
        # 兜底：涨跌分布（含 ST / 北交所，是非 ST 口径的上界）都 ≤10，非 ST 一定 ≤10
        if updown_ok and updown_ld is not None and updown_ld <= LD_PASS_MAX:
            return item("limit_down", T, PASS, f"{updown_ld} 家以内",
                        f"{why_bad}；但东财涨跌分布（含 ST、北交所）此刻只有 {updown_ld} 家，非 ST 只会更少",
                        count=None, count_range=[0, updown_ld], facts=facts, meta=source_meta, names=[])
        return item("limit_down", T, INSUFFICIENT, "数据不足", why_bad, count=None, facts=facts, meta=source_meta)

    lo, hi = len(sealed), len(sealed) + len(unknown)
    names = [{**r, "verified": verify.get(r["code"])} for r in sealed + unknown]
    if pool_tc == 0 or not rows:
        # 接口给了空池：真 0 只。但涨跌分布说 >10 时分不清「全是 ST」还是跌停池漏了 → 不放行
        if updown_ok and updown_ld is not None and updown_ld > LD_PASS_MAX:
            return item("limit_down", T, INSUFFICIENT, "数据不足",
                        f"跌停池 0 只，但东财涨跌分布（含 ST、北交所）有 {updown_ld} 家，分不清是不是跌停池漏了",
                        count=None, facts=facts, meta=source_meta, names=[])
        return item("limit_down", T, PASS, "0 家", "跌停池此刻没有非 ST 跌停股", count=0, facts=facts,
                    meta=source_meta, names=[])
    s_lo, l_lo = tier(lo)
    s_hi, l_hi = tier(hi)
    note = []
    if left:
        note.append(f"{len(left)} 只池里还挂着、行情已离开跌停价，不计")
    if updown_ok and updown_ld is not None:
        note.append(f"东财涨跌分布（含 ST、北交所）{updown_ld} 家")
    tail = "；".join(note)
    if (s_lo, l_lo) != (s_hi, l_hi):
        return item("limit_down", T, INSUFFICIENT, "数据不足",
                    f"{len(unknown)} 只没核上行情，此刻在 {lo}~{hi} 家之间，跨了档位" + (f"；{tail}" if tail else ""),
                    count=None, count_range=[lo, hi], facts=facts, meta=source_meta, names=names)
    reason = f"此刻仍封跌停 {lo} 家" + (f"（另有 {len(unknown)} 只没核上行情，最多 {hi} 家）" if unknown else "")
    return item("limit_down", T, s_lo, l_lo, reason + (f"；{tail}" if tail else ""),
                count=lo, count_range=[lo, hi], facts=facts, meta=source_meta, names=names)


# ═══ 市场过关 ②：最大空头止跌 ══════════════════════════════════════════════

SEALED, RESEALED, NEW_LOW, TESTING, STOPPED, UNKNOWN = "SEALED", "RESEALED", "NEW_LOW", "TESTING", "STOPPED", "UNKNOWN"
BEAR_ZH = {SEALED: "封死跌停", RESEALED: "开板后回封", NEW_LOW: "还在创新低", TESTING: "离开低点，还没回踩确认",
           STOPPED: "止跌", UNKNOWN: "无法判断"}


def judge_bear(pts: Sequence[Pt], *, prev_close: Optional[float], ld_price: Optional[float],
               price_now: Optional[float], now: Optional[datetime] = None) -> dict:
    """
    一只空头标的此刻止没止跌。pts = 今天已走完的分钟价；price_now = 此刻行情价。

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


def bear_item(targets: Sequence[dict], *, phase_ok: bool, phase_note: str, notes: Sequence[str] = ()) -> dict:
    """targets: [{code, name, basis, state: judge_bear 的结果, …}]。全部止跌才通过。"""
    T = "最大空头"
    if not phase_ok:
        return item("bear", T, INSUFFICIENT, "未开盘", phase_note, targets=list(targets), notes=list(notes))
    if not targets:
        return item("bear", T, INSUFFICIENT, "没锁定到标的", "近 3 日领跌股和跌停池封单最大的都没拿到",
                    targets=[], notes=list(notes))
    states = [t["judge"]["state"] for t in targets]
    bad = [t for t in targets if t["judge"]["state"] in (SEALED, RESEALED, NEW_LOW)]
    if bad:
        t = bad[0]
        return item("bear", T, BLOCK, BEAR_ZH[t["judge"]["state"]],
                    f"{t['name'] or t['code']}：{t['judge']['evidence']}——仍在创新低、强封或开板后回封，就等",
                    targets=list(targets), notes=list(notes))
    if any(s == UNKNOWN for s in states):
        t = next(t for t in targets if t["judge"]["state"] == UNKNOWN)
        return item("bear", T, INSUFFICIENT, "数据不足", f"{t['name'] or t['code']}：{t['judge']['evidence']}",
                    targets=list(targets), notes=list(notes))
    if any(s == TESTING for s in states):
        t = next(t for t in targets if t["judge"]["state"] == TESTING)
        return item("bear", T, NOT_MET, "还没止跌", f"{t['name'] or t['code']}：{t['judge']['evidence']}",
                    targets=list(targets), notes=list(notes))
    return item("bear", T, PASS, "已止跌", "；".join(f"{t['name'] or t['code']}：{t['judge']['evidence']}" for t in targets),
                targets=list(targets), notes=list(notes))


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
          "pct": round(_pct(cur, prev_close), 2)}
    if cur < low:
        return {**ev, "ok": False, "evidence": f"此刻跌破 {_hm(pts[i_low][0])} 的低点"}
    if held < IDX_HOLD_BARS:
        return {**ev, "ok": False, "evidence": f"低点 {_hm(pts[i_low][0])} 刚出来（{held} 分钟）"}
    if rebound < IDX_REBOUND_PCT:
        return {**ev, "ok": False, "evidence": f"低点 {_hm(pts[i_low][0])} 守住 {held} 分钟，但只反弹 {rebound:.2f}%"}
    return {**ev, "ok": True, "evidence": f"低点 {_hm(pts[i_low][0])} 守住 {held} 分钟，反弹 {rebound:.2f}%"}


def index_item(indices: Sequence[dict], *, volume: Optional[dict], breadth: Optional[dict],
               phase_ok: bool, phase_note: str) -> dict:
    """
    indices: [{code, name, judge}]；volume: {ratio, amount, prev_same, same_as_of, ...} 或 None；
    breadth: {up, down} 或 None。至少两只核心指数守住反弹 + 同期比 ≥1 + 上涨占比 ≥40% 才通过；
    指数守住了、上涨家数却不到四成 → 「权重托举」。
    """
    T = "大盘止跌放量"
    extra = {"indices": list(indices), "volume": volume, "breadth": breadth}
    if not phase_ok:
        return item("index", T, INSUFFICIENT, "未开盘", phase_note, **extra)
    have = [x for x in indices if x["judge"].get("ok") is not None]
    ok = [x for x in have if x["judge"]["ok"]]
    if len(have) < IDX_MIN_PASS:
        return item("index", T, INSUFFICIENT, "数据不足", f"核心指数分时只拿到 {len(have)} 只", **extra)
    if len(ok) < IDX_MIN_PASS:
        return item("index", T, NOT_MET, "未止跌",
                    "低点守住并反弹的只有 " + ("、".join(x["name"] for x in ok) or "0 只") + "，要至少两只", **extra)
    if not volume or volume.get("ratio") is None:
        return item("index", T, INSUFFICIENT, "数据不足", "拿不到同期成交额，放量没法判断", **extra)
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
                    f"{'、'.join(x['name'] for x in ok)}守住反弹，但成交额只有昨日同期的 {r:.2f} 倍（{volume.get('same_as_of') or '—'}）", **extra)
    return item("index", T, PASS, "止跌放量",
                f"{'、'.join(x['name'] for x in ok)}守住反弹；同期比 {r:.2f}（{volume.get('same_as_of') or '—'}）；上涨占 {up_ratio:.0%}", **extra)


# ═══ 市场过关 ④：高标负反馈收敛 ════════════════════════════════════════════

def highmark_stats(facts: Sequence[dict]) -> dict:
    """facts：样本里每只的此刻事实（live_fact 的字典）。只统计拿到今天行情的。"""
    got = [f for f in facts if f.get("status") == "ok" and f.get("pct") is not None]
    pcts = [f["pct"] for f in got]
    return {"sample": len(facts), "quoted": len(got),
            "limit_down": sum(1 for f in got if f.get("limit") == "limit_down"),
            "median_pct": round(statistics.median(pcts), 2) if pcts else None,
            "red_ratio": round(sum(1 for p in pcts if p > 0) / len(pcts), 3) if pcts else None,
            "limit_down_names": [f.get("name") or f.get("code") for f in got if f.get("limit") == "limit_down"]}


def highmark_item(stats: dict, *, prev: Optional[dict], worst_median: Optional[float],
                  phase_ok: bool, phase_note: str, scope: str) -> dict:
    """
    prev = 至少 HM_RECHECK_S 秒之前的上一次观测 {median_pct, at}；worst_median = 今天观测到的最差中位涨幅。
    高位还有跌停 → 拦截。中位 >0 且红盘 ≥50%：上一次也 >0 且没再走弱 → 通过；否则「转正待确认」。
    仍为负：比当天最差那次高 1 个百分点 → 修复中；否则负反馈未收敛。
    """
    T = "高标负反馈"
    extra = {"stats": stats, "scope": scope, "prev": prev, "worst_median": worst_median}
    if not phase_ok:
        return item("highmark", T, INSUFFICIENT, "未开盘", phase_note, **extra)
    n, q = stats["sample"], stats["quoted"]
    if q < HM_MIN_SAMPLE or (n and q / n < HM_MIN_COVERAGE):
        return item("highmark", T, INSUFFICIENT, "样本不足", f"昨日高标样本 {n} 只，拿到行情 {q} 只，覆盖不够", **extra)
    med, red = stats["median_pct"], stats["red_ratio"]
    desc = f"样本 {q} 只：中位 {med:+.2f}%，红盘 {red:.0%}，跌停 {stats['limit_down']} 只"
    if stats["limit_down"]:
        return item("highmark", T, BLOCK, "高位跌停", f"{desc}（{'、'.join(stats['limit_down_names'][:3])}）", **extra)
    if med > 0 and red >= HM_RED_MIN:
        if prev is None or prev.get("median_pct") is None:
            return item("highmark", T, NOT_MET, "转正待确认", f"{desc}；要再观测一次（≥{HM_RECHECK_S} 秒后）没再走弱才算", **extra)
        if prev["median_pct"] <= 0:
            return item("highmark", T, NOT_MET, "刚转正", f"{desc}；上一次（{prev.get('at')}）中位还是 {prev['median_pct']:+.2f}%", **extra)
        if med < prev["median_pct"] - HM_WORSEN_PP:
            return item("highmark", T, NOT_MET, "又在走弱", f"{desc}；比上一次（{prev.get('at')}）{prev['median_pct']:+.2f}% 低", **extra)
        return item("highmark", T, PASS, "负反馈收敛", f"{desc}；上一次（{prev.get('at')}）{prev['median_pct']:+.2f}%，没再走弱", **extra)
    if worst_median is not None and med >= worst_median + HM_IMPROVE_PP:
        return item("highmark", T, NOT_MET, "修复中", f"{desc}；比今天最差的 {worst_median:+.2f}% 好了", **extra)
    return item("highmark", T, NOT_MET, "负反馈未收敛", desc, **extra)


# ═══ 主线走强 ══════════════════════════════════════════════════════════════

CONFIRMED, PENDING, CANDIDATE = "CONFIRMED", "PENDING", "CANDIDATE"
MAINLINE_ZH = {CONFIRMED: "主线确认走强", PENDING: "待确认", CANDIDATE: "候选（缺分时证据）",
               NOT_MET: "未走强", INSUFFICIENT: "数据不足"}


def merge_groups(cands: Sequence[dict]) -> List[dict]:
    """
    重叠概念归并：今天的活跃成分（涨停 + 触板）重合 ≥60%（按少的那边算，两边都 ≥2 只）就并成一组。
    代表板块 = 最能解释这轮上涨的那个：活跃只数多 → 活跃占成分比例高 → 行业优先 → 名字。
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
                    "origin": "continuation" if "continuation" in origins else
                              "repair" if "repair" in origins else "new",
                    "active": set().union(*(c["active"] for c in gs))})
    out.sort(key=lambda g: (-len(g["active"]), g["origin"] == "new", g["name"]))
    return out


def sector_series(sec: Sequence[Pt], sec_prev: float, idx: Sequence[Pt], idx_prev: float) -> Optional[dict]:
    """板块、上证按分钟对齐（相对昨收的涨幅），算先后、回踩、回踩时的相对强度。对不齐 10 分钟就 None。"""
    s = {t: _pct(px, sec_prev) for t, px in sec}
    i = {t: _pct(px, idx_prev) for t, px in idx}
    common = sorted(set(s) & set(i))
    if len(common) < SEC_MIN_BARS:
        return None
    sv, iv = [s[t] for t in common], [i[t] for t in common]
    rs = [a - b for a, b in zip(sv, iv)]
    t_sl = common[max(k for k, v in enumerate(sv) if v <= min(sv) + 1e-9)]
    t_il = common[max(k for k, v in enumerate(iv) if v <= min(iv) + 1e-9)]
    # 回踩 = 板块见底之后最深的那次回落（之后又创新高也算回踩过）
    i_sl = common.index(t_sl)
    h, p, pullback = max_drawdown(sv, i_sl)
    out = {"sec_pct": round(sv[-1], 2), "idx_pct": round(iv[-1], 2), "rs_now": round(rs[-1], 2),
           "sec_low_at": _hm(t_sl), "idx_low_at": _hm(t_il),
           "lead_ok": t_sl <= t_il + timedelta(minutes=SEC_LEAD_SLACK_MIN),
           "high_at": _hm(common[h]), "high_pct": round(sv[h], 2), "day_high_pct": round(max(sv), 2),
           "pullback_pp": round(pullback, 2), "had_pullback": pullback >= SEC_PULLBACK_PP,
           "rs_held": None, "restrength": None, "bars": len(common)}
    if out["had_pullback"]:
        out.update(pullback_at=_hm(common[p]), pullback_pct=round(sv[p], 2),
                   rs_min_in_pullback=round(min(rs[h:p + 1]), 2), rs_held=min(rs[h:p + 1]) > 0,
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
    三项证据（同一轮行情里）：① 先于或伴随指数走强、此刻跑赢上证 ≥1 个百分点；
    ② 回踩过、回踩时相对强度仍在（一直跑赢上证）、之后再度走强；③ 成分股上涨占比 ≥55%、≥3 只独立跟进。
    新方向只有一轮脉冲（还没回踩 / 回踩后没再走强）→「新方向待确认」。
    没有板块分时：成分股同向快照够强就留作候选，注明缺分时证据。返回 (status, label, reason)。
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
            return CANDIDATE, "候选：缺分时证据", (f"成分股中位 {br['median_pct']:+.2f}%，跑赢上证 {snap:.2f} 个百分点；{b_txt}；"
                                              f"板块分时没拿到（{series_error or '对不齐'}），先后和回踩没法判断")
        return NOT_MET, "没跑赢", f"成分股中位 {br['median_pct']}%，上证 {idx_pct_now}%；板块分时没拿到"
    s = series
    if s["rs_now"] < SEC_RS_MIN:
        return NOT_MET, "没跑赢", f"板块 {s['sec_pct']:+.2f}%、上证 {s['idx_pct']:+.2f}%，只多 {s['rs_now']:.2f} 个百分点"
    if not s["lead_ok"]:
        return NOT_MET, "跟在指数后面", f"板块低点 {s['sec_low_at']} 晚于上证低点 {s['idx_low_at']}，不是先于或伴随"
    new = origin == "new"
    if not s["had_pullback"]:
        return PENDING, ("新方向待确认" if new else "还没回踩"), \
            f"{s['high_at']} 冲到 {s['high_pct']:+.2f}% 后还没回踩，相对强度没经过检验；{b_txt}"
    if not s["rs_held"]:
        return NOT_MET, "回踩时跑输", f"{s['high_at']} 起回落 {s['pullback_pp']:.2f} 个百分点，期间最差比上证低 {abs(s['rs_min_in_pullback']):.2f}"
    if not s["restrength"]:
        return PENDING, ("新方向待确认" if new else "回踩守住，未再走强"), \
            f"{s['pullback_at']} 回踩守住（一直跑赢上证），还没收复回落的一半"
    return CONFIRMED, MAINLINE_ZH[CONFIRMED], (
        f"低点 {s['sec_low_at']}（上证 {s['idx_low_at']}）；此刻跑赢上证 {s['rs_now']:.2f}；"
        f"{s['high_at']} 后回踩 {s['pullback_pp']:.2f} 个百分点、全程跑赢，再度走强；{b_txt}")


# ═══ 带队个股 ══════════════════════════════════════════════════════════════

LEADER_ZH = {CONFIRMED: "带队确认", "SAME": "同向（分不出先后）", PENDING: "带队待验证",
             NOT_MET: "带队未确认", "NONE": "暂无确认带队股"}


def lead_time(*, first_time: Optional[str], pts: Optional[Sequence[Pt]], prev_close: Optional[float]) -> Tuple[Optional[str], str]:
    """「它什么时候先动」：首封 / 首次触板时刻；没有就用分时里第一次涨到 +5% 的时刻。返回 (HH:MM, 依据)。"""
    if first_time:
        return first_time[:5], "首封 / 首次触板"
    if pts and prev_close:
        for t, px in pts:
            if _pct(px, prev_close) >= LEAD_PCT:
                return _hm(t), f"分时第一次 +{LEAD_PCT:g}%"
    return None, ""


def support_of(*, limit: Optional[str], broken_times: Optional[int], pts: Optional[Sequence[Pt]],
               prev_close: Optional[float]) -> Tuple[Optional[bool], str]:
    """「回落时有没有承接」：封着 → 有（炸过板又回封也算）；没封 → 高点后的回落守住涨幅一半以上。"""
    if limit == "sealed":
        return True, (f"炸板 {broken_times} 次后回封" if broken_times else "封住，没给回落")
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


def leader_judge(cands: Sequence[dict], peers: Sequence[dict]) -> dict:
    """
    cands：候选带队股，已按「先动时刻 → 昨日人气核心 → 连板高度 → 近期涨停 → 流通市值小 → 价格低」排好序，
           每只带 lead_at / lead_basis / support(bool|None) / support_txt。
    peers：同组其他活跃股（有首封 / 触板时刻的），[{code, name, at}]。
    带队要回答三件事：什么时候先动、回落有没有承接、后来哪几只跟进。同一分钟分不出先后只标「同向」。
    """
    if not cands:
        return {"status": "NONE", "label": LEADER_ZH["NONE"], "reason": "组里没有昨日人气核心、近期涨停或今天率先启动的成员",
                "leader": None, "followers": []}
    c = cands[0]
    if not c.get("lead_at"):
        return {"status": PENDING, "label": LEADER_ZH[PENDING], "leader": c, "followers": [],
                "reason": f"{c['name']}：没有首封时刻，分时也没拿到，先后没法判断"}
    others = [p for p in peers if p["code"] != c["code"] and p.get("at")]
    same = [p for p in others if p["at"][:5] == c["lead_at"]]
    earlier = [p for p in others if p["at"][:5] < c["lead_at"]]
    later = sorted((p for p in others if p["at"][:5] > c["lead_at"]), key=lambda p: p["at"])
    base = {"leader": c, "followers": later[:2], "same_minute": same[:3], "followers_total": len(later)}
    if earlier:
        e = min(earlier, key=lambda p: p["at"])
        return {**base, "status": PENDING, "label": LEADER_ZH[PENDING],
                "reason": f"{e['name']} {e['at'][:5]} 比 {c['name']} {c['lead_at']} 先动，候选里没有更早的"}
    if same:
        return {**base, "status": "SAME", "label": LEADER_ZH["SAME"],
                "reason": f"{c['name']} 和 {'、'.join(p['name'] for p in same[:2])} 都在 {c['lead_at']}，同一分钟分不出先后"}
    if len(later) < LEADER_FOLLOW_MIN:
        return {**base, "status": PENDING, "label": LEADER_ZH[PENDING],
                "reason": f"{c['name']} {c['lead_at']} 先动，但之后跟进的只有 {len(later)} 只"}
    if c.get("support") is False:
        return {**base, "status": NOT_MET, "label": LEADER_ZH[NOT_MET], "reason": f"{c['name']}：{c['support_txt']}"}
    if c.get("support") is None:
        return {**base, "status": PENDING, "label": LEADER_ZH[PENDING], "reason": f"{c['name']}：{c['support_txt']}"}
    return {**base, "status": CONFIRMED, "label": LEADER_ZH[CONFIRMED],
            "reason": (f"{c['name']} {c['lead_at']}（{c['lead_basis']}）先动；{c['support_txt']}；"
                       f"之后跟进 {'、'.join(p['name'] + ' ' + p['at'][:5] for p in later[:2])} 等 {len(later)} 只")}


def pick_entry(market_status: str, mainlines: Sequence[dict]) -> Tuple[Optional[dict], str]:
    """
    「进入买入检查」只在三件事同时成立时给：市场过关、主线确认走强、该主线有确认的带队股。
    带入带队股的代码和**该主线代表板块的 sector_id**。返回 (入口, 说明)。
    """
    if market_status != PASS:
        return None, "市场没过关，不提供买入检查入口"
    for o in mainlines:
        ld = o.get("leader") or {}
        if o.get("status") == CONFIRMED and ld.get("status") == CONFIRMED and ld.get("leader"):
            c = ld["leader"]
            return ({"code": c["code"], "name": c.get("name"), "sector_id": o["sector"]["id"],
                     "sector_name": o["sector"]["name"]},
                    f"市场过关、{o['sector']['name']} 主线确认走强、{c.get('name')} 带队确认——"
                    "去买入检查核对买点、空间、止损和仓位")
    if not any(o.get("status") == CONFIRMED for o in mainlines):
        return None, "没有确认走强的主线"
    return None, "主线确认了，但没有确认的带队股"


def leader_sort_key(c: dict) -> tuple:
    """先动时刻 → 昨日人气核心 → 连板高度 → 近 20 日涨停 → 流通市值小 → 价格低。不用龙头分。"""
    return (c.get("lead_at") or "99:99", not c.get("in_core"), -(c.get("board") or 0), -(c.get("lu20") or 0),
            c.get("float_mv") if c.get("float_mv") is not None else float("inf"),
            c.get("price") if c.get("price") is not None else float("inf"))
