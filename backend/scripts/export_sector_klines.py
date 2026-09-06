"""
在**能连通 push2his 的机器上**拉板块 K 线，导出成文件，再传到服务器导入。

## 为什么要这么绕

2026-09-06 实测：同样的 URL、同样的参数，在开发机上返回完整 300 根，在生产
服务器上连接直接被掐（`RemoteProtocolError: Server disconnected without sending
a response`）。同一台服务器上 push2delay / push2ex / datacenter / quote 网页
全部正常，只有 push2his 一个子域名对它关门。

所以这不是限流、不是缺请求头、也不是 keep-alive 死连接——**是那个出口 IP 被
单独挡了**。降速和退避都救不了。

而板块历史只需要成功**一次**：拿到之后往后每天那一根由 sync_boards 从它已有的
clist 调用里顺手写（f2 字段，零新增请求，走的是没被拦的 push2delay）。

    历史 300 根   这个脚本，一次性，在通的机器上跑
    每日增量      sync_boards，零新增请求，走 push2delay

## 用法

    # 在能连通的机器上（板块列表从生产 API 取，不需要本地库）
    python scripts/export_sector_klines.py --out sectors.jsonl

    # 传到服务器后
    python scripts/import_sector_klines.py sectors.jsonl

## 礼貌

默认 1.5 秒间隔 + 抖动，失败退避。**别把自己家的 IP 也打进去**——真被封了就
连这条路都没了。308 个板块约 10 分钟。
"""
import argparse
import json
import os
import random
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.services.eastmoney_fetcher import HEADERS, KLINE_URL, _parse_sector_klines

DEFAULT_API = "http://47.250.165.189"


def _sector_codes(api: str, scope: str) -> list:
    """板块列表从**生产 API** 取，不依赖本地数据库——本地库跟线上不是一份数据。"""
    r = httpx.get(f"{api}/api/sectors", timeout=30)
    r.raise_for_status()
    items = r.json().get("items") or []
    if scope == "evidence":
        # 只要今天有领先证据的（V2 主线表里出现的那些），最省请求
        def hit(s):
            ranks = [s.get(k) for k in ("rank_5d", "rank_10d", "rank_20d",
                                        "rank_lu", "rank_strong")]
            return any(r is not None and r <= 5 for r in ranks) \
                or (s.get("board_height") or 0) >= 4
        items = [s for s in items if hit(s)]
    return [s["code"] for s in items if s.get("code", "").startswith("BK")]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="sector_klines.jsonl")
    ap.add_argument("--api", default=DEFAULT_API, help="取板块列表的生产地址")
    ap.add_argument("--scope", choices=["all", "evidence"], default="all")
    ap.add_argument("--days", type=int, default=300)
    ap.add_argument("--delay", type=float, default=1.5,
                    help="每次请求间隔秒。别调小——把自己的 IP 也打进去就没退路了")
    ap.add_argument("--codes", help="逗号分隔，直接指定板块码，跳过 API")
    args = ap.parse_args()

    codes = ([c.strip() for c in args.codes.split(",") if c.strip()]
             if args.codes else _sector_codes(args.api, args.scope))
    if not codes:
        print("没有拿到板块列表")
        return
    print(f"{len(codes)} 个板块，间隔 {args.delay}s + 抖动，"
          f"预计 {len(codes) * args.delay / 60:.0f} 分钟\n")

    end = date.today().strftime("%Y%m%d")
    ok = fail = bars_total = 0
    # 每次新建 Client：这条路不追求快，追求"别被当成爬虫"
    with open(args.out, "w", encoding="utf-8") as f:
        for i, code in enumerate(codes):
            if i:
                time.sleep(args.delay * random.uniform(0.8, 1.4))
            try:
                with httpx.Client(headers=HEADERS, timeout=20,
                                  follow_redirects=True) as c:
                    resp = c.get(KLINE_URL, params={
                        "secid": f"90.{code}",
                        "fields1": "f1,f2,f3,f4,f5,f6",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                        "lmt": args.days, "klt": 101, "fqt": 1, "end": end,
                    })
                raw = ((resp.json().get("data") or {}).get("klines")) or []
                rows = _parse_sector_klines(raw)   # **跟服务端同一套解析**，不另写
            except Exception as e:  # noqa: BLE001
                print(f"  {code} 失败: {type(e).__name__}: {str(e)[:60]}")
                fail += 1
                time.sleep(3)          # 失败就多歇一会，别硬打
                continue
            if not rows:
                print(f"  {code} 无数据（该板块可能没有指数日线）")
                fail += 1
                continue
            f.write(json.dumps({"code": code, "rows": rows}, ensure_ascii=False) + "\n")
            ok += 1
            bars_total += len(rows)
            if ok % 20 == 0:
                print(f"  已完成 {ok}/{len(codes)}，累计 {bars_total} 根")

    print(f"\n成功 {ok} 个板块，共 {bars_total} 根，写入 {args.out}")
    if fail:
        print(f"失败 {fail} 个。**重跑会覆盖文件**，要续传请改 --out 另存再合并")
    print(f"\n下一步：把 {args.out} 传到服务器，然后跑")
    print(f"  python scripts/import_sector_klines.py {args.out}")


if __name__ == "__main__":
    main()
