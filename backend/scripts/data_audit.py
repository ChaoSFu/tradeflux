"""
数据体检的命令行入口（2026-09-12）。页面上的「立即检测 / 试跑 / 确认补上 / 导入」调的都是它（子进程）。

    cd /opt/code/tradeflux/backend
    .venv/bin/python -m scripts.data_audit run                   # 检测全部，打印报告（不落盘）
    .venv/bin/python -m scripts.data_audit run --save            # 检测并写 logs/data_audit/latest.json（页面读这份）
    .venv/bin/python -m scripts.data_audit run --check index_daily --save   # 只重查一项，合并进报告
    .venv/bin/python -m scripts.data_audit fix index_daily           # 试跑：只列出将补什么，不写库
    .venv/bin/python -m scripts.data_audit fix index_daily --apply   # 真的补，补完自动复查这一项
    .venv/bin/python -m scripts.data_audit fix sector_index --file sector_klines_xxx.jsonl [--apply]
                                                                 # 导入收件箱 data/inbox/ 里的板块日线文件
    .venv/bin/python -m scripts.data_audit export-script > export.js # 板块指数导出脚本（已填好缺数据的板块）
    .venv/bin/python -m scripts.data_audit export-script --codes     # 只打印缺数据的板块码，给 export_sector_klines.py --codes

每类缺口的补法都固定成三步：试跑（列出将补什么）→ 确认（--apply）→ 自动复查。
补数拿日更那把锁，跟日更 / 板块全量同步互斥；检测只探一下锁（日更写到一半去查，会把
还没写完的数据当成缺口）。

退出码：0 正常；2 参数不对；3 日更或别的补数任务正在跑（什么都没动）。
"""
import argparse
import fcntl
import os
import re
import shutil
import sys
import time
from contextlib import contextmanager, nullcontext
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.services import data_audit_service as svc  # noqa: E402

LOCK_FILE = "/tmp/tradeflux_daily_update.lock"      # 跟日更、板块全量同步同一把
EXIT_BUSY = 3
_FILE_RE = re.compile(r"^[\w.\-]+\.jsonl$")
_ICON = {svc.ERROR: "✗", svc.GAP: "✗", svc.WARN: "⚠", svc.EXPIRED: "·", svc.OK: "✓"}
_ORDER = [svc.ERROR, svc.GAP, svc.WARN, svc.EXPIRED, svc.OK]
#: 补完复查哪几项。补快照会改市场效应的输入，所以一起复查
RECHECK = {"archive": {"archive", "stock_snapshots"},
           "stock_snapshots": {"stock_snapshots", "market_effect"}}


class Busy(Exception):
    pass


@contextmanager
def update_lock(hold: bool):
    """hold=True：整段拿住日更那把锁（真写库时）；hold=False：只探一下有没有人在跑，立刻放掉。"""
    fd = open(LOCK_FILE, "w")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy()
        if not hold:
            fcntl.flock(fd, fcntl.LOCK_UN)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        fd.close()


# ── 打印 ─────────────────────────────────────────────────────────────────────

def print_checks(checks, quiet: bool = False) -> None:
    for st in _ORDER:
        for c in (x for x in checks if x["status"] == st):
            if quiet and st in (svc.OK, svc.EXPIRED):
                continue
            print(f"  {_ICON[st]} {c['title']}：{c['summary']}")
            fx = c.get("fix") or {}
            if not quiet and st in (svc.ERROR, svc.GAP, svc.WARN) and fx.get("label"):
                hint = f"（python -m scripts.data_audit fix {c['id']}）" if fx.get("kind") == "server" else ""
                print(f"      补法：{fx['label']}{hint}")


def print_report(rep: dict, quiet: bool = False) -> None:
    s, w = rep["summary"], rep.get("window") or {}
    print(f"数据体检｜截止 {rep.get('through')}｜最近 {w.get('days', 0)} 个交易日"
          f"（{w.get('start')} ~ {w.get('end')}）｜{rep['generated_at']}")
    print(f"  待处理 {s['todo']} 项｜提醒 {s['warn']} 项｜补不回来 {s['expired']} 项｜正常 {s['ok']} 项")
    print_checks(rep["checks"], quiet)


# ── 各项补法（fixer 返回退出码；试跑只打印将做什么）─────────────────────────

def _dates(ds, n: int = 10) -> str:
    return "、".join(ds[:n]) + (f" …共 {len(ds)} 天" if len(ds) > n else "")


def _span(db, oldest: str, extra: int = 3) -> int:
    """从最早缺的那天到截止日有几个交易日（再多几根做余量）。"""
    ctx = svc.build_context(db)
    return len([d for d in ctx.window if d >= date.fromisoformat(oldest)]) + extra


def fix_archive(db, a) -> int:
    from app.services.fuyao_archive import archive_max_date
    print(f"存档现在覆盖到 {archive_max_date() or '（没有存档）'}")
    if not a.apply:
        print("试跑（不写库）：将重新下载同花顺 10 年日K存档（约 6 分钟、可续传；只写 data/fuyao/，不改数据库）")
        return 0
    from scripts.full_dump import cmd_refresh
    cmd_refresh()
    return 0


def fix_stock_snapshots(db, a) -> int:
    from scripts.full_dump import cmd_heal
    cmd_heal(svc.WINDOW_DAYS, a.apply)
    return 0


def fix_index_daily(db, a) -> int:
    r = svc.run_check(db, "index_daily")
    items = r.get("items") or []
    if not items:
        print("指数日线没有缺口")
        return 0
    for it in items:
        print(f"  {it['name']}（{it['code']}）缺 {it['missing']} 天：{_dates(it['missing_dates'])}")
    span = _span(db, min(d for it in items for d in it["missing_dates"]))
    if not a.apply:
        print(f"试跑（不写库）：将按最近 {span} 根重拉 5 个指数（东财→腾讯→新浪兜底），已有行会用同一来源的值刷新")
        return 0
    from app.services.index_trend_service import sync_index_bars
    res = sync_index_bars(db, days=span)
    print(f"完成：{res['ok']}/5 个指数，写入 / 刷新 {res['upserts']} 行"
          + (f"；出错：{'；'.join(res['errors'])}" if res.get("errors") else ""))
    return 0


def fix_market_breadth(db, a) -> int:
    r = svc.run_check(db, "market_breadth")
    print(r["summary"])
    if r["status"] != svc.GAP:
        print("没有能补的缺口")
        return 0
    need_margin = bool(r.get("margin_missing"))
    if not a.apply:
        print("试跑（不写库）：将先跑一次日常同步 sync_market_breadth"
              + ("，再跑两融全历史回填（只写两融 / 市盈率那几列）" if need_margin else ""))
        return 0
    from app.services.windvane_service import sync_market_breadth
    res = sync_market_breadth(db)
    print(f"日常同步：{res['ok']}/3 个模块" + (f"；出错：{'；'.join(res['errors'])}" if res.get("errors") else ""))
    if need_margin:
        from scripts.backfill_margin_history import run as backfill_margin
        backfill_margin()
    return 0


def fix_limit_up_details(db, a) -> int:
    miss = svc.run_check(db, "limit_up_details").get("missing_dates") or []
    if not miss:
        print("涨停 / 炸板明细没有缺口")
        return 0
    print(f"缺 {len(miss)} 个交易日：{_dates(miss)}")
    if not a.apply:
        print("试跑（不写库）：将逐日向东财补涨停 / 炸板明细（每天一次请求，间隔 2 秒；太久远的日子东财可能不给）")
        return 0
    from app.services.limit_up_detail_service import sync_limit_up_details
    for i, d in enumerate(miss):
        if i:
            time.sleep(2)
        try:
            lu, bb, warns = sync_limit_up_details(db, date.fromisoformat(d))
            print(f"  {d}：涨停 {lu} 只，炸板 {bb} 只" + (f"（{'；'.join(warns)}）" if warns else ""))
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"  {d}：失败 {type(e).__name__}: {str(e)[:100]}")
    return 0


def fix_market_effect(db, a) -> int:
    miss = svc.run_check(db, "market_effect").get("missing_dates") or []
    if not miss:
        print("市场效应没有缺口")
        return 0
    print(f"缺 {len(miss)} 个交易日：{_dates(miss)}")
    if not a.apply:
        print("试跑（不写库）：将用库里已有的快照重算（不向外部请求；已是最新版本的行跳过）")
        return 0
    from scripts.backfill_market_effects import run as backfill_effects
    backfill_effects(force=False)
    return 0


def fix_leader_cycle(db, a) -> int:
    r = svc.run_check(db, "leader_cycle")
    miss = r.get("missing_dates") or []
    print(r["summary"])
    if (r.get("fix") or {}).get("kind") != "server" or not miss:
        print("没有能在这里补的缺口（今天那行归日更写）")
        return 0
    print(r["fix"]["note"])
    days = _span(db, miss[0], extra=0)
    if not a.apply:
        print(f"试跑（不写库）：将用库里 K 线重建最近 {days} 个交易日里缺的那些天（已有行不动）")
        return 0
    import scripts.backfill_leader_cycle_snapshots as blc
    argv, sys.argv = sys.argv, ["backfill_leader_cycle_snapshots", "--days", str(days)]
    try:
        blc.main()
    finally:
        sys.argv = argv
    return 0


def fix_sector_index(db, a) -> int:
    """板块指数的「补」= 导入收件箱里本机导出的文件（历史只能在本机取，见 FIX_EXPORT）。"""
    if not a.file:
        print(svc.run_check(db, "sector_index")["summary"])
        files = svc.list_inbox()
        print(f"收件箱 {svc.INBOX_DIR}：" + ("、".join(f["name"] for f in files) if files else "空的"))
        print("导入要指定文件：--file 文件名")
        return 2
    path = svc.INBOX_DIR / a.file
    if not _FILE_RE.match(a.file) or not path.is_file():
        print(f"收件箱里没有 {a.file}")
        return 2
    from scripts.import_sector_klines import import_file
    r = import_file(db, str(path), dry_run=not a.apply)
    print(f"{r['sectors']} 个板块｜新增 {r['added']} 行｜已存在跳过 {r['skipped_exist']} 行"
          + (f"｜解析失败 {r['bad']} 行" if r["bad"] else "")
          + (f"｜东财没有指数日线 {len(r['no_data'])} 个" if r["no_data"] else ""))
    if not a.apply:
        print("试跑（不写库）。数字没问题就确认导入——已有行一律不覆盖")
        return 0
    if r["added"] == 0 and not r["no_data"]:
        print("⚠️ 一行都没写——文件里的数据库里都已经有了，或者文件是空的")
    done = svc.INBOX_DIR / "done"
    done.mkdir(exist_ok=True)
    shutil.move(str(path), str(done / a.file))
    print(f"文件已挪到 {done}")
    return 0


FIXERS = {
    "archive": fix_archive,
    "stock_snapshots": fix_stock_snapshots,
    "index_daily": fix_index_daily,
    "market_breadth": fix_market_breadth,
    "limit_up_details": fix_limit_up_details,
    "market_effect": fix_market_effect,
    "leader_cycle": fix_leader_cycle,
    "sector_index": fix_sector_index,
}


# ── 子命令 ───────────────────────────────────────────────────────────────────

def cmd_run(a) -> int:
    try:
        with (nullcontext() if a.no_lock else update_lock(hold=False)):
            db = SessionLocal()
            try:
                rep = svc.run_audit(db, only=set(a.check) if a.check else None)
            finally:
                db.close()
    except Busy:
        print("日更或补数任务正在跑，这次先不查（写到一半的数据会被当成缺口）")
        return EXIT_BUSY
    if a.save:
        rep = svc.save_report(rep, merge=bool(a.check))
    print_report(rep, quiet=a.quiet)
    return 0


def cmd_fix(a) -> int:
    fixer = FIXERS.get(a.check)
    if fixer is None:
        print(f"「{a.check}」没有一键补法。能一键补的：{'、'.join(FIXERS)}")
        return 2
    recheck = RECHECK.get(a.check, {a.check})
    try:
        # 试跑也探锁：日更写到一半时列出来的「将补什么」是错的
        with update_lock(hold=a.apply):
            db = SessionLocal()
            try:
                code = fixer(db, a)
                if code or not a.apply:
                    return code
                print("\n—— 复查 ——")
                rep = svc.save_report(svc.run_audit(db, only=recheck), merge=True)
                print_checks([c for c in rep["checks"] if c["id"] in recheck])
            finally:
                db.close()
    except Busy:
        print("日更或别的补数任务正在跑，这次什么都没动。等它跑完再来")
        return EXIT_BUSY
    return 0


def cmd_export(a) -> int:
    rep = svc.load_report()
    if rep is None:
        db = SessionLocal()
        try:
            rep = svc.run_audit(db, only={"sector_index"})
        finally:
            db.close()
    codes = svc.sector_export_codes(rep)
    if a.codes:
        print(",".join(codes))
        return 0
    js = svc.render_export_script(codes)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(js)
        print(f"{len(codes)} 个板块 → {a.out}", file=sys.stderr)
    else:
        sys.stdout.write(js)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="检测（只读）")
    r.add_argument("--check", action="append", choices=svc.CHECK_IDS,
                   help="只查这一项（可重复）；配 --save 时合并进现有报告")
    r.add_argument("--save", action="store_true", help="写 logs/data_audit/latest.json")
    r.add_argument("--no-lock", action="store_true", help="不探日更锁（只给正持着锁的日更自己调用）")
    r.add_argument("--quiet", action="store_true", help="只打印要处理的项")
    f = sub.add_parser("fix", help="补数：默认试跑，--apply 才写库")
    f.add_argument("check")
    f.add_argument("--apply", action="store_true")
    f.add_argument("--file", help="sector_index 专用：收件箱里的文件名")
    e = sub.add_parser("export-script", help="生成板块指数导出脚本")
    e.add_argument("--out")
    e.add_argument("--codes", action="store_true",
                   help="只打印缺数据的板块码（逗号分隔），给 export_sector_klines.py --codes 用")
    a = ap.parse_args()
    return {"run": cmd_run, "fix": cmd_fix, "export-script": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
