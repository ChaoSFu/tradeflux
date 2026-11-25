"""
外部接口调用计数（2026-11-25）：一趟日更 / 一次同步里，每个外部主机被打了几次。

用户的原则是「一个接口不要过分调用」——先得看得见。装在进程级（httpx.Client.send），
所有走 httpx 的请求（httpx.get / post 底下也是 Client.send）都按主机记一笔，日更结尾打印。
只在日更、大市值同步这种独立子进程里装；网站进程不装（不想给每个请求加开销）。
"""
from __future__ import annotations

import threading
from collections import Counter
from typing import List

_counts: Counter = Counter()
_lock = threading.Lock()
_installed = False

HOST_LABEL = {
    "np-tjxg-g.eastmoney.com": "东财选股 prompt",
    "push2.eastmoney.com": "东财 push2",
    "push2delay.eastmoney.com": "东财 push2delay",
    "push2his.eastmoney.com": "东财 push2his（K 线 / 分时）",
    "push2ex.eastmoney.com": "东财涨跌停池",
    "datacenter.eastmoney.com": "东财数据中心",
    "emweb.securities.eastmoney.com": "东财 F10",
    "qt.gtimg.cn": "腾讯行情",
    "web.ifzq.gtimg.cn": "腾讯 K 线 / 分钟",
    "hq.sinajs.cn": "新浪行情",
    "quotes.sina.cn": "新浪 K 线",
    "money.finance.sina.com.cn": "新浪 K 线",
}


def install() -> None:
    """幂等。之后本进程所有 httpx 同步请求都按主机计数。"""
    global _installed
    if _installed:
        return
    import httpx
    orig = httpx.Client.send

    def send(self, request, *args, **kwargs):
        try:
            host = request.url.host
        except Exception:  # noqa: BLE001
            host = "?"
        with _lock:
            _counts[host] += 1
        return orig(self, request, *args, **kwargs)

    httpx.Client.send = send
    _installed = True


def snapshot() -> dict:
    with _lock:
        return dict(_counts)


def reset() -> None:
    with _lock:
        _counts.clear()


def lines(prefix: str = "  ") -> List[str]:
    """「东财选股 prompt 12 次」这样一行一个主机，按次数从多到少。"""
    snap = snapshot()
    if not snap:
        return [f"{prefix}外部接口调用：0 次"]
    total = sum(snap.values())
    out = [f"{prefix}外部接口调用：共 {total} 次"]
    for host, n in sorted(snap.items(), key=lambda kv: -kv[1]):
        out.append(f"{prefix}  {HOST_LABEL.get(host, host)}（{host}）{n} 次")
    return out
