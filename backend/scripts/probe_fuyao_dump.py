#!/usr/bin/env python3
"""
同花顺官方 API（fuyao.aicubes.cn）全市场日K dump 可用性验证（2026-08-26新增）。

背景：我们现在为 5000+ 只股票逐只打腾讯/新浪 K 线接口，这是所有限流痛苦的根源。
fuyao 提供「全市场最近10个交易日日K」的单个 Parquet 下载，如果它当天收盘后就有
当日数据，整条逐股拉取链路可以退成兜底。

这个脚本要回答的问题按重要性排序：

  1. **文件里真的有今天吗**——下载链接路径里的日期是 dump 的"生成日"，不等于
     文件内容覆盖到那一天。这个仓库刚踩过一模一样的坑（commit 2c9b4b4：
     "返回了但缺那一天"必须算没拿到），所以这里只认 Parquet 里真实的 max(date_ms)。
  2. 覆盖是否完整——每个交易日的股票数是否稳定，有没有半途截断。
  3. 数值对不对——跟我们库里 stock_daily_snapshots.close_price 逐只比。dump 是
     **未复权**(adjusted=none)，我们库里的收盘价来自腾讯前复权，所以近期除权过的
     票会有系统性偏差；这里报告偏差分布而不是断言相等，让人自己看是"个别票除权"
     还是"整体口径不同"。

用法（在服务器上）：
    cd /opt/code/tradeflux/backend
    source .venv/bin/activate
    pip install pyarrow                       # 只为这个验证装，暂不进 requirements
    export FUYAO_KEY='你的key'
    python scripts/probe_fuyao_dump.py                # 默认 daily-k-10d
    python scripts/probe_fuyao_dump.py --kind daily-k # 10年全量（文件很大，慎用）
    python scripts/probe_fuyao_dump.py --no-db        # 只看文件，不连数据库
    deactivate
"""
import argparse
import os
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone, timedelta

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "https://fuyao.aicubes.cn"
_SH_TZ = timezone(timedelta(hours=8))


def _ms_to_date(ms: int):
    """dump 的 date_ms 是 Asia/Shanghai 零点，必须按 +08 解释，别用本地时区。"""
    return datetime.fromtimestamp(ms / 1000, tz=_SH_TZ).date()


def get_download_url(key: str, kind: str) -> dict:
    url = f"{BASE}/api/dump/market-dumps/{kind}/download-url"
    with httpx.Client(timeout=30) as c:
        resp = c.get(url, headers={"X-api-key": key})
    body = resp.json()
    if body.get("code") != 0:
        raise SystemExit(f"❌ 取下载链接失败 code={body.get('code')} {body.get('message')}")
    return body["data"]


def download(url: str, dest: str) -> int:
    t0 = time.time()
    total = 0
    with httpx.Client(timeout=180, follow_redirects=True) as c:
        with c.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
                    total += len(chunk)
    print(f"  下载完成 {total/1048576:.1f} MB，耗时 {time.time()-t0:.1f}s")
    return total


def inspect(path: str) -> dict:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise SystemExit("❌ 缺 pyarrow：先在 venv 里 pip install pyarrow")
    t = pq.read_table(path)
    cols = t.column_names
    print(f"\n  列：{cols}")
    print(f"  行数：{t.num_rows:,}")

    dates = [_ms_to_date(v) for v in t.column("date_ms").to_pylist()]
    codes = t.column("thscode").to_pylist()
    per_day = Counter(dates)
    print(f"\n  {'交易日':<12}{'股票数':>10}")
    for d in sorted(per_day):
        print(f"  {d.isoformat():<12}{per_day[d]:>10,}")
    counts = [per_day[d] for d in sorted(per_day)]
    if counts and (max(counts) - min(counts)) > max(counts) * 0.05:
        print(f"  ⚠️  各日股票数波动超过 5%（{min(counts):,} ~ {max(counts):,}），"
              f"可能是停牌/新股，也可能是 dump 不完整")

    return {
        "table": t,
        "max_date": max(dates) if dates else None,
        "dates": sorted(per_day),
        "codes": set(codes),
    }


def compare_db(t, target_date) -> None:
    """跟我们库里同一天的 close_price 比对。"""
    from app.database import SessionLocal
    from app.models.stock import Stock, StockDailySnapshot

    rows = {}
    for code, ms, close in zip(t.column("thscode").to_pylist(),
                               t.column("date_ms").to_pylist(),
                               t.column("close_price").to_pylist()):
        if _ms_to_date(ms) == target_date:
            rows[code.split(".")[0]] = close

    db = SessionLocal()
    try:
        pairs = (db.query(Stock.code, StockDailySnapshot.close_price)
                 .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
                 .filter(StockDailySnapshot.date == target_date,
                         StockDailySnapshot.close_price.isnot(None))
                 .all())
    finally:
        db.close()

    if not pairs:
        print(f"\n  我们库里 {target_date} 没有带 close_price 的快照，跳过比对")
        return

    diffs, missing, exact = [], 0, 0
    for code, ours in pairs:
        theirs = rows.get(code)
        if theirs is None:
            missing += 1
            continue
        if ours and abs(theirs - ours) < 0.005:
            exact += 1
        elif ours:
            diffs.append((abs(theirs - ours) / ours * 100, code, ours, theirs))

    total = len(pairs)
    print(f"\n  比对 {target_date}：我们库 {total} 只，dump 覆盖 {total - missing} 只，"
          f"dump 缺 {missing} 只")
    print(f"  完全一致（<0.005元）：{exact} 只（{exact/max(total-missing,1)*100:.1f}%）")
    if diffs:
        diffs.sort(reverse=True)
        print(f"  有差异：{len(diffs)} 只，偏差最大的 10 只：")
        for pct, code, ours, theirs in diffs[:10]:
            print(f"    {code}  我们={ours:.2f}  dump={theirs:.2f}  差 {pct:.2f}%")
        print("  提示：dump 是未复权、我们库是腾讯前复权，近期除权的票必然对不上——"
              "少数几只属正常，成片对不上才是口径问题。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="daily-k-10d",
                    choices=["daily-k-10d", "daily-k", "adjustment-factors"])
    ap.add_argument("--no-db", action="store_true", help="不连数据库，只看文件")
    ap.add_argument("--keep", help="把下载的 parquet 留在这个路径")
    args = ap.parse_args()

    key = os.environ.get("FUYAO_KEY")
    if not key:
        raise SystemExit("❌ 请先 export FUYAO_KEY='你的key'")

    print("=" * 72)
    print(f"fuyao dump 验证  {args.kind}  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 72)

    data = get_download_url(key, args.kind)
    url = data["presigned_url"]
    print(f"\n[1] 下载链接（{data.get('expires_in_seconds')}秒后过期）")
    m = re.search(r"/releases/(\d{8})/", url)
    path_date = m.group(1) if m else "?"
    print(f"  路径里的 dump 生成日：{path_date}")

    dest = args.keep or os.path.join(tempfile.gettempdir(), f"fuyao_{args.kind}.parquet")
    print(f"\n[2] 下载到 {dest}")
    download(url, dest)

    print(f"\n[3] 文件内容")
    info = inspect(dest)

    print(f"\n[4] 结论")
    today = datetime.now(_SH_TZ).date()
    mx = info["max_date"]
    print(f"  路径生成日 {path_date} / 文件内最新交易日 {mx} / 今天 {today}")
    if mx == today:
        print("  ✅ 文件里有今天的 K 线 —— dump 可以当日更新的主力源，"
              "逐股拉取可退成兜底")
    elif path_date == today.strftime("%Y%m%d"):
        print("  ⚠️  dump 今天生成了，但内容只到 " + str(mx) + " —— 这正是"
              "「返回了但缺那一天」，当日数据仍需腾讯，dump 只能补历史窗口")
    else:
        print("  ⚠️  dump 尚未生成今天的版本，当日数据仍需腾讯")

    if not args.no_db and args.kind != "adjustment-factors" and mx:
        print(f"\n[5] 跟我们库比对")
        compare_db(info["table"], mx)

    if not args.keep:
        os.unlink(dest)


if __name__ == "__main__":
    main()
