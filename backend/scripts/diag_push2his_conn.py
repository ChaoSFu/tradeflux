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

## 这个脚本发几次请求

A 组 4 次：每次新建 Client（不复用连接），间隔 2s
B 组 4 次：复用同一个 Client，间隔 2s
C 组 1 次：复用 B 组的 Client，先空闲 40s

共 9 次，约 1.5 分钟。故意做得很小——诊断本身不该把 IP 打死。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.services.eastmoney_fetcher import HEADERS, KLINE_URL

CODES = ["BK0662", "BK0963", "BK0728", "BK0700"]


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

    print(f"\n结论线索：A(新连接) {a_ok}/4 成功，B(复用) {b_ok}/4 成功，"
          f"C(复用+空闲40s) {'成功' if c_res.startswith('OK') else '失败'}")
    if a_ok >= 3 and b_ok <= 1:
        print("→ 指向**复用连接失效**：退避不但没用，还在制造失败。改成每次新连接。")
    elif a_ok == 0 and b_ok == 0:
        print("→ 指向**真被限流/拦截**：换连接也没用，退避和冷却是对的。")
    else:
        print("→ 两者都不干净，看上面每行的具体错误再判断。")


if __name__ == "__main__":
    main()
