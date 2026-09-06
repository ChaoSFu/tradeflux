"""
分辨 push2his 的失败到底是「被限流」还是「复用连接失效」。

## 为什么必须分开

板块回填实测全是 `RemoteProtocolError: Server disconnected without sending a
response`。这句话是 httpx 在**服务端关掉了一个复用中的 keep-alive 连接**时的原话，
跟"被拦"是两回事：

  被限流       换新连接也一样失败，且往往带 HTTP 403/429 或 HTML 拦截页
  连接失效     换新连接就好；复用 + 空闲越久越容易触发

两者的处置**完全相反**：前者要退避、要冷却；后者要的恰恰是"别等，换条连接重来"。
更糟的是我们的退避会把空闲间隔从 2s 拉到 51s，如果是后者，退避本身在制造失败
——一个自我强化的循环。

## 三种可能，处置完全不同

    限流          降速/退避能解决
    keep-alive    换 `Connection: close` + 不复用连接就能解决
    子域名封禁    以上都没用，得换出口 IP 或换数据源

## 这个脚本发几次请求

A 组 4 次：每次新建 Client（不复用连接），间隔 2s
B 组 4 次：复用同一个 Client，间隔 2s
C 组 1 次：复用 B 组的 Client，先空闲 40s
D 组 4 次：新建 Client + `Connection: close` + 显式禁用 keep-alive，间隔 2s
E 组 1 次：对照——同一时刻打 push2delay（今天实测正常的兄弟域名）

共 14 次，约 2 分钟。故意做得很小——诊断本身不该把 IP 打死。

## 已经排除的两条

**不是"请求太快"**：板块回填是单线程顺序、基准 2 秒 + 抖动 + 指数退避，而且
生产日志显示**第一个板块就失败**，不是打了十几次之后才被限。

**不是"缺伪装头"**：User-Agent 和 Referer 一直在发（见 eastmoney_fetcher.HEADERS）。
唯一没试过的是 `Connection: close`，就是 D 组。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.services.eastmoney_fetcher import HEADERS, KLINE_URL

CODES = ["BK0662", "BK0963", "BK0728", "BK0700"]
# 兄弟域名对照：今天实测 push2delay / push2ex / datacenter / quote 全部正常，
# 只有 push2his 一个子域名对我们关门。同一时刻再验一次，排除"整条网络出问题"
SIBLING_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
NO_KEEPALIVE = {**HEADERS, "Connection": "close"}


def _params(code, days=300):
    from datetime import date
    return {"secid": f"90.{code}", "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "lmt": days, "klt": 101, "fqt": 1,
            "end": date.today().strftime("%Y%m%d")}


def _try(client, code):
    """返回一行人能看懂的结论。"""
    try:
        r = client.get(KLINE_URL, params=_params(code))
    except Exception as e:  # noqa: BLE001
        return f"连接层失败 {type(e).__name__}: {str(e)[:70]}"
    body = r.content or b""
    if r.status_code != 200:
        return f"HTTP {r.status_code}，body {len(body)} 字节：{r.text[:60]!r}"
    try:
        payload = r.json()
    except ValueError:
        return f"HTTP 200 但非 JSON，body {len(body)} 字节：{r.text[:60]!r}"
    klines = ((payload.get("data") or {}).get("klines") or [])
    return f"OK，{len(klines)} 根" if klines else "HTTP 200 合法 JSON 但 data.klines 为空"


def main():
    print("A 组：每次新建 Client（不复用连接），间隔 2s")
    a_ok = 0
    for i, code in enumerate(CODES):
        if i:
            time.sleep(2)
        with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as c:
            r = _try(c, code)
        a_ok += r.startswith("OK")
        print(f"  {code}: {r}")

    print("\nB 组：复用同一个 Client，间隔 2s")
    b_ok = 0
    client = httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True)
    for i, code in enumerate(CODES):
        if i:
            time.sleep(2)
        r = _try(client, code)
        b_ok += r.startswith("OK")
        print(f"  {code}: {r}")

    print("\nC 组：复用同一个 Client，先空闲 40s")
    time.sleep(40)
    c_res = _try(client, CODES[0])
    print(f"  {CODES[0]}: {c_res}")
    client.close()

    # D 组：Connection: close + 显式关掉 keep-alive。**这是唯一还没试过的一招**
    print("\nD 组：Connection: close + 不复用连接（唯一没试过的），间隔 2s")
    d_ok = 0
    limits = httpx.Limits(max_keepalive_connections=0, max_connections=2)
    for i, code in enumerate(CODES):
        if i:
            time.sleep(2)
        with httpx.Client(headers=NO_KEEPALIVE, timeout=15,
                          follow_redirects=True, limits=limits) as c:
            r = _try(c, code)
        d_ok += r.startswith("OK")
        print(f"  {code}: {r}")

    print("\nE 组：对照——同一时刻打 push2delay（兄弟域名）")
    try:
        with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as c:
            rr = c.get(SIBLING_URL, params={
                "pn": 1, "pz": 5, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f3", "fs": "m:90+t:2", "fields": "f12,f14"})
        e_res = f"HTTP {rr.status_code}，{len(rr.content)} 字节"
        e_ok = rr.status_code == 200
    except Exception as ex:  # noqa: BLE001
        e_res, e_ok = f"{type(ex).__name__}: {str(ex)[:60]}", False
    print(f"  push2delay: {e_res}")

    print(f"\n结论线索：A(新连接) {a_ok}/4，B(复用) {b_ok}/4，"
          f"C(复用+空闲40s) {'成功' if c_res.startswith('OK') else '失败'}，"
          f"D(Connection:close) {d_ok}/4，E(兄弟域名) {'正常' if e_ok else '也挂'}")
    if not e_ok:
        print("→ 兄弟域名也挂，**先查网络/出网本身**，别急着归因到东财风控。")
    elif d_ok >= 3:
        print("→ **keep-alive 死连接**：Connection: close + 不复用就能通。"
              "把板块取数这条路改成每次新连接即可，退避反而是反效果。")
    elif a_ok >= 3 and b_ok <= 1:
        print("→ 指向**复用连接失效**：改成每次新连接。")
    elif a_ok == 0 and b_ok == 0 and d_ok == 0:
        print("→ 四种打法全挂而兄弟域名正常 → **push2his 这个子域名对本 IP 关门**。"
              "降速和换头都没用，只能换出口 IP，或者给板块 K 线找第二个数据源。")
    else:
        print("→ 结果不干净，看上面每行的具体错误再判断，别急着下结论。")


if __name__ == "__main__":
    main()
