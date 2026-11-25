"""
大市值快照 + 人气核心池（区间涨幅前列、成员事实）历史补数（2026-11-25）。

为什么能补：东财选股 prompt 认指定日期的问法——「2026年11月20日总市值大于300亿；2026年11月20日收盘价；……」
「截至2026年11月20日近20个交易日涨幅排名前40」——返回的列名带那一天（`TOAL_MARKET_VALUE<140>{2026-11-20}`），
只认这些列。2026-11-25 拿 11-06 的东财全市场存档逐只对过（≥300 亿的 586 只）：收盘价、成交额、换手率、量比全对；
涨跌幅 6 只差 0.03~0.05 个百分点；区间涨幅个别差到 0.3 个百分点（前复权基准随之后的分红变了）；总市值是
「1.18万亿」这种三四位有效数字；名单差 4 只，都卡在 300 亿边上。名称、是否 ST 只有补数那天的。

为什么要补：东财 clist 全市场翻页被限流（11-19 起第 1 页就 502），腾讯备用又被 600849 卡住，11-12~11-24 缺 7 个交易日。

    cd /opt/code/tradeflux/backend
    .venv/bin/python -m scripts.large_cap_backfill --from 2026-11-12 --to 2026-11-24            # 试跑：问、核对，不写库
    .venv/bin/python -m scripts.large_cap_backfill --from 2026-11-12 --to 2026-11-24 --apply    # 写库

每天的请求：名单约 6 次 + 掉出名单补问 1 次 + 新进名单行业 0~1 次；区间涨幅前列 4 次；人气核心池成员事实 1 次。
任意两次请求至少隔 --interval 秒（默认 2），天与天之间再歇 --pause 秒（默认 5）——一个接口不要过分调用。
**按日期从早到晚补**：跨出要拿前一天的名单比，前一天补好了下一天才有得比。
已有完整快照的日子（东财全市场 11-17、11-18 这种）整天跳过；--force 重补选股名单（东财全市场的不覆盖）。
跟大市值同步共用一把锁，不会同时跑。退出码：0 全部成功 / 1 有失败的日子 / 3 另一个大市值同步正在跑。
"""
import argparse
import fcntl
import os
import sys
import time
from datetime import date, datetime, time as dtime
from typing import Callable, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db  # noqa: E402
from app.models.large_cap import LargeCapSyncBatch  # noqa: E402
from app.services import large_cap_sync_service as sync  # noqa: E402
from app.services import prompt_api  # noqa: E402

LOCK_FILE = "/tmp/tradeflux_large_cap.lock"
EXIT_FAIL, EXIT_BUSY = 1, 3


def _log(msg: str) -> None:
    print(msg, flush=True)


def _existing(db, d: date) -> Optional[LargeCapSyncBatch]:
    return (db.query(LargeCapSyncBatch)
            .filter(LargeCapSyncBatch.trade_date == d, LargeCapSyncBatch.applied.is_(True),
                    LargeCapSyncBatch.source.in_(sync.AUTHORITATIVE))
            .order_by(LargeCapSyncBatch.id.desc()).first())


def run(db, days: Sequence[date], cal: Sequence[date], *, apply: bool, force: bool = False,
        with_cores: bool = True, floor_yi: float = sync.FLOOR_YI, pause: float = 5.0,
        log: Callable[[str], None] = _log) -> List[dict]:
    """逐日补。返回每天的结果 [{date, status, detail}]。"""
    from app.services import popular_core_service as pc
    from app.services import stock_day_facts as sdf
    out = []
    for i, d in enumerate(sorted(days)):
        if i:
            time.sleep(pause)
        ex = _existing(db, d)
        if ex is not None and (ex.universe_floor_yi is None or not force):
            why = "东财全市场快照" if ex.universe_floor_yi is None else "选股名单"
            log(f"[补数] {d} 已有完整快照（{why}，批次 {ex.id}），跳过")
            out.append({"date": d, "status": "skipped", "detail": why})
            continue
        if not apply:
            res = sync.fetch_prompt(db, floor_yi, on=d, recheck_gone=False)
            v = sync.judge(res)
            top = sorted(res.rows.values(), key=lambda r: -(r["total_market_cap"] or 0))[:3]
            log(f"[试跑] {d}：名单 {len(res.rows)}/{res.expected} 只，完整={v.complete}，数据日期 {v.trade_date}"
                + (f"，原因：{'；'.join(v.reasons)}" if v.reasons else ""))
            for r in top:
                log(f"    {r['stock_code']} {r['stock_name']} 总市值 {r['total_market_cap']:.0f}亿 当日 {r['pct_change']}% "
                    f"20日 {r['pct_change_20d']}% 行业 {r['industry']}")
            ok = v.complete and v.trade_date == d
            out.append({"date": d, "status": "dry_ok" if ok else "dry_fail", "detail": v.reasons})
            continue
        r = sync.sync_snapshot(db, apply=True, source="prompt", floor_yi=floor_yi,
                               fetch_pr=lambda db_, f, d=d: sync.fetch_prompt(db_, f, on=d),
                               market_now=datetime.combine(d, dtime(15, 30)), calendar=cal, log=log)
        ok = bool(r.get("applied")) and r.get("trade_date") == d
        log(f"[补数] {d} 大市值快照：{'✅ 已写入' if ok else '❌ ' + str(r.get('status'))}")
        detail = {"snapshot": r.get("status")}
        if with_cores:
            g = sdf.sync_gain_tops(db, d, history=True)
            log(f"[补数] {d} 区间涨幅前列：{'✅ ' + str(g['counts'] or g['reason']) if g['ok'] else '❌ ' + g['reason']}")
            detail["gain_tops"] = g["ok"]
            try:
                items = list(pc.compute(db, d, cal, fetch=False)["items"])
                got = sdf.backfill_facts(db, d, items)
                log(f"[补数] {d} 人气核心池成员 {len(items)} 只，按日期补问到 {len(got)} 只的当日事实")
            except Exception as e:  # noqa: BLE001 —— 成员事实补不到只是这几项缺失
                db.rollback()
                log(f"[补数] {d} ⚠️ 人气核心池成员事实没补：{type(e).__name__}: {e}")
        out.append({"date": d, "status": "ok" if ok else "failed", "detail": detail})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="大市值快照 + 人气核心池历史补数（选股 prompt 按日期问）")
    ap.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    ap.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    ap.add_argument("--apply", action="store_true", help="写库（不加就是试跑：只问名单、核对，不写）")
    ap.add_argument("--force", action="store_true", help="已有选股名单的日子也重补（东财全市场快照不覆盖）")
    ap.add_argument("--no-cores", action="store_true", help="只补大市值快照，不补人气核心池的前列和成员事实")
    ap.add_argument("--interval", type=float, default=2.0, help="任意两次选股请求至少隔几秒（默认 2）")
    ap.add_argument("--pause", type=float, default=5.0, help="天与天之间歇几秒（默认 5）")
    a = ap.parse_args()
    if a.end < a.start:
        _log("--to 早于 --from")
        return EXIT_FAIL

    fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _log("[补数] 另一个大市值同步正在跑，这次不补")
        return EXIT_BUSY

    init_db()
    from app.services import api_call_counter
    from app.services.large_cap_service import get_config
    from app.services.trading_calendar import get_trading_days
    api_call_counter.install()
    prompt_api.MIN_INTERVAL_S = max(prompt_api.MIN_INTERVAL_S, a.interval)
    db = SessionLocal()
    try:
        cal = get_trading_days(db, need_through=a.end) or []
        days = [d for d in cal if a.start <= d <= a.end]
        if not days:
            _log(f"[补数] 交易日历里 {a.start}~{a.end} 没有交易日（日历止于 {cal[-1] if cal else '—'}）")
            return EXIT_FAIL
        floor = sync.universe_floor(get_config(db)["threshold_yi"])
        _log(f"[补数] {'写库' if a.apply else '试跑'}：{len(days)} 个交易日 {days[0]}~{days[-1]}，"
             f"名单下限 {floor:g} 亿，请求间隔 ≥{prompt_api.MIN_INTERVAL_S:g} 秒")
        res = run(db, days, cal, apply=a.apply, force=a.force, with_cores=not a.no_cores, floor_yi=floor,
                  pause=a.pause)
        bad = [r for r in res if r["status"] in ("failed", "dry_fail")]
        _log(f"[补数] 完成：成功 {sum(1 for r in res if r['status'] in ('ok', 'dry_ok'))} 天，"
             f"跳过 {sum(1 for r in res if r['status'] == 'skipped')} 天，失败 {len(bad)} 天"
             + (f"（{', '.join(str(r['date']) for r in bad)}）" if bad else ""))
        return EXIT_FAIL if bad else 0
    finally:
        for ln in api_call_counter.lines(prefix="[补数] "):
            _log(ln)
        db.close()
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


if __name__ == "__main__":
    sys.exit(main())
