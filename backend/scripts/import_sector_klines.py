"""
把 export_sector_klines.py 导出的文件写进 SectorIndexDaily。**零外部请求。**

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
"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.market_index import SectorIndexDaily


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="export_sector_klines.py 导出的 jsonl")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        added = skipped_exist = bad = 0
        sectors = 0
        with open(args.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    code, rows = rec["code"], rec["rows"]
                except (ValueError, KeyError):
                    bad += 1
                    continue
                sectors += 1
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
                    if not args.dry_run:
                        db.add(SectorIndexDaily(
                            sector_code=code, date=d, close=close,
                            pct_change=r.get("pct_change"), open=r.get("open"),
                            high=r.get("high"), low=r.get("low"),
                            volume=r.get("volume"), amount=r.get("amount")))
                    n += 1
                    added += 1
                if not args.dry_run and n:
                    db.commit()
        print(f"{sectors} 个板块｜新增 {added} 行｜已存在跳过 {skipped_exist} 行"
              + (f"｜解析失败 {bad} 行" if bad else ""))
        if args.dry_run:
            print("--dry-run：未写库")
        elif added == 0:
            print("⚠️ 一行都没写。检查文件内容，别让『导入成功』掩盖『其实没写』")
        else:
            print("\n下一步：回填生命周期快照，RS_sector 才会用上真板块指数序列")
            print("  python scripts/backfill_leader_cycle_snapshots.py "
                  "--days 60 --overwrite --include-today")
    finally:
        db.close()


if __name__ == "__main__":
    main()
