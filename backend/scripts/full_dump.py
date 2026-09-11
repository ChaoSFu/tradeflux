"""
full_dump.py —— 10 年全量日K存档的运维命令（2026-09-11）。

    cd /opt/code/tradeflux/backend
    .venv/bin/python -m scripts.full_dump status          # 存档到哪天、库里比它新几个交易日
    .venv/bin/python -m scripts.full_dump refresh         # 重下存档（可续传，约 6 分钟）
    .venv/bin/python -m scripts.full_dump heal            # 只统计：关注的股票近 65 个交易日缺多少行
    .venv/bin/python -m scripts.full_dump heal --apply    # 真的补进快照表

heal 跟日更里 10 日 dump 补历史**走同一个函数**（snapshot_history.insert_history_bars）：
只补历史日、已有行绝不覆盖（原为空的量额除外）、只写 K 线原始字段。

**别跟日更同时跑**（15:30 前后 / 手动点更新时）：两边都可能给同一只票的同一天补行。
"""
import argparse
import json
import resource
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict

from sqlalchemy import func

from app.database import SessionLocal
from app.models.stock import Stock, StockDailySnapshot
from app.services.fuyao_archive import (
    ARCHIVE_PATH, KIND_FULL, META_PATH, archive_max_date, read_archive_rows,
)
from app.services.fuyao_dump import download_dump_resumable, get_api_key, rows_to_bars
from app.services.market_effect_service import recent_trade_dates, refresh_effects
from app.services.snapshot_history import insert_history_bars

SH = timezone(timedelta(hours=8))


def _peak_mb():
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / 1024 / 1024 if sys.platform == "darwin" else v / 1024


def heal(db, days: int = 65, apply: bool = False, archive_path=ARCHIVE_PATH,
         today=None, chunk: int = 200) -> dict:
    """
    用存档给**库里在关注的股票**（Stock 表）补最近 days 个交易日的历史缺口。

    · 只看 Stock 表里的票——用户 2026-08-26 定的边界：不因为存档里有全市场就把快照表撑爆
    · 交易日取存档自己的日期：全市场每个交易日都有行，比外部日历更不会出错
    · 存档一次流式读完（最多 2 秒），但转 KLineBar 和写库**按 chunk 只票分批**：
      两千多只 × 80 天全转成 KLineBar 要一两百 MB，这台机器扛不起
    """
    t0 = time.time()
    d = archive_max_date(archive_path)
    if d is None:
        return {"error": f"没有可用存档：{archive_path}（先跑 refresh）"}
    target = today or datetime.now(SH).date()
    stocks = db.query(Stock.id, Stock.code, Stock.is_st, Stock.name).all()
    wanted = {c: bool(st) for _, c, st, _ in stocks}
    sid_by_code = {c: sid for sid, c, _, _ in stocks}
    # days 个交易日 ≈ days×1.5 个自然日，再加 20 天：长假 + 窗口前还要有一根做前收
    since = d - timedelta(days=int(days * 1.5) + 20)
    rows = read_archive_rows(set(wanted), since, archive_path)
    all_dates = sorted({r[0] for rs in rows.values() for r in rs if r[0] < target})
    window = set(all_dates[-days:])
    in_archive = len(rows)

    counts: Dict[str, int] = {}
    added = vol = 0
    codes = sorted(rows)
    for i in range(0, len(codes), chunk):
        part = {c: rows.pop(c) for c in codes[i:i + chunk]}
        a, v = insert_history_bars(db, rows_to_bars(part, wanted), sid_by_code, target,
                                   only_dates=window, dry_run=not apply, counts=counts)
        added += a
        vol += v

    # 快照变了，依赖它的市场效应缓存得跟着重算——那份缓存算出就不再更新，不重算
    # 「昨日群体·今日反馈」就还停在补洞之前那份只含幸存者的样本上。
    # 窗口里的每一天都算：不只是新补的日子，08-14 之后那段库里早就齐了、只是缓存没动
    effects, effects_error = None, None
    if apply and window:
        try:
            effects = refresh_effects(
                db, [x for x in recent_trade_dates(db, len(window) + 10) if x >= min(window)])
        except Exception as e:  # noqa: BLE001
            db.rollback()
            effects_error = f"{type(e).__name__}: {str(e)[:120]}"

    null_close = 0
    if window:
        null_close = db.query(func.count(StockDailySnapshot.id)).filter(
            StockDailySnapshot.close_price.is_(None),
            StockDailySnapshot.date >= min(window),
            StockDailySnapshot.date <= max(window)).scalar() or 0
    return {
        "archive_max": d, "applied": apply,
        "window": (min(window), max(window), len(window)) if window else None,
        "tracked": len(wanted), "in_archive": in_archive,
        "added": added, "vol_filled": vol, "per_stock": counts,
        "names": {c: n for _, c, _, n in stocks},
        "null_close_rows": null_close, "seconds": round(time.time() - t0, 1),
        "effects_refreshed": effects, "effects_error": effects_error,
    }


def cmd_heal(days: int, apply: bool):
    db = SessionLocal()
    try:
        r = heal(db, days=days, apply=apply)
    finally:
        db.close()
    if "error" in r:
        print(r["error"]); return
    w = r["window"]
    print(f"存档覆盖到 {r['archive_max']}；补洞窗口：最近 {w[2]} 个交易日（{w[0]} ~ {w[1]}）"
          if w else f"存档覆盖到 {r['archive_max']}；窗口里一个交易日都没有")
    print(f"库里关注的股票 {r['tracked']:,} 只，存档里有 {r['in_archive']:,} 只"
          f"（没有的 {r['tracked'] - r['in_archive']:,} 只：多半是 2016 年前就退市，或代码对不上）")
    per = r["per_stock"]
    verb = "已补入" if r["applied"] else "缺的行（只统计，没写库）"
    print(f"\n{verb}：{r['added']:,} 行，涉及 {len(per):,} 只票")
    if per:
        buckets = [(1, 4), (5, 19), (20, 59), (60, 10 ** 9)]
        parts = []
        for lo, hi in buckets:
            n = sum(1 for v in per.values() if lo <= v <= hi)
            parts.append(f"{lo}~{hi if hi < 10 ** 9 else ''}天 {n} 只".replace("~天", "天以上"))
        print("  按缺的天数：" + "，".join(parts))
        top = sorted(per.items(), key=lambda kv: -kv[1])[:10]
        print("  缺得最多的：" + "、".join(f"{c} {r['names'].get(c) or ''} {n}天" for c, n in top))
    print(f"已有行补上成交量/额：{r['vol_filled']:,} 行（原为空的才补）")
    print(f"窗口里 close_price 为空的已有行：{r['null_close_rows']:,} 行"
          f"——不在这次处理范围（已有行一律不覆盖），单列出来是让你知道有多少")
    if r["effects_refreshed"] is not None:
        print(f"市场效应缓存已按补好的快照重算：{r['effects_refreshed']} 个交易日")
    if r["effects_error"]:
        print(f"⚠️ 市场效应重算失败：{r['effects_error']}——快照已补好，"
              f"手动跑一次 python -m scripts.backfill_market_effects --force")
    print(f"\n耗时 {r['seconds']}s，进程峰值 {_peak_mb():,.0f}MB")
    if not r["applied"]:
        print("这是只统计。确认数字没问题后加 --apply 真的写入（别跟日更同时跑）")


def cmd_status():
    d = archive_max_date()
    if d is None:
        print(f"没有可用存档：{ARCHIVE_PATH}（先跑 refresh）"); return
    try:
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        meta = {}
    db = SessionLocal()
    try:
        newer = db.query(func.count(func.distinct(StockDailySnapshot.date))).filter(
            StockDailySnapshot.date > d).scalar() or 0
    finally:
        db.close()
    print(f"存档：{ARCHIVE_PATH}（{ARCHIVE_PATH.stat().st_size / 1048576:.1f}MB）")
    print(f"  覆盖到 {d}，生成日 {meta.get('path_date', '?')}，下载于 {meta.get('fetched_at', '?')}")
    print(f"  库里比存档新的交易日：{newer} 个")


def cmd_refresh():
    key = get_api_key()
    if not key:
        print("没配 FUYAO_API_KEY"); sys.exit(1)
    shown = {"pct": -10}

    def _p(have, total):
        pct = int(have * 100 / total)
        if pct >= shown["pct"] + 10:
            shown["pct"] = pct - pct % 10
            print(f"    {have / 1048576:6.1f} / {total / 1048576:.1f}MB  ({pct}%)", flush=True)

    print("下载 daily-k（可续传；断了再跑一次会接着下）")
    r = download_dump_resumable(key, KIND_FULL, ARCHIVE_PATH, progress=_p)
    d = archive_max_date()
    META_PATH.write_text(json.dumps({
        "path_date": r["path_date"], "size": r["bytes"],
        "fetched_at": datetime.now(SH).isoformat(timespec="seconds"),
        "max_trade_date": d.isoformat() if d else None,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"完成：{r['bytes'] / 1048576:.1f}MB，{r['seconds']:.0f}s，{r['rounds']} 轮，"
          f"断点续传 {r['resumed']} 次；覆盖到 {d}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("refresh")
    h = sub.add_parser("heal")
    h.add_argument("--days", type=int, default=65, help="补最近多少个交易日（默认 65，跟 K 线窗口一致）")
    h.add_argument("--apply", action="store_true", help="真的写库（默认只统计）")
    a = ap.parse_args()
    if a.cmd == "status":
        cmd_status()
    elif a.cmd == "refresh":
        cmd_refresh()
    else:
        cmd_heal(a.days, a.apply)


if __name__ == "__main__":
    main()
