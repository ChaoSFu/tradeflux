"""
板块 K 线取数的对照诊断。**只读，不写库。**

背景：2026-09-03 服务器上单次 fetch_sector_kline('BK0832', days=300) 成功拿到 300 根，
一分钟后回填脚本连续 5 只全部 RemoteProtocolError。同一个函数、同一台机器、
相差一分钟——所以"被限流了"这个结论下得太快，先做对照再说。

三个互斥假设，这个脚本分别给出证据：
  H1 连接复用   _thread_warmed_client 复用了被服务端关掉的 keep-alive
  H2 请求过大   lmt=300 的 payload 触发了更严格的配额（我们其实只要 70 根）
  H3 真限流     跟 client 和大小都无关，短时间内几次就封

每组之间留足间隔，避免自己把自己打死。全程只发 8 个请求。
"""
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.eastmoney_fetcher import KLINE_URL, HEADERS, _thread_warmed_client

CODES = ["BK0832", "BK0447", "BK0433"]
GAP = 4.0          # 组内间隔
COOL = 25.0        # 组间冷却


def _params(code, lmt):
    return {"secid": f"90.{code}", "klt": 101, "fqt": 1, "lmt": lmt, "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5", "fields2": "f51,f52,f53,f54,f55,f56,f57"}


def _try(getter, code, lmt):
    try:
        body = getter(KLINE_URL, params=_params(code, lmt)).json()
        n = len((body.get("data") or {}).get("klines") or [])
        return f"{n} 根" if n else "空"
    except Exception as e:  # noqa: BLE001
        return f"失败 {type(e).__name__}"


def main():
    print(f"每组间隔 {GAP}s，组间冷却 {COOL}s，共 8 个请求\n")

    print("── 组1：每次新建 client，lmt=70（我们真正需要的量）──")
    for i, c in enumerate(CODES):
        if i:
            time.sleep(GAP)
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20) as cl:
            print(f"  {c}: {_try(cl.get, c, 70)}")

    print(f"\n冷却 {COOL}s…")
    time.sleep(COOL)

    print("── 组2：每次新建 client，lmt=300（回填脚本当前的量）──")
    for i, c in enumerate(CODES):
        if i:
            time.sleep(GAP)
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20) as cl:
            print(f"  {c}: {_try(cl.get, c, 300)}")

    print(f"\n冷却 {COOL}s…")
    time.sleep(COOL)

    print("── 组3：复用 warmed client，lmt=70（现行实现的写法）──")
    cl = _thread_warmed_client(timeout=20)
    for i, c in enumerate(CODES[:2]):
        if i:
            time.sleep(GAP)
        print(f"  {c}: {_try(cl.get, c, 70)}")

    print("""
读法：
  组1 全成功、组2 全失败      → H2 请求过大，把 days 降到 90 即可
  组1/组2 成功、组3 失败      → H1 连接复用，改成每次新建 client
  三组都失败                  → H3 真限流，或本机已在封锁期内，隔久点再测
  组1 前几个成功后面失败      → 有短时配额，要靠更大的 delay 而不是改写法
""")


if __name__ == "__main__":
    main()
