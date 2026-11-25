"""
东财智能选股（prompt）接口的唯一出口（2026-11-25）。

用户定的取数原则：**用好 prompt 接口，尽量少拿实时接口取数，一个接口不要过分调用**。起因是东财 push2 的
全市场行情列表（clist，58 页翻一遍、一天四五次重试）被限流，生产 IP 从 11-19 起每次第 1 页就 502。

这里管三件事，所有选股请求都必须走 `search()`：
  · **跨进程节流**：日更子进程、网站进程、大市值同步子进程共用一个文件锁，任意两次请求至少隔
    MIN_INTERVAL_S 秒——并发提交也会排队，不会同一秒打出去好几个。
  · **统一的请求体和解析**：列名里带日期（`OPEN_PRICE<140>{2026-11-25}`、
    `INTERVAL_CHG{2026-10-29|2026-11-25|20|天}`），`data_date()` 拿它核对「数据属于哪天」。
  · **按代码问**：`fetch_codes()` 一次最多 CODES_PER_QUERY 只，取当天开高低收量额 + 区间涨幅。

实测（2026-11-25）：
  · 按代码问 100 只一次返回，停牌 / 退市的不在结果里；不带「行业」时不会附带概念长串（每行约 1.1 KB）。
  · 区间涨幅 = 今天收盘 ÷ N 个交易日前的收盘 − 1（前复权），跟腾讯前复权日 K 逐只对上。
  · VOLUME 单位是股；TRADING_VOLUMES / 市值是带「万」「亿」「万亿」的字符串。
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import string
import time
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import httpx

URL = "https://np-tjxg-g.eastmoney.com/api/smart-tag/stock/v3/pw/search-code"
XC_ID = "xc11bd34d6790101033c"
FINGERPRINT = "a3b5b577646954c0a1ff47146894e3d1"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://xuangu.eastmoney.com/",
    "Origin": "https://xuangu.eastmoney.com",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "actionmode": "edit_way",
    "curpage": "stockResult",
    "jumpsource": "edit_way",
}

MIN_INTERVAL_S = 1.0            # 任意两次选股请求的最小间隔（跨进程）
PAGE_SIZE = 100                 # 实测 pageSize=100 能拿满 100 行
MAX_PAGES = 30
CODES_PER_QUERY = 100
LOCK_PATH = os.environ.get("TRADEFLUX_PROMPT_LOCK", "/tmp/tradeflux_prompt_api.lock")
TIMEOUT_S = 15


class PromptError(RuntimeError):
    """选股接口这次没拿全（请求失败、分页中断）。调用方按「不可用」处理，绝不当成部分结果用。"""


# ── 节流 ────────────────────────────────────────────────────────────────────

def _throttle() -> None:
    """拿文件锁、看上一次请求的时间、不够间隔就睡够再放行。锁拿不到（只读文件系统之类）就只按本进程节流。"""
    if MIN_INTERVAL_S <= 0:
        return
    try:
        import fcntl
        with open(LOCK_PATH, "a+") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                fd.seek(0)
                raw = fd.read().strip()
                last = float(raw) if raw else 0.0
                wait = last + MIN_INTERVAL_S - time.time()
                if wait > 0:
                    time.sleep(min(wait, MIN_INTERVAL_S))
                fd.seek(0)
                fd.truncate()
                fd.write(f"{time.time():.3f}")
                fd.flush()
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    except (OSError, ValueError, ImportError):
        global _local_last
        wait = _local_last + MIN_INTERVAL_S - time.time()
        if wait > 0:
            time.sleep(wait)
        _local_last = time.time()


_local_last = 0.0


# ── 请求 ────────────────────────────────────────────────────────────────────

def _body(keyword: str, page_no: int, page_size: int, xc_id: str, fingerprint: str) -> dict:
    return {
        "needAmbiguousSuggest": True, "pageSize": page_size, "pageNo": page_no, "fingerprint": fingerprint,
        "matchWord": "", "shareToGuba": False, "timestamp": str(int(time.time() * 1_000_000)),
        "requestId": "".join(random.choices(string.ascii_letters, k=32)) + str(int(time.time() * 1000)),
        "removedConditionIdList": [], "ownSelectAll": False, "needCorrect": True, "client": "WEB",
        "product": "", "needShowStockNum": False, "biz": "web_ai_select_stocks", "xcId": xc_id,
        "gids": [], "dxInfoNew": [], "keyWordNew": keyword,
        "customDataNew": json.dumps([{"type": "text", "value": keyword, "extra": ""}], ensure_ascii=False),
    }


def search(keyword: str, *, page_no: int = 1, page_size: int = PAGE_SIZE, xc_id: str = XC_ID,
           fingerprint: str = FINGERPRINT, timeout: float = TIMEOUT_S) -> dict:
    """问一页。返回 result（{total, dataList, columns}）。失败抛 PromptError。"""
    from .eastmoney_fetcher import json_or_explain
    _throttle()
    try:
        resp = httpx.post(URL, headers=HEADERS, json=_body(keyword, page_no, page_size, xc_id, fingerprint),
                          timeout=timeout)
        resp.raise_for_status()
        data = json_or_explain(resp, "东财选股 ")
    except Exception as e:  # noqa: BLE001
        raise PromptError(f"选股接口第 {page_no} 页失败（{type(e).__name__}: {str(e)[:120]}）") from e
    return (data.get("data") or {}).get("result") or {}


def search_all(keyword: str, *, page_size: int = PAGE_SIZE, max_pages: int = MAX_PAGES, xc_id: str = XC_ID,
               fingerprint: str = FINGERPRINT) -> Tuple[List[dict], int]:
    """
    翻完所有页。返回 (条目, 接口自报总数)。**任何一页失败、或者翻到上限还没拿全，都抛 PromptError**——
    不把部分结果当全集用（缺一页就会有公司被当成「不在名单里」）。
    """
    items: List[dict] = []
    seen: set = set()
    total: Optional[int] = None
    for page_no in range(1, max_pages + 1):
        res = search(keyword, page_no=page_no, page_size=page_size, xc_id=xc_id, fingerprint=fingerprint)
        rows = res.get("dataList") or []
        if total is None:
            try:
                total = int(res.get("total"))
            except (TypeError, ValueError):
                total = None
        for it in rows:
            code = str(it.get("SECURITY_CODE") or "").strip()
            if code and code not in seen:
                seen.add(code)
                items.append(it)
        if not rows or (total is not None and len(seen) >= total):
            break
    else:
        raise PromptError(f"翻了 {max_pages} 页还没拿全（接口说 {total} 只，拿到 {len(seen)}）")
    if total is None:
        total = len(items)
    if len(items) < total:
        raise PromptError(f"分页没拿全：接口说 {total} 只，拿到 {len(items)} 只")
    return items, total


# ── 解析 ────────────────────────────────────────────────────────────────────

_DATE_IN_KEY = re.compile(r"\{([^{}]*)\}")
_INTERVAL = re.compile(r"^INTERVAL_CHG\{(\d{4}-\d{2}-\d{2})\|(\d{4}-\d{2}-\d{2})\|(\d+)\|天\}$")


def key_dates(item: dict) -> List[date]:
    """列名里带的日期：`X{2026-11-25}` 给那一天；区间 `{起|止|N|天}` 给止。"""
    out = []
    for k in item:
        for m in _DATE_IN_KEY.findall(k):
            parts = m.split("|")
            d = parts[1] if len(parts) >= 2 else parts[0]
            try:
                out.append(date.fromisoformat(d))
            except ValueError:
                continue
    return out


def data_date(items: Iterable[dict]) -> Optional[date]:
    """这批数据属于哪天：列名日期里最多的那个。一个都没有 = None（说不准）。"""
    from collections import Counter
    c = Counter(d for it in items for d in key_dates(it))
    return c.most_common(1)[0][0] if c else None


def cn_number(v) -> Optional[float]:
    """「1.84万亿」「68.42亿」「466.74万」「12.3」→ 数。"-"、空 → None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    mul = 1.0
    for suf, m in (("万亿", 1e12), ("亿", 1e8), ("万", 1e4)):
        if s.endswith(suf):
            s, mul = s[: -len(suf)], m
            break
    try:
        f = float(s) * mul
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _first(item: dict, *prefixes: str):
    for p in prefixes:
        if p in item:
            return item[p]
        for k, v in item.items():
            if k.startswith(p):
                return v
    return None


def intervals(item: dict) -> Dict[int, Optional[float]]:
    """{窗口天数: 区间涨跌幅 %}。"""
    out: Dict[int, Optional[float]] = {}
    for k, v in item.items():
        m = _INTERVAL.match(k)
        if m:
            out[int(m.group(3))] = cn_number(v)
    return out


def parse_row(item: dict) -> Optional[dict]:
    """一条选股结果 → 统一的事实字典（价格单位元，金额、市值单位元）。"""
    code = str(item.get("SECURITY_CODE") or "").strip()
    if not code:
        return None
    price = cn_number(_first(item, "NEWEST_PRICE"))
    change = cn_number(item.get("PCHG"))
    ds = key_dates(item)
    return {
        "code": code,
        "name": (str(item.get("SECURITY_SHORT_NAME") or "").replace(" ", "") or None),
        "market": (str(item.get("MARKET_SHORT_NAME") or "").strip().upper() or None),
        "price": price if price and price > 0 else None,
        "pct_change": cn_number(item.get("CHG")),
        "prev_close": round(price - change, 4) if price and change is not None and price - change > 0 else None,
        "open": cn_number(_first(item, "OPEN_PRICE")),
        "high": cn_number(_first(item, "PEAK_PRICE")),
        "low": cn_number(_first(item, "BOTTOM_PRICE")),
        "volume": cn_number(item.get("VOLUME")),                    # 股
        "amount": cn_number(item.get("TRADING_VOLUMES")),           # 元
        "turnover_rate": cn_number(item.get("TURNOVER_RATE")),
        "volume_ratio": cn_number(item.get("QRR")),
        "total_market_cap": cn_number(_first(item, "TOAL_MARKET_VALUE", "TOTAL_MARKET_VALUE")),   # 元
        "float_market_cap": cn_number(_first(item, "CIRCULATION_MARKET_VALUE")),                  # 元
        "listing_date": _iso_date(item.get("LISTING_DATE")),
        "industry": (str(item.get("INDUSTRY")).strip() or None) if item.get("INDUSTRY") else None,
        "style_concepts": _style_concepts(item.get("STYLE_CONCEPT")),
        "intervals": intervals(item),
        "data_date": max(ds) if ds else None,
    }


def _iso_date(v) -> Optional[date]:
    s = str(v or "").strip()
    for fmt_len in (10, 8):
        try:
            if fmt_len == 10 and len(s) >= 10 and s[4] == "-":
                return date.fromisoformat(s[:10])
            if fmt_len == 8 and len(s) >= 8 and s[:8].isdigit():
                return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            continue
    return None


def _style_concepts(v) -> Optional[List[str]]:
    """「【5G概念】【AH股】…」→ 名字列表。里面混着 HS300_、权重股 这类风格标签，调用方自己过滤。"""
    if not v:
        return None
    names = re.findall(r"【([^【】]+)】", str(v))
    return list(dict.fromkeys(n.strip() for n in names if n.strip())) or None


# ── 指定日期（历史补数，2026-11-25）────────────────────────────────────────────
#
# 选股接口认「2026年11月20日总市值大于300亿」「截至2026年11月20日近20个交易日涨跌幅」这样的问法，返回的列名带
# 那一天（`TOAL_MARKET_VALUE<140>{2026-11-20}`、`CHG{2026-11-20}`），**同时还带着此刻的不带日期的列**——
# 解析时只认带那一天的列。2026-11-25 拿 11-06 的东财全市场存档逐只对过（≥300 亿的 586 只）：
#   收盘价、成交额、换手率、量比全对；涨跌幅 6 只差 0.03~0.05 个百分点；区间涨幅个别差到 0.3 个百分点
#   （前复权基准随之后的分红变了）；总市值是「1.18万亿」这种三四位有效数字（万亿级 ±50 亿，千亿以下精确到 0.01 亿）；
#   名单差 4 只，都卡在 300 亿边上。名称、是否 ST 只有此刻的（历史上改过名 / 戴摘帽的会跟当天不一样）。

def date_phrase(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


def day_fields_on(d: date) -> str:
    ds = date_phrase(d)
    return (f"{ds}总市值；{ds}流通市值；{ds}收盘价；{ds}涨跌幅；{ds}成交额；{ds}换手率；{ds}量比；{ds}成交量；"
            + "；".join(f"截至{ds}近{n}个交易日涨跌幅" for n in (5, 10, 20, 60)))


def _dated(item: dict, prefix: str, d: date):
    tail = "{" + d.isoformat() + "}"
    for k, v in item.items():
        if k.startswith(prefix) and k.endswith(tail):
            return v
    return None


def parse_row_on(item: dict, d: date) -> Optional[dict]:
    """按指定日期问回来的一条 → 跟 parse_row 同样的字典，数值全取带 d 的列。d 这天的列一个都没有 → data_date=None。"""
    code = str(item.get("SECURITY_CODE") or "").strip()
    if not code:
        return None
    price = cn_number(_dated(item, "NEWEST_PRICE", d))
    pct = cn_number(_dated(item, "CHG", d))
    iv = {}
    for k, v in item.items():
        m = _INTERVAL.match(k)
        if m and m.group(2) == d.isoformat():
            iv[int(m.group(3))] = cn_number(v)
    cap = cn_number(_dated(item, "TOAL_MARKET_VALUE", d))
    name = str(item.get("SECURITY_SHORT_NAME") or "").replace(" ", "") or None
    return {
        "code": code, "name": name, "market": (str(item.get("MARKET_SHORT_NAME") or "").strip().upper() or None),
        "price": price if price and price > 0 else None, "pct_change": pct,
        "prev_close": round(price / (1 + pct / 100), 4) if price and pct is not None and pct > -100 else None,
        "open": None, "high": None, "low": None,
        "volume": cn_number(_dated(item, "VOLUME", d)), "amount": cn_number(_dated(item, "TRADING_VOLUMES", d)),
        "turnover_rate": cn_number(_dated(item, "TURNOVER_RATE", d)), "volume_ratio": cn_number(_dated(item, "QRR", d)),
        "total_market_cap": cap, "float_market_cap": cn_number(_dated(item, "CIRCULATION_MARKET_VALUE", d)),
        "listing_date": _iso_date(item.get("LISTING_DATE")),
        "industry": (str(item.get("INDUSTRY")).strip() or None) if item.get("INDUSTRY") else None,
        "style_concepts": _style_concepts(item.get("STYLE_CONCEPT")),
        "intervals": iv,
        # 这条是不是那一天的：任何一列带那一天（区间涨幅的止日也算）
        "data_date": d if d in key_dates(item) else None,
    }


# ── 按代码问 ─────────────────────────────────────────────────────────────────

DAY_FIELDS = "开盘价；最高价；最低价；收盘价；成交量；成交额；涨跌幅；量比；换手率；5日涨跌幅；10日涨跌幅；20日涨跌幅；60日涨跌幅"


def codes_keyword(codes: Sequence[str], fields: str = DAY_FIELDS) -> str:
    return "股票代码是" + "或".join(codes) + "；" + fields


def fetch_codes(codes: Iterable[str], fields: Optional[str] = None, on: Optional[date] = None) -> Dict[str, dict]:
    """
    按代码问事实，CODES_PER_QUERY 只一问。返回 {代码: parse_row 字典}。
    on=None 问此刻（fields 默认 DAY_FIELDS）；on=某天 问那一天（fields 默认 day_fields_on(on)，按 parse_row_on 解析）。
    停牌、退市、代码不存在的不在结果里——调用方按「没拿到」处理。任何一问失败抛 PromptError。
    """
    if fields is None:
        fields = day_fields_on(on) if on else DAY_FIELDS
    codes = list(dict.fromkeys(c for c in codes if c))
    out: Dict[str, dict] = {}
    for i in range(0, len(codes), CODES_PER_QUERY):
        chunk = codes[i:i + CODES_PER_QUERY]
        items, _total = search_all(codes_keyword(chunk, fields), page_size=PAGE_SIZE, max_pages=2)
        want = set(chunk)
        for it in items:
            r = parse_row_on(it, on) if on else parse_row(it)
            if r and r["code"] in want:
                out[r["code"]] = r
    return out
