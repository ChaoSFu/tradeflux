"""
sector_top_stocks_service.py
============================
通过东方财富 clist API 获取板块内主板（非科创/非创业/非北交所）非ST个股，
按指定周期涨幅降序排列，返回前 N 名。

接口特征：
  - 沪市主板：fs=b:{code}+m:1+t:2
  - 深市主板：fs=b:{code}+m:0+t:6
  - 名称含"ST"的个股在客户端过滤

涨幅字段：
  f3   今日涨幅 %
  f109 近5日涨幅 %
  f110 近10日涨幅 %
  f160 近20日涨幅 %
  f165 近60日涨幅 %
"""

import json
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from typing import List, Optional

_BASE = "https://push2delay.eastmoney.com/api/qt/clist/get"
_HEADERS = [
    "-H", "Referer: https://data.eastmoney.com/",
    "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# 周期 → 排序字段
_PERIOD_FIELD = {
    "5d":  "f109",
    "10d": "f110",
    "20d": "f160",
    "60d": "f165",
}


@dataclass
class TopStock:
    code: str
    name: str
    pct_today: Optional[float]
    pct_5d: Optional[float]
    pct_10d: Optional[float]
    pct_20d: Optional[float]
    pct_60d: Optional[float]


def _curl(url: str) -> dict:
    """用 curl subprocess 发起请求，返回 JSON dict（失败返回空 dict）。"""
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "12"] + _HEADERS + [url],
            capture_output=True, text=True, timeout=15,
        )
        if result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def _fetch_market(bk_code: str, market_fs: str, fid: str, pz: int = 50) -> List[dict]:
    """
    拉取指定市场的板块内个股，按 fid 降序排列。
    返回 raw diff 列表（最多 pz 条）。
    """
    params = {
        "pn": "1", "pz": str(pz), "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fid": fid,
        "fs": f"b:{bk_code}+{market_fs}",
        "fields": "f12,f14,f3,f109,f110,f160,f165",
    }
    url = f"{_BASE}?{urllib.parse.urlencode(params)}"
    data = _curl(url)
    return (data.get("data") or {}).get("diff") or []


def _to_float(v) -> Optional[float]:
    """东财 API 有时返回 '-' 或 None，统一转为 None。"""
    if v is None or v == "-" or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_top_stocks(
    bk_code: str,
    period: str = "20d",
    limit: int = 10,
) -> List[TopStock]:
    """
    返回板块 bk_code 内主板非ST个股，按 period 涨幅降序取前 limit 名。
    period: "5d" | "10d" | "20d" | "60d"
    """
    fid = _PERIOD_FIELD.get(period, "f160")
    fetch_size = max(limit * 3, 30)  # 多取一些，过滤ST后仍能凑够 limit 条

    # ── 拉取沪深主板 ──────────────────────────────────────────────────────────
    sh_items = _fetch_market(bk_code, "m:1+t:2", fid, fetch_size)
    time.sleep(0.2)  # 防频控
    sz_items = _fetch_market(bk_code, "m:0+t:6", fid, fetch_size)

    # ── 合并、过滤ST、去重 ────────────────────────────────────────────────────
    seen: set[str] = set()
    merged: List[dict] = []
    for item in sh_items + sz_items:
        code = str(item.get("f12", ""))
        name = str(item.get("f14", ""))
        if not code or code in seen:
            continue
        if "ST" in name:          # 过滤 ST / *ST
            continue
        seen.add(code)
        merged.append(item)

    # ── 按 fid 对应涨幅降序排序 ───────────────────────────────────────────────
    field_map = {"f109": "pct_5d", "f110": "pct_10d", "f160": "pct_20d", "f165": "pct_60d"}
    raw_field = fid  # e.g. "f160"

    def sort_val(item: dict) -> float:
        v = _to_float(item.get(raw_field))
        return v if v is not None else -9999.0

    merged.sort(key=sort_val, reverse=True)

    # ── 转为 dataclass，取前 limit 条 ─────────────────────────────────────────
    result: List[TopStock] = []
    for item in merged[:limit]:
        result.append(TopStock(
            code=str(item.get("f12", "")),
            name=str(item.get("f14", "")),
            pct_today=_to_float(item.get("f3")),
            pct_5d=_to_float(item.get("f109")),
            pct_10d=_to_float(item.get("f110")),
            pct_20d=_to_float(item.get("f160")),
            pct_60d=_to_float(item.get("f165")),
        ))
    return result
