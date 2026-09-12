"""
买入检查的规则（纯函数，2026-09-11 新增）。当前 rule_version = pretrade_v2。

输入：上下文事实（pre_trade_check_service 取的，已经按 as_of 截好）+ 用户输入。
输出：八个模块的逐项证据 + READY / WAIT / BLOCKED。

## 版本

- pretrade_v1（2026-09-11）：首版。
- pretrade_v2（2026-09-12，生产试用 + 一轮评审之后）：
  · 结论分三个维度——客观交易条件 / 执行纪律 / 风险与仓位，总体取最严的那个（总体口径不变）
  · 失效条件可以不是价格（结构 / 板块 / 时间）；原第 9 题「有没有失效条件」改成直接写出来
  · 买入理由拆成板块 / 个股 / 时机三句，没写全到不了 READY
  · q1、q2 换成更难自欺的问法（答「是」仍是一票否决）
  · 指数按个股所在的盘子看，别的指数只作背景（主板票不再被创业板指单独否决）
  · 没填失效价也按「预算 ÷ 压力损失」卡仓位（c4fb481 已上线，这里补记版本）

## 这里没有分数

每一项都是「事实 + 明文阈值 → PASS / WARN / FAIL / UNKNOWN / INFO」，阈值是本模块的
常量，跟 rule_version 一起存进每一次检查。**阈值是纪律设定，不是统计出来的**——
以后改阈值就升 rule_version，旧检查仍按旧口径可查。

## 三态

    BLOCKED  任意一项 FAIL（违反硬纪律 / 一票否决）
    WAIT     没有 FAIL，但有「尚未满足」、有警示、或某个模块整个拿不到数据
    READY    都没有 —— 只代表「没有发现冲突，可以进入你自己的最终确认」

READY ≠ 买入信号，≠ 预测上涨。结构确认没完成，永远到不了 READY（「交易必须确认」）。

## UNKNOWN ≠ FAIL

单项拿不到（历史时刻的涨跌家数、跌停数……）只列出来，不拖累结论；
**整个模块一项事实都拿不到**才算这个模块未知，结论降到 WAIT。
否则历史复盘永远是 WAIT，复盘就失去意义。
"""
from datetime import datetime, time
from typing import Dict, List, Optional

RULE_VERSION = "pretrade_v2"

# ── 纪律阈值（v1 设定，不是统计出来的）──────────────────────────────────────
EARLIEST_NORMAL_ENTRY = time(9, 45)      # 09:45 前不开普通新仓
INDEX_WEAK_PCT = -1.5                    # 任一核心指数 ≤ 此值：警示
INDEX_CRASH_PCT = -3.0                   # 任一核心指数 ≤ 此值：系统性下跌日，不开新仓
BREADTH_WEAK_RATIO = 0.30                # 上涨家数占比 < 30%：普跌
LIMIT_DOWN_WARN = 10
LIMIT_DOWN_BLOCK = 15                    # 且跌停 ≥ 涨停：负反馈占优
HIGH_BOARD_NEG_PCT = -7.0                # 昨日高标 as_of 跌到这个程度：高标负反馈
PREV_COHORT_WEAK_MEDIAN = -2.0           # 昨日涨停 as_of 中位涨跌 < 此值：亏钱效应
SECTOR_BREADTH_STRONG = 0.60
SECTOR_BREADTH_WEAK = 0.40
STOCK_VS_SECTOR_WEAK = -3.0              # 个股 − 板块中位 ≤ 此值：明显弱于板块
LIMIT_ROOM_TIGHT = 2.0                   # 距涨停 < 2%
PRICE_MISMATCH_PCT = 1.5                 # 计划价与 as_of 价差超过此值：检查的不是这个价位
MAX_TRADES_PER_DAY = 2                   # 默认 1 笔，最多 2 笔，第 3 笔禁止
REENTRY_TRADING_DAYS = 5
LOSS_STREAK_HALVE = 2                    # 连续 2 笔亏损：风险预算减半
LOSS_STREAK_BLOCK = 3                    # 连续 3 笔亏损：暂停主动进攻
DEFAULT_ACCOUNT_RISK_BUDGET_PCT = 1.5    # 单笔最多亏掉账户的 1.5%
DEFAULT_STRESS_LOSS_PCT = 8.0            # 压力损失：不假设止损价一定成交（T+1/跳空/跌停）

# 8 题都是「答是 = 一票否决」。q1 / q2 在 v2 换了问法：
#   q1 用反事实（最强的那只要是能买，我还选它吗）——比直接问「是不是买不到才买它」更难自欺
#   q2 问的是「身份替代了今天的证据」，不是「它以前是不是龙头」——高标二波本来就以此为前提，不能一刀切
MANUAL_QUESTIONS = [
    ("q1", "要是最强的那只现在能买，我就不会选它？"),
    ("q2", "主要是冲它以前是龙头——今天的走势和板块证据其实不强？"),
    ("q3", "因为我刚刚在它身上赚过？"),
    ("q4", "板块 / 高标正在明显走弱，我却想博修复？"),
    ("q5", "个股明显弱于板块，我仍想买？"),
    ("q6", "Setup 已经被破坏，我想补仓降低成本？"),
    ("q7", "我只是怕踏空 / 怕空仓？"),
    ("q8", "我想把上一笔亏损赚回来？"),
]
IMPULSE_KEYS = [k for k, _ in MANUAL_QUESTIONS]

# 买入理由三句（v2）：写不出来就是还没想清楚。失效条件单独写，见 INVALIDATION_TYPES
REASON_FIELDS = [
    ("why_sector", "为什么是这个板块", "主线是什么？板块今天在赚钱吗？"),
    ("why_stock", "为什么是这只", "它在板块里什么位置？凭什么是它不是别的？"),
    ("why_now", "为什么是现在", "哪个结构信号出现了？"),
]
# 失效 ≠ 止损价：短线的失效常常是结构 / 板块 / 时间，不一定是一个价（v2）
INVALIDATION_TYPES = [
    ("price", "价格", "跌破「计划失效价」"),
    ("structure", "结构", "如：跌回分时均价下方 / 第二次上攻失败"),
    ("sector", "板块", "如：板块龙头炸板 / 板块翻绿"),
    ("time", "时间", "如：10:30 前没突破 H1 就走"),
]
_INV_ZH = {k: label for k, label, _ in INVALIDATION_TYPES}

# 三个维度（v2）：同样是 BLOCKED，「机会不成熟」「执行错了」「仓位错了」要分得出来
DIMENSIONS = [
    ("setup", "客观交易条件", ("market", "sector", "leader", "intraday")),
    ("execution", "执行纪律", ("time", "discipline", "manual")),
    ("risk", "风险与仓位", ("risk",)),
]

LIFECYCLE_ZH = {
    "STREAKING": "连板中", "BROKEN": "刚断板", "REPAIRING": "修复中", "CROSS_SUCCESS": "穿越成功",
    "CROSS_WEAKENING": "成功后走弱", "CROSS_FAILED": "修复失败", "FADED": "周期结束",
    "UNKNOWN": "数据不足", "NO_CYCLE": "无有效周期",
}

_GROUP = {"PASS": "positive", "WARN": "caution", "FAIL": "unmet", "UNKNOWN": "unknown", "INFO": "info"}


def _n(v, dash: str = "—"):
    """拿不到的数显示成横杠——文案里不许漏出 None。"""
    return dash if v is None else v


def _item(key: str, level: str, text: str, *, group: Optional[str] = None,
          evidence: Optional[dict] = None) -> dict:
    """group：positive 做得好的 / caution 需要警惕 / unmet 尚未满足 / unknown 无法判断 / info 背景。"""
    return {"key": key, "level": level, "group": group or _GROUP[level], "text": text,
            "evidence": evidence or {}}


def _module(key: str, title: str, items: List[dict], meta: Optional[dict] = None) -> dict:
    levels = {i["level"] for i in items}
    if "FAIL" in levels:
        status = "FAIL"
    elif "WARN" in levels:
        status = "WARN"
    elif "PASS" in levels:
        status = "PASS"
    elif items and levels <= {"UNKNOWN"}:
        status = "UNKNOWN"
    else:
        status = "NEUTRAL"          # 只有背景信息，没有可判断的项
    return {"key": key, "title": title, "status": status, "items": items,
            "known": sum(1 for i in items if i["level"] != "UNKNOWN"),
            "unknown": sum(1 for i in items if i["level"] == "UNKNOWN"),
            "meta": meta or {}}


def _pct(v: Optional[float], sign: bool = True) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%" if sign else f"{v:.2f}%"


def _hm(iso: Optional[str]) -> str:
    return iso[11:16] if iso and len(iso) >= 16 else "—"


def _relevant_indexes(code: str) -> set:
    """
    指数按个股所在的盘子看：主板票看上证 + 深证成指，创业板票看创业板指 + 深证成指，科创板看上证。
    别的指数只作背景——主板票不该被创业板指单独一票否决（v2）。
    """
    if code.startswith(("300", "301")):
        return {"创业板指", "深证成指"}
    if code.startswith("688"):
        return {"上证指数"}
    return {"上证指数", "深证成指"}


def _why(meta: Optional[dict], default: str) -> str:
    notes = (meta or {}).get("notes") or []
    return notes[0] if notes else default


# ── 1. 市场许可 ──────────────────────────────────────────────────────────────

def check_market(ctx: dict) -> dict:
    mk = ctx.get("market") or {}
    items: List[dict] = []

    idx = [i for i in mk.get("indexes") or [] if i.get("pct") is not None]
    if not idx:
        items.append(_item("index", "UNKNOWN",
                           f"核心指数截至 as_of 的涨跌拿不到：{_why(mk.get('indexes_meta'), '分钟数据不可用')}"))
    else:
        names = _relevant_indexes(str((ctx.get("stock") or {}).get("code") or ""))
        rel = [i for i in idx if i.get("name") in names] or idx
        bg = [i for i in idx if i not in rel]
        worst = min(rel, key=lambda i: i["pct"])
        txt = "、".join(f"{i['name']} {_pct(i['pct'])}" for i in rel)
        if bg:
            txt += "（" + "、".join(f"{i['name']} {_pct(i['pct'])}" for i in bg) + " 只作背景）"
        if worst["pct"] <= INDEX_CRASH_PCT:
            items.append(_item("index", "FAIL", f"系统性下跌：{txt}（{worst['name']} ≤ {INDEX_CRASH_PCT}%，不开新仓）"))
        elif worst["pct"] <= INDEX_WEAK_PCT:
            items.append(_item("index", "WARN", f"指数明显走弱：{txt}"))
        else:
            items.append(_item("index", "PASS", f"指数没有明显走弱：{txt}"))
        crash_bg = [i for i in bg if i["pct"] <= INDEX_CRASH_PCT]
        if crash_bg and worst["pct"] > INDEX_CRASH_PCT:
            items.append(_item("index_other", "WARN",
                               "、".join(f"{i['name']} {_pct(i['pct'])}" for i in crash_bg)
                               + " 大跌：不是这只票的盘子，但情绪可能传导"))

    br = mk.get("breadth") or {}
    up, down = br.get("up"), br.get("down")
    if up is None or down is None:
        items.append(_item("breadth", "UNKNOWN", f"涨跌家数：{_why(br.get('meta'), '拿不到')}"))
    else:
        ratio = up / (up + down) if (up + down) else None
        txt = f"上涨 {up} / 下跌 {down}"
        if ratio is not None and ratio < BREADTH_WEAK_RATIO:
            items.append(_item("breadth", "WARN", f"市场普跌：{txt}（上涨只占 {ratio:.0%}）"))
        else:
            items.append(_item("breadth", "PASS", f"市场广度：{txt}"))

    ld, lu = br.get("limit_down"), br.get("limit_up")
    if ld is None:
        items.append(_item("limit_down", "UNKNOWN", f"跌停家数：{_why(br.get('meta'), '拿不到')}"))
    elif ld >= LIMIT_DOWN_BLOCK and ld >= (lu or 0):
        items.append(_item("limit_down", "FAIL", f"负反馈占优：跌停 {ld} 家 ≥ 涨停 {_n(lu)} 家"))
    elif ld >= LIMIT_DOWN_WARN:
        items.append(_item("limit_down", "WARN", f"跌停家数偏多：{ld} 家（涨停 {_n(lu)}）"))
    else:
        items.append(_item("limit_down", "PASS", f"跌停 {ld} 家（涨停 {_n(lu)}）"))

    hb = mk.get("high_boards") or []
    known = [h for h in hb if h.get("pct") is not None]
    if not hb:
        items.append(_item("high_boards", "INFO", "昨日没有 3 板以上的高标"))
    elif not known:
        items.append(_item("high_boards", "UNKNOWN", "昨日高标截至 as_of 的表现拿不到"))
    else:
        txt = "、".join(f"{h['name']}({h['board_prev']}板) {_pct(h['pct'])}" for h in known)
        miss = [h["name"] for h in hb if h.get("pct") is None]
        if miss:        # 停牌 / 拿不到的不算进反馈，但要说出来，不能悄悄少一只
            txt += f"；拿不到：{'、'.join(miss)}"
        if any(h["pct"] <= HIGH_BOARD_NEG_PCT for h in known):
            items.append(_item("high_boards", "WARN", f"高标负反馈：{txt}"))
        elif all(h["pct"] >= 0 for h in known):
            items.append(_item("high_boards", "PASS", f"高标仍有承接：{txt}"))
        else:
            items.append(_item("high_boards", "INFO", f"高标分化：{txt}"))

    coh = mk.get("prev_cohort") or {}
    if coh.get("median_pct") is None:
        items.append(_item("prev_cohort", "UNKNOWN",
                           f"昨日涨停今日表现：{_why(coh.get('meta'), '拿不到')}"))
    else:
        txt = f"中位 {_pct(coh['median_pct'])}（{coh['n']} 只，红盘 {coh['red_ratio']:.0%}）"
        if coh["median_pct"] < PREV_COHORT_WEAK_MEDIAN:
            items.append(_item("prev_cohort", "WARN", f"昨日涨停今日亏钱：{txt}"))
        elif coh["median_pct"] >= 0:
            items.append(_item("prev_cohort", "PASS", f"昨日涨停今日有溢价：{txt}"))
        else:
            items.append(_item("prev_cohort", "INFO", f"昨日涨停今日小幅走弱：{txt}"))

    lt = mk.get("limit_touch") or {}
    if lt.get("touched") is None:
        items.append(_item("limit_touch", "UNKNOWN",
                           f"截至 as_of 触及涨停的家数：{_why(lt.get('meta'), '拿不到')}"))
    else:
        extra = f"，最高 {lt['max_board']} 板" if lt.get("max_board") else ""
        broken = f"（此刻已开板 {lt['broken_now']} 家）" if lt.get("broken_now") is not None else ""
        items.append(_item("limit_touch", "INFO",
                           f"截至 {_hm(ctx.get('as_of'))} 触及过涨停 {lt['touched']} 家{broken}{extra}"))

    pd = mk.get("prev_day") or {}
    if pd.get("date"):
        items.append(_item("prev_day", "INFO",
                           f"昨日（{pd['date']}）：涨停 {_n(pd.get('limit_up'))} / 跌停 "
                           f"{_n(pd.get('limit_down'))}，最高 {_n(pd.get('max_height'))} 板"))
    return _module("market", "市场许可", items, mk.get("meta"))


# ── 2. 主线 / 板块 ────────────────────────────────────────────────────────────

def check_sector(ctx: dict) -> dict:
    sc = ctx.get("sector") or {}
    items: List[dict] = []
    thesis = sc.get("thesis")
    if not thesis:
        items.append(_item("thesis", "UNKNOWN", "这只票在库里没有板块归属，主线无法对照"))
        return _module("sector", "主线 / 板块", items, sc.get("meta"))

    live = sc.get("live") or {}
    if live.get("members") is None:
        items.append(_item("breadth", "UNKNOWN",
                           f"「{thesis['name']}」截至 as_of 的涨跌家数：{_why(live.get('meta'), '拿不到')}"))
    else:
        up, down = live.get("up", 0), live.get("down", 0)
        ratio = up / (up + down) if (up + down) else None
        txt = f"「{thesis['name']}」上涨 {up} / 下跌 {down}（成分 {live['members']} 只）"
        if ratio is None:
            items.append(_item("breadth", "INFO", txt))
        elif ratio >= SECTOR_BREADTH_STRONG:
            items.append(_item("breadth", "PASS", f"板块普涨：{txt}"))
        elif ratio < SECTOR_BREADTH_WEAK:
            items.append(_item("breadth", "WARN", f"板块广度不足：{txt}"))
        else:
            items.append(_item("breadth", "INFO", f"板块分化：{txt}"))
        lu_now = live.get("limit_up_now") or 0
        if lu_now >= 2 and ratio is not None and ratio < 0.5:
            items.append(_item("concentration", "WARN",
                               f"只有少数核心在涨：板块涨停 {lu_now} 只，但上涨家数不到一半——"
                               f"这是个股强，不是板块赚钱效应"))
        if live.get("median_pct") is not None:
            items.append(_item("median", "INFO",
                               f"成分股中位涨跌 {_pct(live['median_pct'])}（等权中位，不是板块指数）"))
        if (live.get("limit_down_now") or 0) >= 1:
            items.append(_item("sector_neg", "WARN", f"板块内有 {live['limit_down_now']} 只跌停"))

    rel = sc.get("stock_vs_sector")
    if rel is not None:
        txt = f"个股 {_pct(sc.get('stock_pct'))} vs 板块中位 {_pct(live.get('median_pct'))}"
        if rel <= STOCK_VS_SECTOR_WEAK:
            items.append(_item("relative", "WARN", f"个股明显弱于板块：{txt}"))
        elif rel >= 0:
            items.append(_item("relative", "PASS", f"个股强于板块中位：{txt}"))
        else:
            items.append(_item("relative", "INFO", f"个股略弱于板块中位：{txt}"))

    ct = sc.get("continuation") or {}
    if ct.get("prev_limit_ups"):
        p = ct["prev_limit_ups"]
        if ct.get("down_now") is not None and ct["down_now"] >= max(1, p / 2):
            items.append(_item("continuation", "WARN",
                               f"昨日板块涨停 {p} 只，截至 as_of 有 {ct['down_now']} 只翻绿——延续性差"))
        elif ct.get("retouched") is not None:
            items.append(_item("continuation", "INFO",
                               f"昨日板块涨停 {p} 只，截至 as_of 再次触及涨停 {ct['retouched']} 只"))

    pd = sc.get("prev_day") or {}
    if pd.get("date"):
        items.append(_item("prev_day", "INFO",
                           f"昨日（{pd['date']}）板块涨停 {_n(pd.get('limit_up_count'))} 只、"
                           f"成分股最高 {_n(pd.get('board_height'))} 板"))
    tr = sc.get("trend") or []
    if tr:
        items.append(_item("trend", "INFO",
                           "近 {} 个交易日板块涨停数：{}（只是涨停数序列，不是板块指数趋势{}）".format(
                               len(tr), " → ".join(str(_n(t["limit_up_count"], "?")) for t in tr),
                               "；? = 那天库里没有日快照" if any(t["limit_up_count"] is None for t in tr) else "")))
    return _module("sector", "主线 / 板块", items, sc.get("meta"))


# ── 3. Leader / 个股资格 ──────────────────────────────────────────────────────

def check_leader(ctx: dict) -> dict:
    ld = ctx.get("leader") or {}
    items: List[dict] = []
    lc = ld.get("lifecycle")
    if not lc:
        n60 = ld.get("board_count_60d")
        items.append(_item("lifecycle", "INFO",
                           "不属于「高标二波」：高标周期池只收曾经 ≥4 连板的票"
                           + (f"（它 60 日最高 {n60} 板）" if n60 is not None else "") + "，没有生命周期状态可看"))
    else:
        shown = lc["state"] if lc.get("state") not in (None, "UNKNOWN") else lc.get("last_valid_state")
        zh = LIFECYCLE_ZH.get(shown or "UNKNOWN", shown)
        days = f"，已 {lc['days_in_state']} 个交易日" if lc.get("days_in_state") is not None else ""
        txt = f"{lc.get('date')} 收盘时的生命周期：{zh}{days}——状态只说明值不值得观察，不等于买点"
        if shown in ("CROSS_FAILED", "FADED"):
            items.append(_item("lifecycle", "WARN", txt))
        else:
            items.append(_item("lifecycle", "INFO", txt))
        if lc.get("days_since_break") is not None:
            items.append(_item("since_break", "INFO", f"距断板 {lc['days_since_break']} 个交易日"))

    if ld.get("board_count_60d") is None:
        items.append(_item("recognition", "UNKNOWN", "历史辨识度拿不到（库里没有昨日快照）"))
    else:
        items.append(_item("recognition", "INFO",
                           f"60 日最高 {ld['board_count_60d']} 连板，近 20 日涨停 {_n(ld.get('limit_up_days_20d'))} 次，"
                           f"20 日涨幅 {_pct(ld.get('pct_change_20d'))}"))

    rs = ld.get("rs_market_20")
    if rs is None:
        items.append(_item("rs_market", "UNKNOWN", "20 日相对大盘强弱拿不到（只有高标周期快照里的票有）"))
    else:
        d3 = ld.get("rs_market_20_delta_3d")
        chg = f"，近 3 日 {d3:+.1f}" if d3 is not None else ""
        if rs < 0:
            items.append(_item("rs_market", "WARN", f"20 日跑输大盘 {rs:+.1f} 个点{chg}"))
        else:
            items.append(_item("rs_market", "PASS", f"20 日跑赢大盘 {rs:+.1f} 个点{chg}"))

    rss = ld.get("rs_sector_20")
    if rss is not None:         # 只有高标周期池里的票有；跑赢大盘不等于是板块里的强者
        if rss < 0:
            items.append(_item("rs_sector", "WARN", f"20 日跑输板块 {rss:+.1f} 个点：跑赢大盘不等于是板块里的强者"))
        else:
            items.append(_item("rs_sector", "PASS", f"20 日跑赢板块 {rss:+.1f} 个点"))

    rk = ((ctx.get("sector") or {}).get("live") or {}).get("ranking") or {}
    if rk.get("top"):           # 实时才有：板块里谁比它强，一眼看到
        top = "、".join(f"{t['name']} {_pct(t['pct'])}" for t in rk["top"])
        pos = f"排第 {rk['rank']} / {rk['total']}" if rk.get("rank") else f"不在今天有行情的 {rk['total']} 只里"
        if rk.get("rank") == 1:
            items.append(_item("sector_rank", "PASS", f"板块内领涨（{pos}）：{top}"))
        else:
            gap = f"，落后领涨 {rk['leader_gap']:.1f} 个点" if rk.get("leader_gap") is not None else ""
            items.append(_item("sector_rank", "INFO", f"板块内{pos}{gap}；领涨：{top}"))

    bp, smax = ld.get("board_prev"), ld.get("sector_max_board_prev")
    if bp is not None and smax is not None:
        if bp > 0 and bp >= smax:
            items.append(_item("sector_position", "PASS", f"昨日是板块内最高板（{bp} 板）"))
        else:
            items.append(_item("sector_position", "INFO",
                               f"昨日 {bp} 板，板块最高 {smax} 板——不是板块最高标"))

    room = ld.get("limit_room_pct")
    if room is not None:
        if room <= 0.05:
            items.append(_item("limit_room", "WARN", "已在涨停价（排板）——那是另一种交易，不在这套结构规则里"))
        elif room < LIMIT_ROOM_TIGHT:
            items.append(_item("limit_room", "WARN", f"距涨停只剩 {room:.2f}%，追高空间有限"))
        else:
            items.append(_item("limit_room", "INFO", f"距涨停 {room:.2f}%"))
    if ld.get("is_st"):
        items.append(_item("st", "WARN", "ST 股（涨跌幅 5%）"))
    return _module("leader", "Leader / 个股资格", items, ld.get("meta"))


# ── 4. 日内结构 ──────────────────────────────────────────────────────────────

def check_intraday(ctx: dict, inp: dict) -> dict:
    it = ctx.get("intraday") or {}
    items: List[dict] = []
    if it.get("quality") in (None, "UNKNOWN") or it.get("price") is None:
        items.append(_item("data", "UNKNOWN", f"日内数据：{_why(it, '拿不到')}"))
        items.append(_item("structure", "UNKNOWN", "修复→H1→回踩L1→再突破：没有分钟数据，无法判断",
                           group="unknown"))
        return _module("intraday", "日内结构", items, it)

    amt = f"，成交额 {it['amount'] / 1e8:.2f} 亿" if it.get("amount") else ""
    vw = f"，均价 {it['vwap']:.2f}" if it.get("vwap") else ""
    items.append(_item("snapshot", "INFO",
                       f"昨收 {it.get('prev_close')} · 开 {it.get('open')} · as_of 价 {it['price']}"
                       f"（{_pct(it.get('pct'))}）· 高 {it.get('high')} · 低 {it.get('low')}{vw}{amt}"
                       f"（{it.get('source')}，{it.get('quality')}）"))

    st = it.get("structure") or {}
    status = st.get("status")
    if status == "UNKNOWN" or not status:
        items.append(_item("structure", "UNKNOWN", f"结构无法回放：{st.get('reason', '数据不足')}"))
    else:
        if st.get("first_repair_at"):
            items.append(_item("repair", "PASS", f"{_hm(st['first_repair_at'])} 首次收复修复关键位（现价 > max(昨收, 均价)）"))
        else:
            items.append(_item("repair", "WARN", f"截至 as_of 没有收复修复关键位（{st.get('anchor')}）", group="unmet"))
        if st.get("state") == "FAILED":
            items.append(_item("failed", "WARN", f"{_hm(st.get('failed_at'))} 跌破修复关键位，结构失效", group="unmet"))
        elif st.get("first_repair_at"):
            # 第一波还在创新高时 H1 只是候选：回落 ≥ 阈值之后才确认（v2 改了说法，状态机没变）
            if st.get("h1") is not None and st.get("state") in ("PULLBACK", "CONFIRMED"):
                items.append(_item("h1", "PASS", f"H1 已确认 = {st['h1']}（{_hm(st.get('h1_at'))}，之后回落了"
                                                 f" ≥{st.get('pullback_min_pct')}%）"))
            elif st.get("state") == "REPAIRING":
                items.append(_item("h1", "WARN",
                                   f"第一波还在走：H1 还没确认——{st.get('h1')} 只是目前的最高收盘，价格还在创新高就不算 H1；"
                                   f"回落 ≥{st.get('pullback_min_pct')}% 才确认", group="unmet"))
            if st.get("state") == "PULLBACK" and st.get("l1") is not None:
                items.append(_item("l1", "INFO", f"回踩中：L1 候选 = {st['l1']}（{_hm(st.get('l1_at'))}），还没跌破关键位"
                                                 f"——再突破 H1 之前 L1 都可能更低"))
            elif st.get("state") == "CONFIRMED" and st.get("l1") is not None:
                items.append(_item("l1", "PASS", f"L1 = {st['l1']}（{_hm(st.get('l1_at'))}），回踩没跌破关键位"))
            if st.get("state") == "CONFIRMED":
                items.append(_item("breakout", "PASS",
                                   f"{_hm(st.get('breakout_at'))} 再次突破 H1——结构确认完成（仍然只是结构满足，不是买入指令）"))
            elif st.get("state") == "PULLBACK":
                items.append(_item("breakout", "WARN", "尚未再突破 H1：标准确认买点还没形成", group="unmet"))

    price, vwap = it.get("price"), it.get("vwap")
    if vwap:
        if price < vwap:
            items.append(_item("vwap", "WARN", f"as_of 价 {price} 低于分时均价 {vwap:.2f}"))
        else:
            items.append(_item("vwap", "PASS", f"as_of 价 {price} 在分时均价 {vwap:.2f} 之上"))

    ip = inp.get("intended_price")
    if ip and price:
        diff = (ip / price - 1) * 100
        if abs(diff) > PRICE_MISMATCH_PCT:
            items.append(_item("price_match", "WARN",
                               f"计划/实际价 {ip} 跟 as_of 时刻价 {price} 相差 {diff:+.2f}%——检查对应的不是这个价位"))
    rel = it.get("vs_market")
    if rel is not None:
        items.append(_item("vs_market", "INFO", f"相对上证 {rel:+.2f} 个点"))
    return _module("intraday", "日内结构", items, it)


# ── 5. 时间纪律 ──────────────────────────────────────────────────────────────

def check_time(ctx: dict, inp: dict) -> dict:
    items: List[dict] = []
    as_of = datetime.fromisoformat(ctx["as_of"])
    t = as_of.time()
    ans = (inp.get("answers") or {})
    cal = ctx.get("calendar") or {}
    if cal.get("behind"):
        # 数据基础可能是旧的：结论站不住，就不给 READY
        items.append(_item("calendar", "WARN",
                           f"交易日历只到 {cal.get('last')}，前一交易日按 {ctx.get('prev_trade_date')} 算可能是旧的——"
                           "「昨天」的结论（高标周期、昨日涨停、板块昨日表现）不一定是昨天，先更新数据再检查"))
    elif ctx.get("is_trading_day") is None and cal.get("last"):
        items.append(_item("calendar", "INFO", f"交易日历还没收录 {as_of.date()}（盘中常见），按交易日处理"))
    if ctx.get("is_trading_day") is False:
        items.append(_item("session", "WARN", f"{as_of.date()} 不是交易日"))
    elif t >= time(15, 0):
        items.append(_item("session", "WARN", f"{t:%H:%M:%S} 已收盘，这个时刻下不了单"))
    elif time(11, 30) < t < time(13, 0):
        items.append(_item("session", "WARN", f"{t:%H:%M:%S} 午间休市，这个时刻下不了单"))
    elif t < EARLIEST_NORMAL_ENTRY:
        a_plus = ans.get("a_plus")
        if a_plus is True:
            items.append(_item("time", "WARN",
                               f"{t:%H:%M:%S} 在 09:45 前：按你事前写好的 A+ 例外处理——例外本身就是更高风险"))
        elif a_plus is False:
            items.append(_item("time", "FAIL", f"{t:%H:%M:%S} 在 09:45 前，且没有事前的 A+ 例外：不开普通新仓"))
        else:
            items.append(_item("time", "WARN", f"{t:%H:%M:%S} 在 09:45 前：需要回答是否有事前写好的 A+ 例外",
                               group="unmet"))
    else:
        items.append(_item("time", "PASS",
                           f"{t:%H:%M:%S}，09:45 之后——时间纪律通过（这跟结构确认是两件事）"))
    return _module("time", "时间纪律", items)


# ── 6. 个人行为纪律（Trade Journal）──────────────────────────────────────────

def check_discipline(ctx: dict, inp: dict) -> dict:
    dc = ctx.get("discipline") or {}
    items: List[dict] = []
    ans = inp.get("answers") or {}
    if not dc.get("available"):
        items.append(_item("journal", "UNKNOWN", f"没能对照交易记录：{dc.get('reason', '未登录')}"))
        return _module("discipline", "个人行为纪律", items, dc.get("meta"))

    buys = dc.get("today_buys") or []
    n = len(buys)
    listed = "、".join(f"{_hm(b['trade_time'])} {b.get('stock_name') or b.get('stock_code')}" for b in buys)
    if n >= MAX_TRADES_PER_DAY:
        items.append(_item("trade_count", "FAIL", f"今天已有 {n} 笔买入（{listed}），第 {n + 1} 笔直接禁止"))
    elif n == 1:
        note = (ans.get("second_trade_note") or "").strip()
        if note:
            items.append(_item("trade_count", "INFO", f"今天第 2 笔（第一笔：{listed}）。你写的比第一笔多出的证据：「{note}」"))
        else:
            items.append(_item("trade_count", "WARN",
                               f"今天第 2 笔（第一笔：{listed}）：门槛高于第一笔——写出这笔比第一笔多了什么证据",
                               group="unmet"))
    else:
        items.append(_item("trade_count", "PASS", "今天第 1 笔新开仓"))

    recent = dc.get("recent_same_stock") or []
    if recent:
        last = recent[0]
        txt = (f"最近 {REENTRY_TRADING_DAYS} 个交易日内交易过该股"
               f"（{last['trade_time'][:16].replace('T', ' ')} {last['action']} {last['price']}）")
        fact = (ans.get("new_market_fact") or "").strip()
        if fact:
            items.append(_item("reentry", "INFO", f"{txt}；你写的新市场事实：「{fact}」"))
        else:
            items.append(_item("reentry", "FAIL", f"{txt}：快速重入必须先写出新的市场事实"))
        if dc.get("last_same_stock_pnl") is not None and dc["last_same_stock_pnl"] > 0:
            items.append(_item("profit_reentry", "WARN", "上一笔刚在它身上盈利，快速重入属高风险"))

    cl = dc.get("consecutive_losses") or 0
    if cl >= LOSS_STREAK_BLOCK:
        items.append(_item("loss_streak", "FAIL", f"连续 {cl} 笔亏损：暂停主动进攻，先复盘"))
    elif cl >= LOSS_STREAK_HALVE:
        items.append(_item("loss_streak", "WARN", f"连续 {cl} 笔亏损：风险预算减半"))
    else:
        items.append(_item("loss_streak", "INFO", f"最近连续亏损 {cl} 笔"))

    hold = dc.get("holding") or {}
    if hold.get("holding"):
        pos = f"（净仓位约 {hold['net_position_pct']:.0f}%）" if hold.get("net_position_pct") else ""
        items.append(_item("holding", "INFO", f"当前已持有该股{pos}：这笔是加仓"))
    return _module("discipline", "个人行为纪律", items, dc.get("meta"))


# ── 7. 一票否决（人工回答）──────────────────────────────────────────────────

def check_manual(inp: dict) -> dict:
    ans = inp.get("answers") or {}
    items: List[dict] = []
    unanswered = [q for k, q in MANUAL_QUESTIONS if ans.get(k) is None]
    for k, q in MANUAL_QUESTIONS:
        if ans.get(k) is True:
            items.append(_item(k, "FAIL", f"{q} —— 是：一票否决"))
    if all(ans.get(k) is False for k in IMPULSE_KEYS):
        items.append(_item("impulse", "PASS", f"{len(IMPULSE_KEYS)} 个冲动问题都答了「否」"))
    if unanswered:
        items.append(_item("unanswered", "WARN", f"还有 {len(unanswered)} 个问题没回答", group="unmet"))

    missing = [label for key, label, _ in REASON_FIELDS if not (ans.get(key) or "").strip()]
    if missing:
        items.append(_item("reasons", "WARN", f"买入理由没写全：缺「{'」「'.join(missing)}」——一句话写不出来，就是还没想清楚",
                           group="unmet"))
    else:
        items.append(_item("reasons", "PASS", "板块、个股、时机三句理由都写了"))

    itype = ans.get("invalidation_type")
    itext = (ans.get("invalidation_text") or "").strip()
    stop = inp.get("planned_stop")
    if itype == "price" or (itype is None and stop is not None):
        if stop is None:
            items.append(_item("invalidation", "WARN", "选了价格失效，但没填计划失效价", group="unmet"))
        else:
            items.append(_item("invalidation", "PASS", f"失效条件（价格）：跌破 {stop}" + (f"，{itext}" if itext else "")))
    elif itype in _INV_ZH:
        if itext:
            items.append(_item("invalidation", "PASS", f"失效条件（{_INV_ZH[itype]}）：{itext}"))
        else:
            items.append(_item("invalidation", "WARN", f"选了{_INV_ZH[itype]}失效，但没写具体是什么事实", group="unmet"))
    else:
        items.append(_item("invalidation", "WARN", "没写失效条件：什么事实出现代表你错了？", group="unmet"))
    return _module("manual", "动机与计划（人工）", items)


# ── 8. 风险与仓位 ────────────────────────────────────────────────────────────

def check_risk(ctx: dict, inp: dict) -> dict:
    items: List[dict] = []
    it = ctx.get("intraday") or {}
    price = inp.get("intended_price") or it.get("price")
    stop = inp.get("planned_stop")
    pos = inp.get("position_pct")
    budget = float(inp.get("account_risk_budget_pct") or DEFAULT_ACCOUNT_RISK_BUDGET_PCT)
    stress = float(inp.get("stress_loss_pct") or DEFAULT_STRESS_LOSS_PCT)
    cl = (ctx.get("discipline") or {}).get("consecutive_losses") or 0
    if cl >= LOSS_STREAK_HALVE:
        budget = budget / 2

    if not price:
        items.append(_item("price", "UNKNOWN", "没有计划价，也拿不到 as_of 价，风险算不出来"))
        return _module("risk", "风险与仓位", items)
    if stop is None:
        # 不用失效价也能卡住上限：真实上限 = 预算 ÷ max(结构止损, 压力损失) ≤ 预算 ÷ 压力损失。
        # 连这个都超了，填什么失效价都救不回来（2026-09-12 生产：100% 仓位只得了一句「算不出来」）
        cap = budget / stress * 100
        halved = "（连续亏损已减半）" if cl >= LOSS_STREAK_HALVE else ""
        ans = inp.get("answers") or {}
        itype = ans.get("invalidation_type")
        non_price = itype in ("structure", "sector", "time") and (ans.get("invalidation_text") or "").strip()
        if non_price:       # 失效条件本来就不是价格：不缺失效价，仓位按压力损失算（v2）
            items.append(_item("stop", "INFO", f"失效条件是{_INV_ZH[itype]}，不是价格：不用失效价，仓位按压力损失 {stress:g}% 算"))
        else:
            items.append(_item("stop", "WARN", "没填计划失效价——风险算不出来", group="unmet"))
        basis = f"风险预算 {budget:g}%{halved} ÷ 压力损失 {stress:g}%"
        if pos is not None and pos > cap + 1e-9:
            items.append(_item("position", "FAIL",
                               f"计划仓位 {pos:g}% 超过上限：就算不算失效价，只按压力损失 {stress:g}% 算，"
                               f"风险预算 {budget:g}%{halved} 最多容纳 {cap:.1f}% 仓位",
                               evidence={"max_position_pct": round(cap, 1)}))
        elif non_price and pos is not None:
            items.append(_item("position", "PASS", f"计划仓位 {pos:g}% ≤ 上限 {cap:.1f}%（{basis}）",
                               evidence={"max_position_pct": round(cap, 1)}))
        elif non_price:
            items.append(_item("position", "WARN", f"没填计划仓位；按 {basis}，最多 {cap:.1f}%",
                               group="unmet", evidence={"max_position_pct": round(cap, 1)}))
        else:           # 仓位没填也把保守上限亮出来
            items.append(_item("position", "INFO",
                               f"只按压力损失 {stress:g}% 算，仓位上限 {cap:.1f}%；填了失效价才算得准",
                               evidence={"max_position_pct": round(cap, 1)}))
    elif stop >= price:
        items.append(_item("stop", "FAIL", f"失效价 {stop} 不低于买入价 {price}——不是有效的失效条件"))
    else:
        sl = (price - stop) / price * 100
        eff = max(sl, stress)
        max_pos = budget / eff * 100
        items.append(_item("structural_loss", "INFO", f"结构止损幅度 {sl:.2f}%（{price} → {stop}）",
                           evidence={"structural_loss_pct": round(sl, 2)}))
        basis = (f"风险预算 {budget:g}%{'（连续亏损已减半）' if cl >= LOSS_STREAK_HALVE else ''} ÷ "
                 f"max(结构止损 {sl:.2f}%, 压力损失 {stress:g}%)")
        if pos is None:
            items.append(_item("position", "WARN", f"没填计划仓位；按 {basis}，最多 {max_pos:.1f}%",
                               group="unmet", evidence={"max_position_pct": round(max_pos, 1)}))
        elif pos > max_pos + 1e-9:
            items.append(_item("position", "FAIL", f"计划仓位 {pos:g}% 超过上限 {max_pos:.1f}%（{basis}）",
                               evidence={"max_position_pct": round(max_pos, 1)}))
        else:
            items.append(_item("position", "PASS", f"计划仓位 {pos:g}% ≤ 上限 {max_pos:.1f}%（{basis}）",
                               evidence={"max_position_pct": round(max_pos, 1)}))

    items.append(_item("t_plus_1", "INFO", "T+1：今天买入最早明天才能卖，失效价今天不可能成交"))
    ldp = (ctx.get("stock") or {}).get("limit_down_price")
    lp = (ctx.get("stock") or {}).get("limit_pct")
    if ldp and price:
        d1 = (ldp / price - 1) * 100
        worst = ((ldp * (1 - (lp or 10) / 100)) / price - 1) * 100
        items.append(_item("gap_risk", "INFO",
                           f"今日跌停价 {ldp}（相对买价 {d1:+.1f}%）；明天再一个跌停，最坏约 {worst:+.1f}%"
                           f"——止损价不一定能成交，所以按压力损失算仓位"))
    if it.get("amount"):
        items.append(_item("liquidity", "INFO", f"截至 as_of 成交额 {it['amount'] / 1e8:.2f} 亿（流动性参考）"))
    return _module("risk", "风险与仓位", items)


# ── 结论 ──────────────────────────────────────────────────────────────────────

def _verdict_of(items: List[dict], modules: List[dict]) -> str:
    if any(i["level"] == "FAIL" for i in items):
        return "BLOCKED"
    if any(i["group"] in ("unmet", "caution") for i in items) or any(m["status"] == "UNKNOWN" for m in modules):
        return "WAIT"
    return "READY"


def _dimension(key: str, title: str, mods: List[dict]) -> dict:
    """一个维度自己的结论 + 最主要的一两条原因（没有分数，只是把同一批项分组）。"""
    items = [i for m in mods for i in m["items"]]
    v = _verdict_of(items, mods)
    if v == "BLOCKED":
        pick = [i for i in items if i["level"] == "FAIL"]
    elif v == "WAIT":
        pick = ([i for i in items if i["group"] == "unmet" and i["level"] != "FAIL"]
                + [i for i in items if i["group"] == "caution"])
    else:
        pick = []
    texts = [i["text"] for i in pick[:2]]
    if v == "WAIT" and not pick:
        texts = [f"「{m['title']}」整块拿不到数据" for m in mods if m["status"] == "UNKNOWN"]
    lead = ("；".join(texts) + (f"……共 {len(pick)} 项" if len(pick) > 2 else "")) if texts else "没发现冲突"
    return {"key": key, "title": title, "verdict": v, "lead": lead,
            "counts": {"fail": sum(1 for i in items if i["level"] == "FAIL"),
                       "unmet": sum(1 for i in items if i["group"] == "unmet" and i["level"] != "FAIL"),
                       "caution": sum(1 for i in items if i["group"] == "caution")}}


def decide(modules: List[dict]) -> dict:
    items = [dict(i, module=m["key"]) for m in modules for i in m["items"]]
    vetoes = [i for i in items if i["level"] == "FAIL"]
    unmet = [i for i in items if i["group"] == "unmet" and i["level"] != "FAIL"]
    cautions = [i for i in items if i["group"] == "caution"]
    unknowns = [i for i in items if i["group"] == "unknown"]
    positives = [i for i in items if i["group"] == "positive"]
    blind = [m["title"] for m in modules if m["status"] == "UNKNOWN"]
    by_key = {m["key"]: m for m in modules}
    dims = [_dimension(k, t, [by_key[x] for x in keys if x in by_key]) for k, t, keys in DIMENSIONS]

    if vetoes:
        verdict = "BLOCKED"
        blocked = [d["title"] for d in dims if d["verdict"] == "BLOCKED"]
        others = [f"{d['title']}本身是 {d['verdict']}" for d in dims if d["verdict"] != "BLOCKED"]
        summary = (f"{'、'.join(blocked)}有硬性否决" + (f"；{'，'.join(others)}" if others else "")
                   + "。先处理这些，再谈买点。")
    elif unmet or cautions or blind:
        verdict = "WAIT"
        parts = []
        if unmet:
            parts.append(f"{len(unmet)} 项尚未满足")
        if cautions:
            parts.append(f"{len(cautions)} 项需要警惕")
        if blind:
            parts.append(f"{'、'.join(blind)}整块拿不到数据")
        structure_unmet = any(i["module"] == "intraday" and i["group"] == "unmet" for i in items)
        summary = ("没有硬性违规，但" + "、".join(parts) + "。"
                   + ("结构确认还没完成——可以继续观察，不是确认买点。" if structure_unmet
                      else "可以继续观察，证据还不够。"))
    else:
        verdict = "READY"
        summary = ("READY FOR MANUAL DECISION：市场、主线、个股、结构与风险没有明显冲突。"
                   "这不是买入信号；是否执行、买多少，仍由你决定。")
    return {"verdict": verdict, "summary": summary, "rule_version": RULE_VERSION, "dimensions": dims,
            "vetoes": vetoes, "unmet": unmet, "cautions": cautions,
            "unknowns": unknowns, "positives": positives}


def evaluate(ctx: dict, inp: dict) -> dict:
    modules = [
        check_market(ctx), check_sector(ctx), check_leader(ctx), check_intraday(ctx, inp),
        check_time(ctx, inp), check_discipline(ctx, inp), check_manual(inp), check_risk(ctx, inp),
    ]
    return {"modules": modules, "decision": decide(modules)}
