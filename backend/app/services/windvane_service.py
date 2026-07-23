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
from datetime import date as date_cls, datetime
from typing import List, Optional

import httpx
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .eastmoney_fetcher import HEADERS
from ..models.market_index import MarketBreadthDaily

DATACENTER_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
UPDOWN_URL = "https://quotederivates.eastmoney.com/datacenter/updowndistribution"
TRENDS2_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"

# 盘中分钟数据/外推结果缓存（避免每次请求都打外部接口）
_TRENDS_TTL = 60
_trends_cache: dict = {"ts": None, "data": None}

# 沪(上证A指) / 深(深证A指) / 京(北证50 代理全市场)
UPDOWN_MARKETS = [("1", "000002"), ("0", "399002"), ("0", "899050")]

_SYNC_DEBOUNCE = 600     # 库空自愈同步防抖（秒）
_last_sync_attempt: dict = {"ts": None}
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
    amount_hsj: Optional[float] = None  # 含北交所


class TurnoverData(BaseModel):
    today: float              # 最新一日成交额（元，沪深口径，收盘入库值）
    prev: float               # 前一日
    avg60: float              # 60日均值
    series: List[TurnoverPoint]
    # 今日盘中实时（仅当今日为交易日、且收盘数据尚未入库时有值）
    intraday_date: Optional[str] = None    # 今日日期
    intraday_amount: Optional[float] = None  # 当前实时两市成交额（元）
    intraday_estimate: Optional[float] = None  # 预估全天成交额（元，按已过交易时间外推）
    is_trading: bool = False               # 当前是否处于交易时段（盘中）


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


def _fetch_trade_date(client: httpx.Client) -> Optional[dict]:
    """交易日历：{'today': 'YYYY-MM-DD', 'is_open': bool, 'last': 'YYYY-MM-DD'}；失败返回 None"""
    try:
        payload = _get_json(client, DATACENTER_URL, {
            "reportName": "RPT_DMSK_WINDVANE_DATE",
            "columns": "TODAY_DATE,IS_OPEN,LAST_DATE,NEXT_DATE",
            "source": "securities",
            "client": "APP",
        })
        rows = (payload.get("result") or {}).get("data") or []
        if not rows:
            return None
        r = rows[0]
        return {
            "today": str(r["TODAY_DATE"])[:10],
            "is_open": bool(r.get("IS_OPEN")),
            "last": str(r["LAST_DATE"])[:10],
        }
    except Exception:
        return None


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
        TurnoverPoint(
            date=str(r["TRADE_DATE"])[:10],
            amount=float(r["DEAL_AMOUNT"] or 0),
            amount_hsj=float(r["DEAL_AMOUNT_HSJ"] or 0),
        )
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


# ── 数据同步（daily_update 调用；refresh=true 或库空时自愈调用）──────────────

def sync_market_breadth(db: Session) -> dict:
    """
    拉取两融/涨跌统计/成交额并 upsert 入 market_breadth_daily（一天一行，按来源填列）。
    返回 {'ok': 成功模块数, 'errors': [...]}
    """
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

    try:
        # 预取涉及日期的行，避免逐行查询
        all_dates: set = set()
        if margin:
            all_dates |= {p.date for p in margin.series}
        if turnover:
            all_dates |= {p.date for p in turnover.series}
        rows: dict = {}
        if all_dates:
            date_objs = [date_cls.fromisoformat(d) for d in all_dates]
            for r in db.query(MarketBreadthDaily).filter(MarketBreadthDaily.date.in_(date_objs)).all():
                rows[str(r.date)] = r

        def row_for(d: str) -> MarketBreadthDaily:
            r = rows.get(d)
            if r is None:
                # 预取范围外的日期（如 updown 绑定的今天）：先查库避免撞唯一约束
                r = (
                    db.query(MarketBreadthDaily)
                    .filter(MarketBreadthDaily.date == date_cls.fromisoformat(d))
                    .first()
                )
                if r is None:
                    r = MarketBreadthDaily(date=date_cls.fromisoformat(d))
                    db.add(r)
                rows[d] = r
            return r

        if margin:
            for p in margin.series:
                r = row_for(p.date)
                r.margin_balance = p.balance
                r.margin_net_buy = p.net_buy
                r.szzs_close = p.szzs_close
        if turnover:
            for p in turnover.series:
                r = row_for(p.date)
                r.deal_amount = p.amount
                r.deal_amount_hsj = p.amount_hsj
        if updown:
            # 涨跌统计接口返回的是「当前实时」数据、无自带日期：
            #   交易日 → 绑定到今天（盘中多次同步渐进覆盖，15:30 收盘后运行定格为收盘口径）
            #   非交易日 → 绑定到上一交易日（此时接口数据即上一收盘的最终值）
            with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=10) as _c:
                cal = _fetch_trade_date(_c)
            if cal:
                bind_date = cal["today"] if cal["is_open"] else cal["last"]
            else:
                bind_date = (
                    turnover.series[-1].date if turnover and turnover.series
                    else (margin.latest_date if margin else str(date_cls.today()))
                )
            r = row_for(bind_date)
            r.up_count = updown.up
            r.down_count = updown.down
            r.flat_count = updown.flat
            r.limit_up_count = updown.limit_up
            r.limit_down_count = updown.limit_down
            r.natural_limit_up = updown.natural_limit_up
            r.natural_limit_down = updown.natural_limit_down
            r.up_buckets = updown.up_buckets
            r.down_buckets = updown.down_buckets
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        errors.append(f"入库失败: {e}")

    _last_sync_attempt["ts"] = datetime.now()
    return {"ok": sum(x is not None for x in (margin, updown, turnover)), "errors": errors}


# ── 读取（DB 优先）────────────────────────────────────────────────────────────

def _read_windvane_from_db(db: Session) -> WindvaneResponse:
    errors: List[str] = []

    # 两融序列（近130个交易日）
    m_rows = (
        db.query(MarketBreadthDaily)
        .filter(MarketBreadthDaily.margin_balance.isnot(None))
        .order_by(MarketBreadthDaily.date.desc())
        .limit(130)
        .all()
    )
    m_rows.reverse()
    margin = None
    if m_rows:
        series = [
            MarginPoint(date=str(r.date), balance=r.margin_balance or 0,
                        net_buy=r.margin_net_buy or 0, szzs_close=r.szzs_close or 0)
            for r in m_rows
        ]
        margin = MarginData(latest_date=series[-1].date, balance=series[-1].balance,
                            net_buy=series[-1].net_buy, series=series)
    else:
        errors.append("融资融券: 库内暂无数据")

    # 成交额序列（近60个交易日）
    t_rows = (
        db.query(MarketBreadthDaily)
        .filter(MarketBreadthDaily.deal_amount.isnot(None))
        .order_by(MarketBreadthDaily.date.desc())
        .limit(60)
        .all()
    )
    t_rows.reverse()
    turnover = None
    if t_rows:
        t_series = [
            TurnoverPoint(date=str(r.date), amount=r.deal_amount or 0, amount_hsj=r.deal_amount_hsj)
            for r in t_rows
        ]
        turnover = TurnoverData(
            today=t_series[-1].amount,
            prev=t_series[-2].amount if len(t_series) > 1 else 0.0,
            avg60=sum(p.amount for p in t_series) / len(t_series),
            series=t_series,
        )
    else:
        errors.append("成交分析: 库内暂无数据")

    # 涨跌统计（最新一条有效记录）
    u_row = (
        db.query(MarketBreadthDaily)
        .filter(MarketBreadthDaily.up_count.isnot(None))
        .order_by(MarketBreadthDaily.date.desc())
        .first()
    )
    updown = None
    if u_row:
        updown = UpDownData(
            up=u_row.up_count or 0, down=u_row.down_count or 0, flat=u_row.flat_count or 0,
            limit_up=u_row.limit_up_count or 0, limit_down=u_row.limit_down_count or 0,
            natural_limit_up=u_row.natural_limit_up or 0,
            natural_limit_down=u_row.natural_limit_down or 0,
            up_buckets=u_row.up_buckets or [0] * 10,
            down_buckets=u_row.down_buckets or [0] * 10,
        )
    else:
        errors.append("涨跌统计: 库内暂无数据")

    latest_date = max(
        [str(m_rows[-1].date) if m_rows else "", str(t_rows[-1].date) if t_rows else "",
         str(u_row.date) if u_row else ""]
    )
    return WindvaneResponse(
        updated_at=latest_date or datetime.now().date().isoformat(),
        margin=margin, updown=updown, turnover=turnover, errors=errors,
    )


def _fetch_trends_projection() -> Optional[dict]:
    """
    用东财分钟数据（trends2, ndays=2）做「昨日同期」外推——与东财自身口径同源:
      今日盘中累计 = 今日各分钟成交额之和（沪市综指000001 + 深市综指399106,与SUMTVALLIST同口径）
      预估全天 = 今日累计 × 昨日全天 ÷ 昨日同一分钟数累计
    返回 {'date','amount','estimate','is_trading'} 或 None（失败/无数据）。60s 缓存。
    """
    now = datetime.now()
    if _trends_cache["data"] is not None and _trends_cache["ts"] and \
            (now - _trends_cache["ts"]).total_seconds() < _TRENDS_TTL:
        return _trends_cache["data"]

    tot_today = tot_yday_same = tot_yday_full = 0.0
    today_str: Optional[str] = None
    today_minutes = 0
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=10) as client:
            for secid in ("1.000001", "0.399106"):
                r = client.get(TRENDS2_URL, params={
                    "fields1": "f1,f2", "fields2": "f51,f57",
                    "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                    "iscr": "0", "iscca": "0", "secid": secid, "time": "0", "ndays": "2",
                })
                trends = ((r.json().get("data") or {}).get("trends")) or []
                by_date: dict[str, list[float]] = {}
                for line in trends:
                    parts = line.split(",")
                    if len(parts) < 2:
                        continue
                    d = parts[0][:10]
                    try:
                        by_date.setdefault(d, []).append(float(parts[1]))
                    except ValueError:
                        continue
                dates = sorted(by_date)
                if len(dates) < 2:
                    continue
                yday_rows, today_rows = by_date[dates[-2]], by_date[dates[-1]]
                tot_today += sum(today_rows)
                tot_yday_full += sum(yday_rows)
                tot_yday_same += sum(yday_rows[: len(today_rows)])
                today_str = dates[-1]
                today_minutes = max(today_minutes, len(today_rows))
    except Exception:
        return None

    if today_str is None or tot_today <= 0:
        return None
    # A股全天 241 根分钟线（9:30-11:30 + 13:00-15:00）;不足即仍在盘中
    trading = today_minutes < 241
    # 开盘前几分钟样本太少,外推不稳定 → 只给盘中值不给预估
    estimate: Optional[float] = None
    if not trading:
        estimate = tot_today
    elif today_minutes >= 5 and tot_yday_same > 0:
        estimate = tot_today * tot_yday_full / tot_yday_same
    data = {"date": today_str, "amount": tot_today, "estimate": estimate, "is_trading": trading}
    _trends_cache["ts"] = now
    _trends_cache["data"] = data
    return data


def _enrich_intraday(resp: WindvaneResponse) -> None:
    """
    最新交易日数据未入库时（盘中/收盘后未同步），补实时成交额 + 预估全天。
    日期以分钟数据自带日期为准——周末/节假日 trends2 最新日即上一交易日,
    已在收盘序列里,自然跳过,无需交易日历判断。
    """
    t = resp.turnover
    if t is None:
        return
    proj = _fetch_trends_projection()
    if not proj:
        return
    if t.series and t.series[-1].date >= proj["date"]:
        return  # 已入库,不覆盖
    t.intraday_date = proj["date"]
    t.intraday_amount = proj["amount"]
    t.intraday_estimate = proj["estimate"]
    t.is_trading = proj["is_trading"]


def get_windvane(db: Session, force_refresh: bool = False) -> WindvaneResponse:
    """
    市场风向标数据。读 DB（daily_update 每日同步写入）；
    refresh=true 强制重新同步；库空时自愈同步一次（10 分钟防抖）。
    成交分析额外补今日盘中实时成交额 + 预估全天。
    """
    with _lock:
        sync_errors: List[str] = []
        if force_refresh:
            sync_errors = sync_market_breadth(db).get("errors", [])
        else:
            has_any = db.query(MarketBreadthDaily.id).first() is not None
            if not has_any:
                last = _last_sync_attempt["ts"]
                if last is None or (datetime.now() - last).total_seconds() > _SYNC_DEBOUNCE:
                    sync_errors = sync_market_breadth(db).get("errors", [])

        resp = _read_windvane_from_db(db)
        try:
            _enrich_intraday(resp)
        except Exception:  # noqa: BLE001
            pass
        # 同步错误合并展示（去重）
        for e in sync_errors:
            if e not in resp.errors:
                resp.errors.append(e)
        return resp
