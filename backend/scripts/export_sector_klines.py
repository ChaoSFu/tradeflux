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

开发机这边的 push2his 也按频率限流：2026-09-12 实测约 2 秒一个、连取 20 个就被掐
（浏览器控制台里也一样），封禁至少二十分钟。所以默认 15 秒一个 + 抖动，连续失败 5 次
就停手，过一阵加 `--resume` 接着导。**别把自己家的 IP 也打进去**——真被封了就连这条
路都没了。300 个板块约 75 分钟。
"""
import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.services.eastmoney_fetcher import KLINE_URL, _parse_sector_klines

DEFAULT_API = "http://47.250.165.189"


# 东财行情页请求 push2his 时自带的公开参数（不是账号凭据）+ 浏览器本来就会带的请求头
_UT = "fa5fd1943c7b386f172d6893dbfba10b"
# 跟浏览器里那条能通的请求（2026-09-12 从 DevTools 复制）一模一样的一整套请求头。
# 当天一度以为「带不带 ut、请求头全不全」决定能不能过；后来浏览器自己也是连取 20 个就被掐——
# 真正起作用的是频率限流。这套头只是让请求跟网页发的一致，不是通关钥匙，别指望靠改头提速
_PAGE_HEADERS = {
    "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9", "Referer": "https://quote.eastmoney.com/",
    "Sec-Fetch-Dest": "script", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Site": "same-site",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
    "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"macOS"',
}


def _fetch_one(code: str, days: int):
    """
    像东财行情页一样取一个板块：每次新建连接、带 ut 和浏览器请求头。返回 (rows, kind, detail)。

    失败分类跟 fetch_sector_kline_detailed 一致：blocked 被拦（被掐连接、403/429/451/503、
    返回的不是 JSON，算连续失败）/ no_data 板块本来没有指数日线（不算）/ error 拿到了但解析不出。
    """
    try:
        with httpx.Client(headers=_PAGE_HEADERS, timeout=20, follow_redirects=True) as c:
            r = c.get(KLINE_URL, params={
                "secid": f"90.{code}", "ut": _UT,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": 101, "fqt": 1, "end": "20500101", "lmt": days,
            })
    except Exception as e:  # noqa: BLE001
        return [], "blocked", f"{type(e).__name__}: {str(e)[:80]}"
    if r.status_code in (403, 429, 451, 503):
        return [], "blocked", f"HTTP {r.status_code}"
    try:
        payload = r.json()
    except ValueError:
        return [], "blocked", f"不是 JSON：{r.text[:60]!r}"
    raw = (payload.get("data") or {}).get("klines") or []
    if not raw:
        return [], "no_data", "data.klines 为空"
    rows = _parse_sector_klines(raw)   # 跟服务端同一套解析
    return (rows, "ok", "") if rows else ([], "error", f"拿到 {len(raw)} 行但解析不出")


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
    ap.add_argument("--delay", type=float, default=15,
                    help="每次请求间隔秒。实测约 2 秒一个、连取 20 个就会被限流；"
                         "别调小——把自己的 IP 也打进去就没退路了")
    ap.add_argument("--codes", help="逗号分隔，直接指定板块码，跳过 API。只补缺的：在服务器上跑 "
                                    "python -m scripts.data_audit export-script --codes，把输出贴过来")
    ap.add_argument("--stop-after-failures", type=int, default=5,
                    help="连续失败这么多次就停手——多半是被限流了，接着打只会把自己的 IP 也打死")
    ap.add_argument("--resume", action="store_true",
                    help="接着上次的 --out 往下导：文件里已有的板块跳过，追加写（失败的会重试）")
    args = ap.parse_args()

    codes = ([c.strip() for c in args.codes.split(",") if c.strip()]
             if args.codes else _sector_codes(args.api, args.scope))
    if not codes:
        print("没有拿到板块列表")
        return
    if args.resume and os.path.exists(args.out):
        done = set()
        with open(args.out, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["code"])
                except (ValueError, KeyError):
                    pass
        codes = [c for c in codes if c not in done]
        print(f"--resume：{args.out} 里已有 {len(done)} 个板块，这次只导剩下的 {len(codes)} 个")
        if not codes:
            print("没有剩下的，不发请求")
            return
    print(f"{len(codes)} 个板块，间隔 {args.delay}s + 抖动，"
          f"预计 {len(codes) * args.delay / 60:.0f} 分钟\n")

    ok = fail = no_data = bars_total = streak = 0
    stopped = False
    # 取数见 _fetch_one：每次新连接 + ut + 浏览器请求头（2026-09-12 实测能过，见那里的说明）
    with open(args.out, "a" if args.resume else "w", encoding="utf-8") as f:
        for i, code in enumerate(codes):
            if i:
                time.sleep(args.delay * random.uniform(0.8, 1.4))
            rows, kind, detail = _fetch_one(code, args.days)
            if kind == "no_data":
                # 合法 JSON 但没有序列：这个板块本来就没有指数日线，不是被拦——不算连续失败。
                # 写一条空记录：导入时记下来，数据体检就不再把它当缺口；--resume 也不会再取它
                print(f"  {code} 无数据（该板块没有指数日线）")
                f.write(json.dumps({"code": code, "rows": []}) + "\n")
                no_data += 1
                streak = 0
                continue
            if kind != "ok":
                fail += 1
                print(f"  {code} 失败（{kind}）: {detail}")
                streak += 1
                if streak >= args.stop_after_failures:
                    print(f"\n连续失败 {streak} 次，停手——多半是被限流了，别硬打。过一阵接着导：")
                    print(f"  python scripts/export_sector_klines.py --out {args.out} --resume")
                    stopped = True
                    break
                time.sleep(3)          # 失败就多歇一会，别硬打
                continue
            streak = 0
            f.write(json.dumps({"code": code, "rows": rows}, ensure_ascii=False) + "\n")
            ok += 1
            bars_total += len(rows)
            if ok % 20 == 0:
                print(f"  已完成 {ok}/{len(codes)}，累计 {bars_total} 根")

    print(f"\n成功 {ok} 个板块，共 {bars_total} 根，写入 {args.out}")
    if no_data:
        print(f"无数据 {no_data} 个（东财没有这些板块的指数日线，已写空记录，导入时会记下）")
    if fail:
        print(f"失败 {fail} 个。加 --resume 重跑会只补这些（已导出的跳过、追加写）")
    if stopped:
        return
    print(f"\n下一步：把 {args.out} 传到服务器，然后跑")
    print(f"  python scripts/import_sector_klines.py {args.out}")


if __name__ == "__main__":
    main()
