"""
大市值日快照同步（大市值概览页的数据源）。日更之后、几次重试、页面上的「立即同步」都调它（子进程）。

    cd /opt/code/tradeflux/backend
    .venv/bin/python -m scripts.large_cap_sync                         # 试跑：抓全市场、判完整，不写库
    .venv/bin/python -m scripts.large_cap_sync --apply                 # 写快照 + 补 F10 分类
    .venv/bin/python -m scripts.large_cap_sync --apply --if-missing    # 最近一个收盘日已有东财完整快照就不动（定时重试用）
    .venv/bin/python -m scripts.large_cap_sync --apply --source tencent   # 只走腾讯备用
    .venv/bin/python -m scripts.large_cap_sync --classify-only         # 只补 F10 分类（按库里最新快照）

只写已收盘的数据；拿不全就一行不写（见 large_cap_sync_service 的模块注释）。

退出码：0 写入成功 / 已有；1 没拿到完整的收盘数据；3 另一个大市值同步正在跑。
"""
import argparse
import fcntl
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db  # noqa: E402
from app.models.large_cap import LargeCapMarketDaily, LargeCapSyncBatch  # noqa: E402
from app.services import large_cap_sync_service as sync  # noqa: E402
from app.services.large_cap_service import expected_latest, get_config  # noqa: E402

LOCK_FILE = "/tmp/tradeflux_large_cap.lock"
EXIT_FAIL, EXIT_BUSY = 1, 3


def _log(msg: str) -> None:
    print(msg, flush=True)


def _classify(db, codes_by_cap, limit: int) -> None:
    cfg = get_config(db)
    # 门槛附近也要有分类：跨入的公司昨天还在门槛下面
    floor = min(float(cfg["threshold_yi"]), 500.0) * 0.8
    st = sync.refresh_classification(db, codes_by_cap, floor_yi=floor, limit=limit, log=_log)
    _log(f"[大市值] F10 分类：≥{floor:.0f}亿 {st['targets']} 只，本次试 {st['tried']}，"
         f"成功 {st['ok']}，拉空 {st['empty']}，失败 {st['error']}，还差 {st['remaining_missing']} 只没分类")


def _already_have(db) -> bool:
    from app.services.eastmoney_fetcher import SH_TZ
    from app.services.trading_calendar import get_trading_days
    now = datetime.now(SH_TZ).replace(tzinfo=None)
    # 缓存不含今天就刷一次（子进程里，一天至多一次）——拿旧日历定目标日，会把今天漏掉
    cal = get_trading_days(db, need_through=now.date()) or []
    target, _ = expected_latest(cal, now) if cal else (None, False)
    if target is None:
        return False
    b = (db.query(LargeCapSyncBatch)
         .filter(LargeCapSyncBatch.trade_date == target, LargeCapSyncBatch.applied.is_(True),
                 LargeCapSyncBatch.source == sync.SOURCE_EM).first())
    if b is not None:
        _log(f"[大市值] {target} 已有东财完整快照（批次 {b.id}），不重复同步")
        return True
    _log(f"[大市值] {target} 还没有东财完整快照，开始同步")
    return False


def _print_dry(out: dict) -> None:
    rows = out.get("rows") or {}
    caps = sorted((r["total_market_cap"] for r in rows.values() if r["total_market_cap"]), reverse=True)
    _log(f"[试跑] 交易日 {out.get('trade_date')}，{len(rows)} 只，有效总市值 {len(caps)}")
    for t in (500, 800, 1000, 1500, 2000):
        _log(f"  ≥{t}亿：{sum(1 for c in caps if c >= t)} 只")
    top = sorted(rows.values(), key=lambda r: -(r["total_market_cap"] or 0))[:5]
    for r in top:
        _log(f"  {r['stock_code']} {r['stock_name']} 总市值 {r['total_market_cap']:.2f}亿 "
             f"今日 {r['pct_change']}% 20日 {r['pct_change_20d']}% 换手 {r['turnover_rate']}% "
             f"量比 {r['volume_ratio']} 行业 {r['industry']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="大市值日快照同步")
    ap.add_argument("--apply", action="store_true", help="写库（不加就是试跑）")
    ap.add_argument("--source", choices=["auto", "eastmoney", "tencent"], default="auto")
    ap.add_argument("--if-missing", action="store_true", help="最近收盘日已有东财完整快照就什么都不做")
    ap.add_argument("--classify-limit", type=int, default=150, help="本次最多补多少只 F10 分类")
    ap.add_argument("--no-classify", action="store_true")
    ap.add_argument("--classify-only", action="store_true")
    a = ap.parse_args()

    fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _log("[大市值] 另一个大市值同步正在跑，这次跳过")
        return EXIT_BUSY

    init_db()
    db = SessionLocal()
    try:
        if a.classify_only:
            latest = (db.query(LargeCapSyncBatch.trade_date)
                      .filter(LargeCapSyncBatch.applied.is_(True))
                      .order_by(LargeCapSyncBatch.trade_date.desc()).first())
            if not latest:
                _log("[大市值] 库里还没有快照，没法定分类范围")
                return EXIT_FAIL
            codes = [(c, cap) for c, cap in db.query(LargeCapMarketDaily.stock_code,
                                                     LargeCapMarketDaily.total_market_cap)
                     .filter(LargeCapMarketDaily.trade_date == latest[0],
                             LargeCapMarketDaily.total_market_cap.isnot(None))]
            codes.sort(key=lambda x: -x[1])
            _classify(db, codes, a.classify_limit)
            return 0

        if a.apply and a.if_missing and _already_have(db):
            return 0

        out = sync.sync_snapshot(db, apply=a.apply, source=a.source, log=_log)
        if not a.apply:
            if str(out["status"]).startswith("dry_run"):
                _print_dry(out)
                return 0
            _log("[试跑] 没拿到完整数据")
            return EXIT_FAIL
        if out.get("applied") and not a.no_classify:
            try:
                _classify(db, out.get("codes_by_cap") or [], a.classify_limit)
            except Exception as e:  # noqa: BLE001 —— 分类失败不影响已经写好的行情快照
                db.rollback()
                _log(f"[大市值] ⚠️ F10 分类失败（快照已写好，不受影响）：{type(e).__name__}: {e}")
        if out.get("applied") or out["status"] == "kept_existing":
            _log(f"[大市值] ✅ {out.get('trade_date')} 收盘快照就绪")
            return 0
        _log(f"[大市值] ❌ 没写入：{out['status']}")
        return EXIT_FAIL
    finally:
        db.close()
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


if __name__ == "__main__":
    sys.exit(main())
