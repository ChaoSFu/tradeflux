"""
把 export_sector_klines.py（或数据体检页面生成的浏览器导出脚本）导出的文件写进
SectorIndexDaily。**零外部请求。**

## 为什么需要这一步

2026-09-06 完整验证：这台生产服务器对 push2his.eastmoney.com 的任何请求都被拒。

    只有 secid 的最短 URL      (52) Empty reply
    全套参数                    (52)
    个股 secid=1.600000         (52)   ← 不是板块被单独挡
    请求头/UA/cookie 跟能通的机器完全一致  (52)
    钉到对方用的那个边缘节点     (52)   ← 不是坏节点
    IPv6                        (7)    服务器没有 IPv6 出口
    同一时刻 push2delay / push2ex / datacenter / quote 网页  200 ✓

所以跟参数、请求头、cookie、节点全都无关，是这台机器的出口被 push2his 单独拒。
降速、退避、换 UA 都没有意义。

而板块历史只需要成功**一次**：拿到之后每天那一根由 sync_boards 从 clist 顺手写
（走没被拦的 push2delay，零新增请求）。所以在能连通的机器上导出一次、导入进来，
就把这个缺口永久补上了。

## 三条纪律

1. **绝不覆盖已有行**。已有行可能是 sync_boards 每天写的真实收盘，导入的是
   前复权序列，两者口径不同——不能让导入的值盖掉当日权威值。
2. **日期与数值都要校验**。解析不出来的行跳过并计数，不写脏数据。
3. **报出实际写了多少**，别让"导入成功"掩盖"其实一行没写"。

文件里 `rows` 为空的记录表示「东财没有这个板块的指数日线」（导出时确认过），导入时
记进 AppConfig，数据体检就不会每天把它当缺口报。
"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.market_index import SectorIndexDaily


def import_file(db, path: str, dry_run: bool = False) -> dict:
    """
    导入一个导出文件。返回 {"sectors", "added", "skipped_exist", "bad", "no_data": [板块码]}。
    命令行和数据体检页面的「试跑导入 / 确认导入」走的都是这一个函数。
    """
    added = skipped_exist = bad = sectors = 0
    no_data = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                code, rows = rec["code"], rec["rows"]
            except (ValueError, KeyError, TypeError):
                bad += 1
                continue
            sectors += 1
            if not rows:
                no_data.append(code)
                continue
            have = {d for (d,) in db.query(SectorIndexDaily.date)
                    .filter(SectorIndexDaily.sector_code == code).all()}
            n = 0
            for r in rows:
                try:
                    d = date.fromisoformat(r["date"])
                    close = float(r["close"])
                except (KeyError, ValueError, TypeError):
                    bad += 1
                    continue
                if close <= 0:
                    bad += 1
                    continue
                if d in have:
                    # **绝不覆盖**：已有行可能是 sync_boards 写的当日权威收盘，
                    # 而导入的是前复权序列，口径不同
                    skipped_exist += 1
                    continue
                if not dry_run:
                    db.add(SectorIndexDaily(
                        sector_code=code, date=d, close=close,
                        pct_change=r.get("pct_change"), open=r.get("open"),
                        high=r.get("high"), low=r.get("low"),
                        volume=r.get("volume"), amount=r.get("amount")))
                n += 1
                added += 1
            if not dry_run and n:
                db.commit()
    if not dry_run and no_data:
        from app.services.data_audit_service import record_no_data_codes
        record_no_data_codes(db, no_data, date.today())
    return {"sectors": sectors, "added": added, "skipped_exist": skipped_exist,
            "bad": bad, "no_data": no_data}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="export_sector_klines.py 或浏览器导出脚本导出的 jsonl")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        r = import_file(db, args.path, dry_run=args.dry_run)
    finally:
        db.close()
    print(f"{r['sectors']} 个板块｜新增 {r['added']} 行｜已存在跳过 {r['skipped_exist']} 行"
          + (f"｜解析失败 {r['bad']} 行" if r["bad"] else "")
          + (f"｜东财没有指数日线 {len(r['no_data'])} 个" if r["no_data"] else ""))
    if args.dry_run:
        print("--dry-run：未写库")
    elif r["added"] == 0 and not r["no_data"]:
        print("⚠️ 一行都没写。检查文件内容，别让『导入成功』掩盖『其实没写』")
    else:
        # 2026-09-12 核对：生命周期快照的 RS_sector 走东财板块区间涨幅（rs_sector_source=vendor），
        # 不读这张表。导入只是把板块指数历史补齐备用，**不用**再回填生命周期快照
        print("\n已写入。现在还没有页面读板块指数日线（RS_sector 用的是东财板块区间涨幅），"
              "不用再回填生命周期快照。复查：python -m scripts.data_audit run --check sector_index --save")


if __name__ == "__main__":
    main()
