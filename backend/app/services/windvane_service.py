"""
市场风向标数据（复刻东财「牛熊风向标」页三个模块的数据源）：
  1. 融资融券：datacenter RPT_DMSK_WINDVANE_MARGIN
       两融余额 / 融资净买入 / 上证收盘（近半年逐交易日）
  2. 涨跌统计：quotederivates updowndistribution（沪 000002 + 深 399002 + 京 899050 三市求和）
       响应为编号字段，已对照官方页面逐项破解：
         "1"  数据时间 HHMMSS
         "2"  上涨(不含涨停) 10档分布 [0-1%, 1-2%, ..., 9-10%+]
         "3"  下跌(不含跌停) 10档分布（对称）
         "4"  平盘家数    "5" 涨停家数    "6" 跌停家数
         "7"  非自然涨停数（自然涨停 = 5 - 7）
         "8"  非自然跌停数（自然跌停 = 6 - 8）
       校验：上涨总数 = sum("2") + 涨停；下跌总数 = sum("3") + 跌停（与官方页一致）
  3. 成交分析：datacenter RPT_DMSK_WINDVANE_SUMTVALLIST（近60日两市成交额）
       + RPT_DMSK_WINDVANE_AVGDEAL（60日均值）；DEAL_AMOUNT=沪深，_HSJ=含北交所

进程内缓存 10 分钟，三块任一失败不影响其余（errors 提示）。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import List, Optional

import httpx
from pydantic import BaseModel

from .eastmoney_fetcher import HEADERS

DATACENTER_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
UPDOWN_URL = "https://quotederivates.eastmoney.com/datacenter/updowndistribution"

# 沪(上证A指) / 深(深证A指) / 京(北证50 代理全市场)
UPDOWN_MARKETS = [("1", "000002"), ("0", "399002"), ("0", "899050")]

_CACHE_TTL = 600
_cache: dict = {"ts": None, "resp": None}
_lock = threading.Lock()


# ── Schemas ───────────────────────────────────────────────────────────────────

class MarginPoint(BaseModel):
    date: str
    balance: float        # 两融余额（元）
    net_buy: float        # 融资净买入（元）
    szzs_close: float     # 上证指数收盘


class MarginData(BaseModel):
    latest_date: str
    balance: float
    net_buy: float
    series: List[MarginPoint]


class UpDownData(BaseModel):
    up: int
    down: int
    flat: int
    limit_up: int
    limit_down: int
    natural_limit_up: int
    natural_limit_down: int
    up_buckets: List[int]     # 不含涨停，[0-1%, 1-2%, ..., 9-10%+] 10档
    down_buckets: List[int]   # 不含跌停，对称


class TurnoverPoint(BaseModel):
    date: str
    amount: float             # 沪深两市成交额（元）


class TurnoverData(BaseModel):
    today: float              # 最新一日成交额（元，沪深口径）
    prev: float               # 前一日
    avg60: float              # 60日均值
    series: List[TurnoverPoint]


class WindvaneResponse(BaseModel):
    updated_at: str
    margin: Optional[MarginData] = None
    updown: Optional[UpDownData] = None
    turnover: Optional[TurnoverData] = None
    errors: List[str] = []


# ── Fetchers ─────────────────────────────────────────────────────────────────

def _get_json(client: httpx.Client, url: str, params: dict) -> dict:
    import json as _json
    resp = client.get(url, params=params)
    resp.raise_for_status()
    # quotederivates 返回 GBK 编码（errmsg 中文），严格 utf-8 解码会炸
    try:
        return resp.json()
    except UnicodeDecodeError:
        return _json.loads(resp.content.decode("gbk", errors="replace"))


def _fetch_margin(client: httpx.Client) -> MarginData:
    payload = _get_json(client, DATACENTER_URL, {
        "reportName": "RPT_DMSK_WINDVANE_MARGIN",
        "columns": "PUBLISH_DATE,MARGIN_BALANCE,FIN_NETBUY_AMT,SZZS_CLOSE",
        "sortTypes": "1",
        "sortColumns": "PUBLISH_DATE",
        "source": "securities",
        "client": "APP",
    })
    rows = (payload.get("result") or {}).get("data") or []
    if not rows:
        raise ValueError("两融数据为空")
    series = [
        MarginPoint(
            date=str(r["PUBLISH_DATE"])[:10],
            balance=float(r["MARGIN_BALANCE"] or 0),
            net_buy=float(r["FIN_NETBUY_AMT"] or 0),
            szzs_close=float(r["SZZS_CLOSE"] or 0),
        )
        for r in rows
    ]
    last = series[-1]
    return MarginData(latest_date=last.date, balance=last.balance, net_buy=last.net_buy, series=series)


def _fetch_updown(client: httpx.Client) -> UpDownData:
    up_b = [0] * 10
    down_b = [0] * 10
    flat = lu = ld = non_nat_lu = non_nat_ld = 0
    ok = 0
    for market, code in UPDOWN_MARKETS:
        try:
            d = _get_json(client, UPDOWN_URL, {
                "version": "100", "cver": "100", "market": market, "code": code,
            })
        except Exception:
            continue  # 单市场失败（如北交所盘前）不阻塞整体
        if d.get("errid") not in (0, "0", None):
            continue
        arr2, arr3 = d.get("2") or [], d.get("3") or []
        if len(arr2) != 10 or len(arr3) != 10:
            continue
        up_b = [a + b for a, b in zip(up_b, arr2)]
        down_b = [a + b for a, b in zip(down_b, arr3)]
        flat += int(d.get("4") or 0)
        lu += int(d.get("5") or 0)
        ld += int(d.get("6") or 0)
        non_nat_lu += int(d.get("7") or 0)
        non_nat_ld += int(d.get("8") or 0)
        ok += 1
    if ok == 0:
        raise ValueError("三市涨跌分布均拉取失败")
    return UpDownData(
        up=sum(up_b) + lu, down=sum(down_b) + ld, flat=flat,
        limit_up=lu, limit_down=ld,
        natural_limit_up=max(lu - non_nat_lu, 0),
        natural_limit_down=max(ld - non_nat_ld, 0),
        up_buckets=up_b, down_buckets=down_b,
    )


def _fetch_turnover(client: httpx.Client) -> TurnoverData:
    payload = _get_json(client, DATACENTER_URL, {
        "reportName": "RPT_DMSK_WINDVANE_SUMTVALLIST",
        "columns": "TRADE_DATE,DEAL_AMOUNT,DEAL_AMOUNT_HSJ",
        "sortColumns": "TRADE_DATE",
        "sortTypes": "1",
        "source": "SECURITIES",
        "client": "APP",
    })
    rows = (payload.get("result") or {}).get("data") or []
    if not rows:
        raise ValueError("成交额数据为空")
    series = [
        TurnoverPoint(date=str(r["TRADE_DATE"])[:10], amount=float(r["DEAL_AMOUNT"] or 0))
        for r in rows
    ]
    avg60 = 0.0
    try:
        avg_payload = _get_json(client, DATACENTER_URL, {
            "reportName": "RPT_DMSK_WINDVANE_AVGDEAL",
            "columns": "AVG_DEAL_AMOUNT,AVG_DEAL_AMOUNT_HSJ",
            "source": "SECURITIES",
            "client": "APP",
        })
        avg_rows = (avg_payload.get("result") or {}).get("data") or []
        if avg_rows:
            avg60 = float(avg_rows[0].get("AVG_DEAL_AMOUNT") or 0)
    except Exception:
        avg60 = sum(p.amount for p in series) / len(series)  # 兜底：用返回序列自算
    return TurnoverData(
        today=series[-1].amount,
        prev=series[-2].amount if len(series) > 1 else 0.0,
        avg60=avg60,
        series=series,
    )


def get_windvane(force_refresh: bool = False) -> WindvaneResponse:
    with _lock:
        if (
            not force_refresh
            and _cache["resp"] is not None
            and (datetime.now() - _cache["ts"]).total_seconds() < _CACHE_TTL
        ):
            return _cache["resp"]

        margin = updown = turnover = None
        errors: List[str] = []
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            try:
                margin = _fetch_margin(client)
            except Exception as e:  # noqa: BLE001
                errors.append(f"融资融券: {e}")
            try:
                updown = _fetch_updown(client)
            except Exception as e:  # noqa: BLE001
                errors.append(f"涨跌统计: {e}")
            try:
                turnover = _fetch_turnover(client)
            except Exception as e:  # noqa: BLE001
                errors.append(f"成交分析: {e}")

        resp = WindvaneResponse(
            updated_at=datetime.now().isoformat(timespec="seconds"),
            margin=margin, updown=updown, turnover=turnover, errors=errors,
        )
        if margin or updown or turnover:
            _cache["ts"] = datetime.now()
            _cache["resp"] = resp
        return resp
