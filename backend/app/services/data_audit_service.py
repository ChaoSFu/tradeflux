"""
数据体检（2026-09-12）：定期查各张按日写的表缺了什么，并把每类缺口的补法固定下来。

## 为什么要有

日更里很多步骤是「独立步骤，失败不影响主流程」——指数、市场宽度、涨停明细、监管快照、
成交额前列、板块日快照……失败了只在日志里写一行。第二天没人去翻日志，缺口就悄悄留下；
等某个页面算出一个怪数再回头查，往往已经过了能补的时间。

## 三条纪律

1. **检测只读。** 这个模块不改任何数据。补数走 scripts/data_audit.py fix：先试跑、再确认、
   补完自动复查——页面上登录后点按钮，跟 2026-09-12 补板块指数是同一个节奏。
2. **「那天该有」要有参照。** 交易日由交易日历定（读缓存，不发请求）；表是后建的，建表
   之前的日子不算缺；股票快照以 10 年存档里「那天有成交」为准，停牌不算缺。没有日历就
   如实标 warn，不拿「库里有哪些日期」冒充交易日——那样整天没跑的日子会凭空消失
   （trading_calendar 模块注释里那笔旧账）。
3. **补不了的如实说补不了。** 监管状态、成交额前列、板块日快照、日复盘只能在当天拍——
   过了当天就补不回来，标 expired、只记录、不进待办。否则红点永远亮着，提醒就等于没有。

## 跑在哪

检测和补数都在子进程里跑（scripts/data_audit.py）：股票快照那项要读 10 年存档，常驻
服务进程里跑会把内存峰值留在堆里（DATA_SOURCES.md 坑 18）。接口只读落盘的报告文件。
"""
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..models.app_config import AppConfig
from ..models.leader_cycle import LeaderCycleSnapshot
from ..models.limit_up_detail import LimitUpDailyDetail
from ..models.market_effect import MarketEffectDaily
from ..models.market_index import IndexDailySnapshot, MarketBreadthDaily, SectorIndexDaily
from ..models.regulatory import RegulatoryStatusDaily
from ..models.review import DailyReview
from ..models.sector import Sector, SectorDailySnapshot
from ..models.stock import StockDailySnapshot
from ..models.turnover_pool import TurnoverPoolDaily
from .trading_calendar import calendar_status, get_trading_days

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPORT_DIR = BACKEND_DIR / "logs" / "data_audit"
#: 本机导出的板块日线文件 scp 到这里，页面上「试跑导入 / 确认导入」只认这个目录里的文件
INBOX_DIR = BACKEND_DIR / "data" / "inbox"
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "sector_kline_console.js"
KEEP_REPORTS = 30
WINDOW_DAYS = 65                  # 跟 K 线窗口一致
CLOSE_AT = (15, 30)               # 过了这个点，今天才算进「应该已经有了」
SH = timezone(timedelta(hours=8))
NO_DATA_KEY = "data_audit:sector_index_no_data"

OK, GAP, EXPIRED, WARN, ERROR = "ok", "gap", "expired", "warn", "error"

#: 有一键补法的检测项。scripts/data_audit.py 的 FIXERS 必须跟它一致（有测试盯着）
FIXABLE = ("archive", "stock_snapshots", "index_daily", "market_breadth",
           "limit_up_details", "market_effect", "leader_cycle", "sector_index")


# ── 补法描述（页面据此决定显示按钮还是步骤）──────────────────────────────────

def _fix_server(label: str, note: str = "") -> dict:
    """服务器上能补：页面给「试跑 / 确认补上」两个按钮，走 scripts/data_audit.py fix。"""
    return {"kind": "server", "label": label, "note": note}


FIX_NONE = {"kind": "none", "label": "", "note": ""}
FIX_RERUN = {"kind": "rerun_update", "label": "重跑今天的日更",
             "note": "只能当天补：在顶栏「数据更新」里重跑一次今天的日更"}
FIX_GONE = {"kind": "none", "label": "过了当天，补不回来",
            "note": "这类数据只能在当天拍。列在这里，是让你知道那几天相关页面的数据是空的"}
FIX_EXPORT = {"kind": "local_export", "label": "本机导出 → 传到服务器 → 页面导入",
              "note": "服务器连不上 push2his（2026-09-06 实测出口被拒），历史只能在本机浏览器里慢慢取"}


# ── 检测上下文 ───────────────────────────────────────────────────────────────

@dataclass
class AuditContext:
    db: Session
    now: datetime
    today: date
    through: Optional[date]      # 截止日：最后一个「应该已经有数据」的交易日
    window: List[date]           # 最近 WINDOW_DAYS 个交易日（<= through），升序
    calendar: dict               # JSON 可序列化的日历状态

    @property
    def through_is_today(self) -> bool:
        return self.through is not None and self.through == self.today

    def is_today(self, d: date) -> bool:
        return self.through_is_today and d == self.through


def build_context(db: Session, now: Optional[datetime] = None) -> AuditContext:
    """
    截止日：今天是交易日（日历里有）且已过 15:30 就含今天，否则到前一个交易日。
    日历读缓存（get_trading_days 不带 need_through 时有缓存就不发请求）。
    """
    now = now or datetime.now(SH)
    if now.tzinfo is not None:
        now = now.astimezone(SH)
    today = now.date()
    after_close = (now.hour, now.minute) >= CLOSE_AT
    cal = get_trading_days(db) or []
    st = calendar_status(cal, today)
    info = {"source": "calendar" if cal else "snapshots",
            "last": st["last"].isoformat() if st["last"] else None,
            "behind": st["behind"], "is_trading_day": st["is_trading_day"]}
    if cal:
        days = [d for d in cal if d < today or (d == today and after_close)]
    else:
        # 没有日历只能退回快照日期——整天没跑的日子会查不出来，calendar 那项会标 warn
        days = sorted(d for (d,) in db.query(StockDailySnapshot.date).distinct().all()
                      if d < today or (d == today and after_close))
    window = days[-WINDOW_DAYS:]
    return AuditContext(db=db, now=now, today=today, through=window[-1] if window else None,
                        window=window, calendar=info)


# ── 小工具 ───────────────────────────────────────────────────────────────────

@dataclass
class Check:
    id: str
    title: str
    table: str
    why: str                                      # 为什么会缺（一句话，页面上照抄）
    fn: Callable[[AuditContext, "Check"], dict]


def _iso(ds: Sequence[date]) -> List[str]:
    return [d.isoformat() for d in ds]


def _brief(ds: Sequence[date], n: int = 4) -> str:
    s = "、".join(d.strftime("%m-%d") for d in ds[:n])
    return s + (f" 等 {len(ds)} 天" if len(ds) > n else "")


def _res(chk: Check, status: str, summary: str, fix: Optional[dict] = None, **data) -> dict:
    return {"id": chk.id, "title": chk.title, "table": chk.table, "why": chk.why,
            "status": status, "summary": summary, "fix": fix or FIX_NONE,
            "checked_at": datetime.now(SH).isoformat(timespec="seconds"), **data}


def _missing_days(db: Session, col, window: Sequence[date]) -> Tuple[Optional[date], List[date]]:
    """表最早的日期 + 窗口里（从最早那天起算）一行都没有的交易日。表是空的返回 (None, [])。"""
    first = db.query(func.min(col)).scalar()
    if first is None:
        return None, []
    days = [d for d in window if d >= first]
    if not days:
        return first, []
    have = {d for (d,) in db.query(col).filter(col >= days[0], col <= days[-1]).distinct().all()}
    return first, [d for d in days if d not in have]


def _empty_table(chk: Check) -> dict:
    return _res(chk, WARN, "表里一行都没有——功能还没跑过，或者写入一直在失败")


def _all_there(ctx: AuditContext, chk: Check, first: date) -> dict:
    n = sum(1 for d in ctx.window if d >= first)
    return _res(chk, OK, f"最近 {n} 个交易日齐全（从 {first} 起算）", first_date=first.isoformat())


def _server_per_day(col, label: str, note: str) -> Callable[[AuditContext, Check], dict]:
    """每个交易日至少一行、服务器上能补的表。"""
    def fn(ctx: AuditContext, chk: Check) -> dict:
        first, miss = _missing_days(ctx.db, col, ctx.window)
        if first is None:
            return _empty_table(chk)
        if not miss:
            return _all_there(ctx, chk, first)
        return _res(chk, GAP, f"缺 {len(miss)} 个交易日：{_brief(miss)}", fix=_fix_server(label, note),
                    missing_dates=_iso(miss), first_date=first.isoformat())
    return fn


def _same_day(col) -> Callable[[AuditContext, Check], dict]:
    """只能当天拍的快照：今天缺 → 重跑日更；更早缺 → expired（补不回来，只记录）。"""
    def fn(ctx: AuditContext, chk: Check) -> dict:
        first, miss = _missing_days(ctx.db, col, ctx.window)
        if first is None:
            return _empty_table(chk)
        if not miss:
            return _all_there(ctx, chk, first)
        today = [d for d in miss if ctx.is_today(d)]
        old = [d for d in miss if not ctx.is_today(d)]
        if today:
            return _res(chk, GAP, "今天的还没有" + (f"；另有 {len(old)} 天已过当天、补不回来" if old else ""),
                        fix=FIX_RERUN, missing_dates=_iso(today), expired_dates=_iso(old),
                        first_date=first.isoformat())
        return _res(chk, EXPIRED, f"缺 {len(old)} 个交易日，都已过当天、补不回来：{_brief(old)}",
                    fix=FIX_GONE, expired_dates=_iso(old), first_date=first.isoformat())
    return fn


# ── 各项检测 ─────────────────────────────────────────────────────────────────

def _check_calendar(ctx: AuditContext, chk: Check) -> dict:
    c = ctx.calendar
    auto = {"kind": "none", "label": "下次日更会自动刷新日历", "note": ""}
    if c["source"] != "calendar":
        return _res(chk, WARN, "没有交易日历缓存，退回用快照日期当交易日——整天没跑的日子会查不出来", fix=auto)
    if c["behind"]:
        return _res(chk, WARN, f"日历只到 {c['last']}，中间还夹着工作日——最近几天的缺口可能漏报", fix=auto)
    w = ctx.window
    return _res(chk, OK, f"日历到 {c['last']}；检测截止 {ctx.through}，窗口 {len(w)} 个交易日"
                         + (f"（{w[0]} ~ {w[-1]}）" if w else ""))


def _check_archive(ctx: AuditContext, chk: Check) -> dict:
    from .fuyao_archive import archive_max_date
    d = archive_max_date()
    fix = _fix_server("重下存档", "约 6 分钟、可续传；只写 data/fuyao/，不改数据库")
    if d is None:
        return _res(chk, WARN, "没有可用的 10 年存档——「个股日快照」那项因此没法按存档核对", fix=fix)
    behind = [x for x in ctx.window if x > d]
    if len(behind) >= 2:
        return _res(chk, WARN, f"存档只到 {d}，比截止日落后 {len(behind)} 个交易日——这几天的快照缺口查不出来",
                    fix=fix, archive_max=d.isoformat())
    return _res(chk, OK, f"存档到 {d}", archive_max=d.isoformat())


def _check_stock_snapshots(ctx: AuditContext, chk: Check) -> dict:
    db = ctx.db
    first = db.query(func.min(StockDailySnapshot.date)).scalar()
    if first is None:
        return _empty_table(chk)
    days = [d for d in ctx.window if d >= first]
    cnt = dict(db.query(StockDailySnapshot.date, func.count(StockDailySnapshot.id))
               .filter(StockDailySnapshot.date >= days[0], StockDailySnapshot.date <= days[-1])
               .group_by(StockDailySnapshot.date).all()) if days else {}
    empty = [d for d in days if not cnt.get(d)]
    today_empty = [d for d in empty if ctx.is_today(d)]
    old_empty = [d for d in empty if not ctx.is_today(d)]
    heal_fix = _fix_server("用 10 年存档补洞",
                           "只补历史日、已有行一律不覆盖（原为空的成交量额除外）、只写 K 线原始字段；"
                           "补完会顺手重算市场效应。存档没覆盖到的日子要先重下存档")

    parts: List[str] = []
    data: dict = {"empty_days": _iso(old_empty)}
    status, fix = OK, FIX_NONE
    try:
        # 按存档逐只核对：存档里那天有成交、库里没行才算缺。读 10 年存档，只在子进程里跑到这里
        from scripts.full_dump import heal
        h = heal(db, days=WINDOW_DAYS, apply=False)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        h = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    if "error" in h:
        parts.append(f"没法按存档逐只核对（{h['error']}）")
        status = WARN
    else:
        per, names = h.get("per_stock") or {}, h.get("names") or {}
        added, vol = h.get("added", 0), h.get("vol_filled", 0)
        win = h.get("window")
        data.update(
            counts={"missing_rows": added, "stocks": len(per), "volume_fillable": vol,
                    "null_close_rows": h.get("null_close_rows", 0),
                    "tracked": h.get("tracked", 0), "in_archive": h.get("in_archive", 0)},
            heal_window=[win[0].isoformat(), win[1].isoformat(), win[2]] if win else None,
            items=[{"code": c, "name": names.get(c) or "", "missing": n}
                   for c, n in sorted(per.items(), key=lambda kv: -kv[1])[:100]])
        if added or vol:
            status, fix = GAP, heal_fix
            if added:
                parts.append(f"缺 {added:,} 行（{len(per):,} 只票）")
            if vol:
                parts.append(f"{vol:,} 行成交量为空、存档里有，可以补上")
        else:
            parts.append(f"按存档核对齐全（关注 {h.get('tracked', 0):,} 只）")
    if old_empty:
        parts.append(f"{len(old_empty)} 个交易日整天没有快照：{_brief(old_empty)}")
        status, fix = GAP, heal_fix
    if today_empty:
        parts.insert(0, "今天还没有快照（日更还没跑，或者失败了）")
        status = GAP
        if fix["kind"] == "none":
            fix = FIX_RERUN
    return _res(chk, status, "；".join(parts), fix=fix, **data)


def _check_index_daily(ctx: AuditContext, chk: Check) -> dict:
    from .index_trend_service import INDICES
    db, w = ctx.db, ctx.window
    if not w:
        return _res(chk, WARN, "没有可核对的交易日")
    have: Dict[str, Set[date]] = {}
    for code, d in (db.query(IndexDailySnapshot.index_code, IndexDailySnapshot.date)
                    .filter(IndexDailySnapshot.date >= w[0], IndexDailySnapshot.date <= w[-1]).all()):
        have.setdefault(code, set()).add(d)
    firsts = dict(db.query(IndexDailySnapshot.index_code, func.min(IndexDailySnapshot.date))
                  .group_by(IndexDailySnapshot.index_code).all())
    items = []
    for m in INDICES:
        first = firsts.get(m["code"])
        miss = [d for d in w if (first is None or d >= first) and d not in have.get(m["code"], set())]
        if miss:
            items.append({"code": m["code"], "name": m["name"], "missing": len(miss),
                          "missing_dates": _iso(miss)})
    if not items:
        return _res(chk, OK, f"{len(INDICES)} 个指数最近 {len(w)} 个交易日齐全")
    return _res(chk, GAP, "、".join(f"{it['name']} 缺 {it['missing']} 天" for it in items),
                fix=_fix_server("重拉指数日线", "东财→腾讯→新浪兜底，从最早缺的那天起重拉；"
                                               "已有行会用同一来源的值刷新（跟日更同一个函数）"),
                items=items)


def _check_market_breadth(ctx: AuditContext, chk: Check) -> dict:
    db = ctx.db
    first = db.query(func.min(MarketBreadthDaily.date)).scalar()
    if first is None:
        return _empty_table(chk)
    days = [d for d in ctx.window if d >= first]
    if not days:
        return _all_there(ctx, chk, first)
    rows = {d: (m, a, u) for d, m, a, u in db.query(
        MarketBreadthDaily.date, MarketBreadthDaily.margin_balance,
        MarketBreadthDaily.deal_amount, MarketBreadthDaily.up_count,
    ).filter(MarketBreadthDaily.date >= days[0], MarketBreadthDaily.date <= days[-1]).all()}
    blank = (None, None, None)
    # 每一列从它自己第一次有值的那天起算：两融历史回填到了 2010 年，涨跌统计是后来才接的——
    # 开始采集之前的日子不算缺（跟「建表之前不算缺」同一个道理）
    firsts = {key: db.query(func.min(MarketBreadthDaily.date)).filter(col.isnot(None)).scalar()
              for key, col in (("margin", MarketBreadthDaily.margin_balance),
                               ("amount", MarketBreadthDaily.deal_amount),
                               ("updown", MarketBreadthDaily.up_count))}

    def _miss(i: int, key: str, ds: List[date]) -> List[date]:
        f = firsts[key]
        return [d for d in ds if f is not None and d >= f and rows.get(d, blank)[i] is None]

    # 两融、成交额收盘官方值都可能次日才公布：最新一天不算缺
    margin_miss = _miss(0, "margin", days[:-1])
    amount_miss = _miss(1, "amount", days[:-1])
    updown_miss = _miss(2, "updown", days)
    updown_today = [d for d in updown_miss if ctx.is_today(d)]
    updown_old = [d for d in updown_miss if not ctx.is_today(d)]
    never = [label for key, label in (("margin", "两融"), ("amount", "成交额"), ("updown", "涨跌统计"))
             if firsts[key] is None]
    parts = [f"{'、'.join(never)}一天都没取到过"] if never else []
    if margin_miss:
        parts.append(f"两融缺 {len(margin_miss)} 天：{_brief(margin_miss)}")
    if amount_miss:
        parts.append(f"成交额缺 {len(amount_miss)} 天：{_brief(amount_miss)}")
    if updown_today:
        parts.append("今天的涨跌统计还没有")
    if updown_old:
        parts.append(f"涨跌统计缺 {len(updown_old)} 天（只能当天取，补不回来）")
    data = {"margin_missing": _iso(margin_miss), "amount_missing": _iso(amount_miss),
            "missing_dates": _iso(updown_today), "expired_dates": _iso(updown_old)}
    if margin_miss or amount_miss or updown_today:
        return _res(chk, GAP, "；".join(parts), fix=_fix_server(
            "重拉市场宽度", "先跑一次日常同步（两融往后补、成交额序列、当天涨跌统计）；"
                          "两融有空洞再跑两融全历史回填（只写两融 / 市盈率那几列）"), **data)
    if updown_old:
        return _res(chk, EXPIRED, "；".join(parts), fix=FIX_GONE, **data)
    if never:
        return _res(chk, WARN, "；".join(parts), **data)
    return _all_there(ctx, chk, first)


def _check_leader_cycle(ctx: AuditContext, chk: Check) -> dict:
    first, miss = _missing_days(ctx.db, LeaderCycleSnapshot.date, ctx.window)
    if first is None:
        return _empty_table(chk)
    if not miss:
        return _all_there(ctx, chk, first)
    today = [d for d in miss if ctx.is_today(d)]
    old = [d for d in miss if not ctx.is_today(d)]
    if old:
        return _res(chk, GAP, f"缺 {len(old)} 个交易日：{_brief(old)}"
                              + ("；今天的也还没有（日更里写）" if today else ""),
                    fix=_fix_server("用库里 K 线重建",
                                    "不向外部请求，不覆盖已有行。注意幸存者偏差：重建用的是今天的强势池，"
                                    "当时还没进池的票不会有行——做趋势研究可以，别当历史成分股用"),
                    missing_dates=_iso(old), first_date=first.isoformat())
    return _res(chk, GAP, "今天的还没有（日更里写）", fix=FIX_RERUN,
                missing_dates=_iso(today), first_date=first.isoformat())


def _check_sector_index(ctx: AuditContext, chk: Check) -> dict:
    from .sector_index_service import MIN_BARS_FOR_RS, existing_bar_counts
    db, w = ctx.db, ctx.window
    secs = [(c, n) for c, n in db.query(Sector.code, Sector.name)
            .filter(Sector.is_watched.is_(True)).order_by(Sector.code).all()
            if (c or "").startswith("BK")]
    if not secs:
        return _res(chk, WARN, "没有关注板块")
    no_data = load_no_data_codes(db)
    codes = [c for c, _ in secs]
    bars = existing_bar_counts(db, codes)
    firsts = dict(db.query(SectorIndexDaily.sector_code, func.min(SectorIndexDaily.date))
                  .filter(SectorIndexDaily.sector_code.in_(codes))
                  .group_by(SectorIndexDaily.sector_code).all())
    have: Dict[str, Set[date]] = {}
    if w:
        for c, d in (db.query(SectorIndexDaily.sector_code, SectorIndexDaily.date)
                     .filter(SectorIndexDaily.sector_code.in_(codes),
                             SectorIndexDaily.date >= w[0], SectorIndexDaily.date <= w[-1]).all()):
            have.setdefault(c, set()).add(d)
    # 今天那根由日更后的板块同步写（只在收盘后写），没写是日更的事，不算历史洞
    hist_days = [d for d in w if not ctx.is_today(d)]
    items, excluded, today_missing = [], [], 0
    for code, name in secs:
        if code in no_data:
            excluded.append(code)
            continue
        n, first = bars.get(code, 0), firsts.get(code)
        got = have.get(code, set())
        holes = [d for d in hist_days if first is not None and d >= first and d not in got]
        if ctx.through_is_today and first is not None and ctx.through not in got:
            today_missing += 1
        if n < MIN_BARS_FOR_RS or holes:
            items.append({"code": code, "name": name, "bars": n, "holes": _iso(holes),
                          "reason": "历史不足" if n < MIN_BARS_FOR_RS else "有洞"})
    data = {"items": items, "watched": len(secs), "min_bars": MIN_BARS_FOR_RS,
            "no_data": excluded, "today_missing": today_missing}
    note = f"（另有 {len(excluded)} 个板块东财没有指数日线，已排除）" if excluded else ""
    if items:
        short = sum(1 for it in items if it["reason"] == "历史不足")
        return _res(chk, GAP, f"{len(items)} 个关注板块要补：{short} 个历史不足 {MIN_BARS_FOR_RS} 根，"
                              f"{len(items) - short} 个窗口里有洞{note}", fix=FIX_EXPORT, **data)
    if today_missing:
        return _res(chk, GAP, f"今天有 {today_missing} 个板块的点位还没写（日更后的板块同步写）{note}",
                    fix=FIX_RERUN, **data)
    return _res(chk, OK, f"{len(secs)} 个关注板块历史齐全{note}", **data)


CHECKS: List[Check] = [
    Check("calendar", "交易日历", "app_config · trading_calendar",
          "缓存只在跨天、问到新日期时才去拉；拉失败会一直用旧的", _check_calendar),
    Check("archive", "10 年日K存档", "data/fuyao/daily-k.parquet",
          "存档要手动重下，不会自己更新", _check_archive),
    Check("stock_snapshots", "个股日快照", "stock_daily_snapshots",
          "某天拉 K 线失败、或者某只票那几天不在候选池里，那几天就没有快照", _check_stock_snapshots),
    Check("index_daily", "指数日线", "index_daily_snapshots",
          "日更里的「大盘趋势同步」是独立步骤，失败只记一行日志", _check_index_daily),
    Check("market_breadth", "市场宽度（两融 / 成交额 / 涨跌统计）", "market_breadth_daily",
          "三个模块各自独立，哪个接口那天挂了，那天那几列就是空的", _check_market_breadth),
    Check("limit_up_details", "涨停 / 炸板明细", "limit_up_daily_details",
          "涨停明细归档是独立步骤，东财接口挂了那天就没有",
          _server_per_day(LimitUpDailyDetail.trade_date, "逐日补涨停 / 炸板明细",
                          "东财涨停池接口支持历史日期；太久远的日子东财也可能不给，每天成功与否如实报")),
    Check("market_effect", "市场效应", "market_effect_daily",
          "按天缓存，某天没被算过就一直空着",
          _server_per_day(MarketEffectDaily.trade_date, "重算市场效应",
                          "只用库里已有的快照重算，不向外部请求；已是最新版本的行跳过")),
    Check("leader_cycle", "龙头周期快照", "leader_cycle_snapshots",
          "日更里写，日更那天失败就缺（2026-09-04 起才有）", _check_leader_cycle),
    Check("sector_index", "板块指数日线", "sector_index_daily",
          "历史只能从 push2his 取，而服务器被它拒；每天那一根由板块同步写，同步失败那天就有洞",
          _check_sector_index),
    Check("daily_review", "日复盘", "daily_reviews",
          "日更最后一步写，日更失败那天就没有；它依赖当天的池子状态，只能当天补",
          _same_day(DailyReview.date)),
    Check("regulatory_status", "监管状态快照", "regulatory_status_daily",
          "名单每次整表重建，只有当天拍下的快照能留下时间序列", _same_day(RegulatoryStatusDaily.date)),
    Check("turnover_pool", "成交额前列存档", "turnover_pool_daily",
          "东财只给当天的成交额前列名单", _same_day(TurnoverPoolDaily.date)),
    Check("sector_daily_snapshot", "板块日快照（弱转强）", "sector_daily_snapshots",
          "拍的是当天的板块字段，过了当天就变了", _same_day(SectorDailySnapshot.date)),
]
CHECK_IDS = [c.id for c in CHECKS]
_BY_ID = {c.id: c for c in CHECKS}


# ── 跑检测 ───────────────────────────────────────────────────────────────────

def summarize(checks: Sequence[dict]) -> dict:
    s = {k: 0 for k in (OK, GAP, EXPIRED, WARN, ERROR)}
    for c in checks:
        s[c["status"]] = s.get(c["status"], 0) + 1
    s["todo"] = s[GAP] + s[ERROR]      # 顶栏红点的数字：能补的缺口 + 检测本身出错
    return s


def run_audit(db: Session, *, now: Optional[datetime] = None,
              only: Optional[Set[str]] = None) -> dict:
    """跑全部（或 only 指定的）检测项，返回报告 dict（JSON 可序列化）。只读。"""
    ctx = build_context(db, now)
    results = []
    for chk in CHECKS:
        if only and chk.id not in only:
            continue
        try:
            results.append(chk.fn(ctx, chk))
        except Exception as e:  # noqa: BLE001
            # 一项查挂了不能拖垮其余各项；如实报「检测本身出错」，算进待办
            db.rollback()
            results.append(_res(chk, ERROR, f"检测本身出错：{type(e).__name__}: {str(e)[:160]}"))
    w = ctx.window
    return {
        "generated_at": ctx.now.isoformat(timespec="seconds"),
        "through": ctx.through.isoformat() if ctx.through else None,
        "window": {"start": w[0].isoformat() if w else None,
                   "end": w[-1].isoformat() if w else None, "days": len(w)},
        "calendar": ctx.calendar,
        "checks": results,
        "summary": summarize(results),
        "inbox_dir": str(INBOX_DIR),
        "ssh_user": settings.DATA_AUDIT_SSH_USER,
    }


def run_check(db: Session, check_id: str, now: Optional[datetime] = None) -> dict:
    """现查一项（补数前后用，不读报告里的旧结果）。"""
    return run_audit(db, now=now, only={check_id})["checks"][0]


# ── 报告文件 ─────────────────────────────────────────────────────────────────

def load_report(report_dir: Optional[Path] = None) -> Optional[dict]:
    p = Path(report_dir or REPORT_DIR) / "latest.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_report(report: dict, *, merge: bool = False, report_dir: Optional[Path] = None) -> dict:
    """
    写 latest.json（页面读这份）。merge=True：只替换报告里同 id 的检测项（补完复查用），
    其余各项和生成时间保留；否则另存一份带时间戳的副本，只留最近 KEEP_REPORTS 份。
    """
    d = Path(report_dir or REPORT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    Path(INBOX_DIR).mkdir(parents=True, exist_ok=True)    # scp 之前收件箱得先在
    if merge:
        old = load_report(d)
        if old:
            fresh = {c["id"]: c for c in report["checks"]}
            checks = [fresh.pop(c["id"], c) for c in old.get("checks", [])] + list(fresh.values())
            report = {**old, "checks": checks, "summary": summarize(checks),
                      "updated_at": report["generated_at"]}
    body = json.dumps(report, ensure_ascii=False, indent=1)
    tmp = d / "latest.json.tmp"
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, d / "latest.json")
    if not merge:
        stamp = datetime.now(SH).strftime("%Y%m%d-%H%M%S")
        (d / f"report-{stamp}.json").write_text(body, encoding="utf-8")
        for p in sorted(d.glob("report-*.json"))[:-KEEP_REPORTS]:
            p.unlink(missing_ok=True)
    return report


def list_inbox() -> List[dict]:
    d = Path(INBOX_DIR)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime, SH).isoformat(timespec="seconds")})
    return out


# ── 板块指数：东财没有指数日线的板块 / 导出脚本 ──────────────────────────────

def load_no_data_codes(db: Session) -> Dict[str, str]:
    """{板块码: 首次确认东财没有指数日线的日期}。这些板块不再算缺口。"""
    row = db.query(AppConfig).filter(AppConfig.key == NO_DATA_KEY).first()
    if not row or not row.value:
        return {}
    try:
        return dict((json.loads(row.value) or {}).get("codes") or {})
    except (ValueError, TypeError, AttributeError):
        return {}


def record_no_data_codes(db: Session, codes: Sequence[str], when: date) -> None:
    """导入时文件里的空记录（导出脚本对「东财没有这个板块的指数日线」写的）记到这里。"""
    cur = load_no_data_codes(db)
    for c in codes:
        cur.setdefault(c, when.isoformat())
    val = json.dumps({"codes": cur}, ensure_ascii=False)
    row = db.query(AppConfig).filter(AppConfig.key == NO_DATA_KEY).first()
    if row:
        row.value = val
    else:
        db.add(AppConfig(key=NO_DATA_KEY, value=val))
    db.commit()


def sector_export_codes(report: Optional[dict]) -> List[str]:
    chk = next((c for c in (report or {}).get("checks", []) if c.get("id") == "sector_index"), None)
    return [it["code"] for it in (chk or {}).get("items", [])]


def render_export_script(codes: Sequence[str], today: Optional[date] = None) -> str:
    """把缺数据的板块填进导出脚本模板（templates/sector_kline_console.js）。"""
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    day = (today or datetime.now(SH).date()).strftime("%Y%m%d")
    return (tpl.replace("__CODES__", json.dumps(list(codes)))
               .replace("__DATE__", day)
               .replace("__COUNT__", str(len(codes))))


def run_audit_subprocess(args: Sequence[str],
                         on_line: Optional[Callable[[str], None]] = None) -> int:
    """子进程跑 scripts/data_audit.py，返回退出码（3 = 日更正在跑，什么都没动）。"""
    from .daily_update_runner import run_script_subprocess
    return run_script_subprocess(["-m", "scripts.data_audit", *args], on_line=on_line)
