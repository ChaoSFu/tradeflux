#!/usr/bin/env python3
"""
东财接口可用性诊断（2026-08-26新增）。

背景：生产服务器上 push2his/push2 这一系域名成片 RemoteProtocolError，但同样的
请求在开发机上 HTTP 200 正常返回，怀疑是针对服务器出口IP的限流。限流可能是
**阶段性**的（按时段、按累计请求量、或滑动窗口），单次测试看不出来，所以这个
脚本按轮次重复采样，并带上腾讯/新浪作为对照组——如果对照组也同时变差，那就是
网络问题而不是东财针对性限流。

用法（在服务器上）：
    cd /opt/code/tradeflux/backend
    source .venv/bin/activate
    python scripts/diagnose_eastmoney.py                    # 默认 10 轮 × 间隔 30 秒
    python scripts/diagnose_eastmoney.py --rounds 60 --interval 60   # 跑 1 小时
    python scripts/diagnose_eastmoney.py --burst 20         # 额外做一次并发压测
    deactivate

输出两部分：
  1. 逐轮明细——每个接口的 HTTP 状态/耗时/错误类型，能看出"什么时候开始坏的"
  2. 汇总——各接口成功率。对照组正常而东财全挂 ⇒ 针对性限流坐实
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import httpx

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

# 各家 Referer 要求不同：拿东财的 Referer 去打新浪会被跳转页挡掉（这个脚本第一版
# 就踩了，产生假阳性）。诊断工具报假警比不报还糟，所以每个目标带自己的 headers。
_SINA_HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": _UA["User-Agent"]}
_TENCENT_HEADERS = {"Referer": "https://finance.qq.com/", "User-Agent": _UA["User-Agent"]}

# (标签, 方法, URL, params, headers)
_TARGETS = [
    # ── 东财：当前生产上报错的两个 ──────────────────────────────────────────
    ("东财 K线 push2his", "GET",
     "https://push2his.eastmoney.com/api/qt/stock/kline/get",
     {"secid": "1.600000", "klt": "101", "fqt": "1", "end": "20500101", "lmt": "5",
      "fields1": "f1,f2,f3", "fields2": "f51,f53,f55"}, _UA),
    ("东财 行情 push2", "GET",
     "https://push2.eastmoney.com/api/qt/ulist.np/get",
     {"secids": "1.600000,0.002821", "fields": "f2,f3,f12,f14", "fltt": "2"}, _UA),

    # ── 东财：当前生产上正常的三个（用来区分"整个东财挂了"还是"只有push2系挂了"）──
    ("东财 涨停池 push2ex", "GET",
     "https://push2ex.eastmoney.com/getTopicZTPool",
     {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt", "Pageindex": "0",
      "pagesize": "5", "sort": "fbt:asc",
      "date": datetime.now().strftime("%Y%m%d")}, _UA),
    ("东财 报表 datacenter", "GET",
     "https://datacenter.eastmoney.com/securities/api/data/v1/get",
     {"source": "SECURITIES", "client": "APP",
      "reportName": "RPT_PCHOT_LIMITLIST_HSDETIAL",
      "columns": "SECURITY_CODE", "pageNumber": "1", "pageSize": "1"}, _UA),
    ("东财 选股 np-tjxg", "GET",
     "https://np-tjxg-g.eastmoney.com/api/smart-tag/stock/v3/pw/search-code", None, _UA),

    # ── 对照组：这两个正常说明网络本身没问题 ────────────────────────────────
    ("[对照] 腾讯 K线", "GET",
     "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
     {"param": "sh600000,day,,,5,qfq"}, _TENCENT_HEADERS),
    ("[对照] 新浪 K线", "GET",
     "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
     "?symbol=sh600000&scale=240&ma=no&datalen=5",
     None, _SINA_HEADERS),
]


def _probe(label: str, method: str, url: str, params, headers=None, timeout: int = 15) -> dict:
    t0 = time.time()
    try:
        with httpx.Client(headers=headers or _UA, follow_redirects=False, timeout=timeout) as c:
            resp = c.request(method, url, params=params)
        ms = int((time.time() - t0) * 1000)
        body = resp.content
        # 200 但 body 极小/不是 JSON 也算不健康——限流常见的表现是返回空或错误页
        note = ""
        ok = 200 <= resp.status_code < 300 and len(body) > 50
        if resp.status_code in (301, 302, 307, 308):
            note = f"→{resp.headers.get('location', '?')[:40]}"
            ok = False
        elif ok:
            try:
                resp.json()
            except Exception:  # noqa: BLE001
                # 不是 JSON 不代表坏：腾讯行情是 v_xxx="..."、新浪行情是 var hq_str_...、
                # 新浪K线是 /*注释*/ 开头再跟 JSONP。判定标准跟各自解析器保持一致——
                # 能取出 (...) 里的 payload 就算健康。（第一版只认 var/v_/( 三种前缀，
                # 把新浪K线误报成故障，白白让人去查一个不存在的问题。）
                txt = body.lstrip()
                l, r = body.find(b"("), body.rfind(b")")
                jsonp_ok = 0 <= l < r and (r - l) > 20
                if not (txt.startswith((b"var ", b"v_", b"(", b"/*", b"{")) or jsonp_ok):
                    note = f"非JSON body={body[:40]!r}"
                    ok = False
        elif 200 <= resp.status_code < 300:
            note = f"body仅{len(body)}字节"
        return {"label": label, "ok": ok, "code": resp.status_code, "ms": ms,
                "bytes": len(body), "err": "", "note": note}
    except Exception as e:  # noqa: BLE001
        return {"label": label, "ok": False, "code": 0,
                "ms": int((time.time() - t0) * 1000), "bytes": 0,
                "err": type(e).__name__, "note": str(e)[:50]}


def _round_once() -> list:
    """一轮：每个接口串行打一次（串行是为了排除并发因素）。"""
    return [_probe(lbl, m, u, p, h) for lbl, m, u, p, h in _TARGETS]


def _burst(n: int) -> None:
    """并发压测：同一个接口瞬间打 n 次，看是不是「按并发」触发限流。"""
    print(f"\n{'='*72}\n并发压测：push2his 与 腾讯K线 各并发 {n} 次\n{'='*72}")
    for label, url, params, hdr in [
        ("东财 push2his",
         "https://push2his.eastmoney.com/api/qt/stock/kline/get",
         {"secid": "1.600000", "klt": "101", "fqt": "1", "end": "20500101", "lmt": "5",
          "fields1": "f1", "fields2": "f51,f53"}, _UA),
        ("[对照] 腾讯K线",
         "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
         {"param": "sh600000,day,,,5,qfq"}, _TENCENT_HEADERS),
    ]:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(_probe, label, "GET", url, params, hdr) for _ in range(n)]
            res = [f.result() for f in as_completed(futs)]
        ok = sum(1 for r in res if r["ok"])
        errs = {}
        for r in res:
            if not r["ok"]:
                k = r["err"] or f"HTTP{r['code']}"
                errs[k] = errs.get(k, 0) + 1
        print(f"  {label:16} 成功 {ok}/{n}  耗时{time.time()-t0:.1f}s  "
              f"失败分布={errs or '无'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=10, help="采样轮数（默认10）")
    ap.add_argument("--interval", type=int, default=30, help="轮间隔秒数（默认30）")
    ap.add_argument("--burst", type=int, default=0, help="额外做一次N并发压测（0=不做）")
    args = ap.parse_args()

    print(f"{'='*72}")
    print(f"东财接口诊断  开始于 {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"共 {args.rounds} 轮，间隔 {args.interval} 秒"
          f"（预计 {args.rounds * args.interval // 60} 分钟）")
    print(f"{'='*72}")

    stats: dict = {lbl: {"ok": 0, "total": 0, "errs": {}} for lbl, *_ in _TARGETS}  # noqa

    for i in range(1, args.rounds + 1):
        rows = _round_once()
        print(f"\n[第 {i}/{args.rounds} 轮  {datetime.now():%H:%M:%S}]")
        for r in rows:
            st = stats[r["label"]]
            st["total"] += 1
            if r["ok"]:
                st["ok"] += 1
            else:
                k = r["err"] or f"HTTP{r['code']}"
                st["errs"][k] = st["errs"].get(k, 0) + 1
            flag = "✅" if r["ok"] else "❌"
            detail = f"HTTP {r['code']:<3} {r['ms']:>5}ms {r['bytes']:>7}B"
            if r["err"]:
                detail = f"{r['err']:<22} {r['ms']:>5}ms"
            print(f"  {flag} {r['label']:<22} {detail}  {r['note']}")
        if i < args.rounds:
            time.sleep(args.interval)

    print(f"\n{'='*72}\n汇总\n{'='*72}")
    print(f"  {'接口':<24}{'成功率':>10}   失败分布")
    for lbl, *_ in _TARGETS:
        st = stats[lbl]
        rate = f"{st['ok']}/{st['total']}"
        print(f"  {lbl:<24}{rate:>10}   {st['errs'] or '—'}")

    if args.burst:
        _burst(args.burst)

    print(f"\n{'='*72}")
    print("怎么读这份结果：")
    print("  · 对照组(腾讯/新浪)全绿、东财全红 ⇒ 针对本机IP的东财限流，坐实")
    print("  · 对照组也红 ⇒ 是网络/DNS 问题，不是东财针对性限流")
    print("  · push2his/push2 红、但 push2ex/datacenter/选股 绿 ⇒ 只有 push2 这一系被限")
    print("  · 前几轮绿、后面转红 ⇒ 按累计请求量触发的滑动窗口限流")
    print("  · 串行全绿但 --burst 红 ⇒ 按并发触发，降并发即可缓解")
    print(f"{'='*72}")


if __name__ == "__main__":
    main()
