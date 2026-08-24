"""
市场风向标数据（复刻东财「牛熊风向标」页三个模块的数据源）：
  1. 融资融券：datacenter RPTA_RZRQ_LSHJ（两融历史汇总，可翻页回溯至2010-03-31，
       字段与旧用的 RPT_DMSK_WINDVANE_MARGIN 逐日核对完全一致：RZRQYE≡两融余额、
       RZJME≡融资净买入，两者是同一份底层数据，新接口只是不限制翻页深度）
       + 中证指数官网 csindex.com.cn（上证指数收盘+滚动市盈率，逐日核对与新浪一致）。
       历史数据一次性回填（backfill_margin_history.py），日常同步只取最新几天，
       避免不必要地依赖外部接口。
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
import time
from datetime import date as date_cls, datetime, timedelta
from typing import List, Optional

import httpx
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .eastmoney_fetcher import HEADERS, SINA_HEADERS, _warmed_client
from ..models.market_index import MarketBreadthDaily

DATACENTER_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
UPDOWN_URL = "https://quotederivates.eastmoney.com/datacenter/updowndistribution"
TRENDS2_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
MARGIN_HISTORY_REPORT = "RPTA_RZRQ_LSHJ"
CSINDEX_PERF_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
SZZS_INDEX_CODE = "000001"  # 上证指数
KC50_INDEX_CODE = "000688"  # 科创50
BZ50_INDEX_CODE = "899050"  # 北证50
# 深证成指(399001)/创业板指(399006)是深交所自己发布的原生指数，不在中证指数
# 官网(csindex.com.cn)的perf库里——实测这两个代码该接口直接返回空data，跟
# 上证/科创50/北证50不是同一套体系，要接入得先找到另一个数据源（2026-08-24
# 排查确认，未接入，不要在没找到数据源前假装能加）。

# 盘中分钟数据/外推结果缓存（避免每次请求都打外部接口）
_TRENDS_TTL = 60
_trends_cache: dict = {"ts": None, "data": None}

# 沪(上证A指) / 深(深证A指) / 京(北证50 代理全市场)
UPDOWN_MARKETS = [("1", "000002"), ("0", "399002"), ("0", "899050")]

_SYNC_DEBOUNCE = 600     # 库空自愈同步防抖（秒）
_last_sync_attempt: dict = {"ts": None}
_lock = threading.Lock()

# 融资融券图表可选时间周期：'6m' 为默认（保持现状，精确取最新130个交易日）；
# 其余按日历天数下限过滤；'all' 不设下限。周期 >1y 按周降采样（见 _downsample_weekly）。
MARGIN_RANGES = ("6m", "1y", "3y", "5y", "all")
_MARGIN_RANGE_START_DAYS = {"1y": 365, "3y": 365 * 3, "5y": 365 * 5}
_MARGIN_DOWNSAMPLE_RANGES = ("3y", "5y", "all")


# ── Schemas ───────────────────────────────────────────────────────────────────

class MarginPoint(BaseModel):
    date: str
    balance: float        # 两融余额（元）
    net_buy: float        # 融资净买入（元）
    szzs_close: float     # 上证指数收盘
    szzs_pe: Optional[float] = None  # 上证指数滚动市盈率
    kc50_pe: Optional[float] = None  # 科创50滚动市盈率（2026-08-24新增）
    bz50_pe: Optional[float] = None  # 北证50滚动市盈率（2026-08-24新增）


class MarginData(BaseModel):
    latest_date: str
    balance: float
    net_buy: float
    series: List[MarginPoint]


class UpDownData(BaseModel):
    date: str
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


def _fetch_margin_history_page(
    client: httpx.Client, page_number: int, page_size: int = 800,
) -> tuple[list[dict], int]:
    """
    两融历史一页（RPTA_RZRQ_LSHJ，sortTypes=-1 即最新在前）。
    返回 (rows, total)，rows 为 [{'date','balance','net_buy'}, ...]。
    """
    payload = _get_json(client, DATACENTER_URL, {
        "reportName": MARGIN_HISTORY_REPORT,
        "columns": "ALL",
        "source": "WEB",
        "sortColumns": "DIM_DATE",
        "sortTypes": "-1",
        "pageNumber": page_number,
        "pageSize": page_size,
    })
    result = payload.get("result") or {}
    raw = result.get("data") or []
    rows = [
        {
            "date": str(r["DIM_DATE"])[:10],
            "balance": float(r.get("RZRQYE") or 0),
            "net_buy": float(r.get("RZJME") or 0),
        }
        for r in raw
    ]
    return rows, int(result.get("count") or 0)


def fetch_margin_history_full(page_size: int = 800) -> list[dict]:
    """
    翻页拉取两融余额全部历史（回溯至2010-03-31，约3972条），仅供一次性回填脚本
    （backfill_margin_history.py）使用，日常同步不调用这个。
    分页过程中任一页失败 → 视为不可用，返回空列表（不把「部分结果」当作权威全集，
    同 fetch_strong_pool_codes 的既有约定）。返回按日期升序（最早在前）。
    """
    all_rows: list[dict] = []
    with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        page = 1
        while True:
            try:
                rows, total = _fetch_margin_history_page(client, page, page_size)
            except Exception as e:  # noqa: BLE001
                print(f"[windvane] 两融历史第{page}页拉取失败: {e}（本次视为不可用）", flush=True)
                return []
            all_rows.extend(rows)
            if not rows or len(all_rows) >= total:
                break
            page += 1
            time.sleep(0.3)
    all_rows.reverse()
    return all_rows


def fetch_index_pe_history(index_code: str, start_date: str, end_date: str) -> dict[str, dict]:
    """
    指定指数收盘价 + 滚动市盈率（中证指数官网 csindex.com.cn，跟东财完全独立的
    数据源）。start_date/end_date 形如 'YYYYMMDD'。一次请求返回整个区间。
    返回 {'YYYY-MM-DD': {'close': float|None, 'pe': float|None}}；失败返回空字典。

    2026-08-24通用化（原名 fetch_szzs_pe_history，只认死上证指数）：实测该接口
    对上证指数(000001)/科创50(000688)/北证50(899050)都能查到数据，深证成指
    (399001)/创业板指(399006)查不到（这两个是深交所自己的原生指数，不在中证
    指数官网的perf库里），调用方目前只应该传前三个代码。
    """
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            r = client.get(CSINDEX_PERF_URL, params={
                "indexCode": index_code, "startDate": start_date, "endDate": end_date,
            })
            data = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[windvane] 指数市盈率拉取失败({index_code}): {e}", flush=True)
        return {}

    out: dict[str, dict] = {}
    for row in data.get("data") or []:
        d = str(row.get("tradeDate") or "")
        if len(d) != 8:
            continue
        date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        close, pe = row.get("close"), row.get("peg")
        out[date_str] = {
            "close": float(close) if close is not None else None,
            "pe": float(pe) if pe is not None else None,
        }
    return out


def _fetch_margin(client: httpx.Client, days: int = 5) -> MarginData:
    """
    融资融券最新数据（日常同步用）。历史数据由 backfill_margin_history.py 一次性
    回填，daily_update 之后不再变化，所以这里只取最新几天，不用每次现查半年数据。
    两融余额/净买入来自 RPTA_RZRQ_LSHJ，上证/科创50/北证50收盘+市盈率来自中证
    指数官网，独立数据源都只查最新这几天。
    """
    rows, _ = _fetch_margin_history_page(client, 1, page_size=days)
    if not rows:
        raise ValueError("两融数据为空")
    rows = list(reversed(rows))  # 转成升序（最早在前）

    start = rows[0]["date"].replace("-", "")
    end = date_cls.today().strftime("%Y%m%d")
    szzs_pe_map = fetch_index_pe_history(SZZS_INDEX_CODE, start, end)
    kc50_pe_map = fetch_index_pe_history(KC50_INDEX_CODE, start, end)
    bz50_pe_map = fetch_index_pe_history(BZ50_INDEX_CODE, start, end)
    series = [
        MarginPoint(
            date=r["date"], balance=r["balance"], net_buy=r["net_buy"],
            szzs_close=(szzs_pe_map.get(r["date"], {}).get("close") or 0),
            szzs_pe=szzs_pe_map.get(r["date"], {}).get("pe"),
            kc50_pe=kc50_pe_map.get(r["date"], {}).get("pe"),
            bz50_pe=bz50_pe_map.get(r["date"], {}).get("pe"),
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
        # 占位：UpDownData.date 是给"选历史日期"读库场景用的（_read_windvane_from_db
        # 里真实赋值成 u_row.date），这里是抓取阶段的中间对象，真正落库用哪天由调用方
        # sync_market_breadth() 另外拉交易日历算出 bind_date 决定（这个对象的.date从不
        # 被读取）。此前这里漏传必填的date字段，导致 UpDownData 构造直接抛
        # pydantic ValidationError，"涨跌统计"这一步天天静默失败，MarketBreadthDaily.
        # up_count 等字段停在最后一次成功写入的日期不再前进（2026-08-24发现，回归自
        # 30a8701新增date必填字段时漏改这处调用点）。
        date="",
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

def _gap_days(last_date: Optional[date_cls], minimum: int = 5) -> int:
    """
    按库内最新日期算「需要补几天」：last_date 到今天差几天就拉几天（+3天
    buffer盖住周末/节假日），下限 minimum。天然覆盖"daily_update偶尔漏跑
    几天"的情况——固定拉最近N天的话，一旦漏跑超过N天，缺口永远补不上。
    last_date=None（库里还没有过数据）时只返回 minimum，深度回填交给专门
    的一次性回填脚本，不在这个增量同步路径里现拉几年数据。
    """
    if last_date is None:
        return minimum
    gap = (date_cls.today() - last_date).days + 3
    return max(minimum, gap)


def sync_market_breadth(db: Session) -> dict:
    """
    拉取两融/涨跌统计/成交额并 upsert 入 market_breadth_daily（一天一行，按来源填列）。
    返回 {'ok': 成功模块数, 'errors': [...]}
    """
    margin = updown = turnover = None
    errors: List[str] = []
    last_margin_date = (
        db.query(MarketBreadthDaily.date)
        .filter(MarketBreadthDaily.margin_balance.isnot(None))
        .order_by(MarketBreadthDaily.date.desc())
        .limit(1).scalar()
    )
    margin_days = _gap_days(last_margin_date)
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        try:
            margin = _fetch_margin(client, days=margin_days)
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
                if p.szzs_pe is not None:
                    r.szzs_pe = p.szzs_pe
                if p.kc50_pe is not None:
                    r.kc50_pe = p.kc50_pe
                if p.bz50_pe is not None:
                    r.bz50_pe = p.bz50_pe
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

def _downsample_weekly(rows: list) -> list:
    """每 ISO 周只保留最后一个交易日的行。rows 需按日期升序传入，返回也是升序。"""
    by_week: dict = {}
    for r in rows:
        by_week[r.date.isocalendar()[:2]] = r  # 升序遍历，同周后来的覆盖前面的 → 保留每周最后一条
    return list(by_week.values())


def _read_windvane_from_db(db: Session, margin_range: str = "6m", updown_date: Optional[str] = None) -> WindvaneResponse:
    errors: List[str] = []

    # 两融序列：'6m' 精确取最新130个交易日（保持现状）；其余按日历天数下限过滤，
    # 'all' 不设下限；周期较长时按周降采样，避免几千个原始点糊在一张小图上。
    m_query = db.query(MarketBreadthDaily).filter(MarketBreadthDaily.margin_balance.isnot(None))
    if margin_range == "6m":
        m_rows = m_query.order_by(MarketBreadthDaily.date.desc()).limit(130).all()
        m_rows.reverse()
    else:
        start_days = _MARGIN_RANGE_START_DAYS.get(margin_range)
        if start_days is not None:
            m_query = m_query.filter(MarketBreadthDaily.date >= date_cls.today() - timedelta(days=start_days))
        m_rows = m_query.order_by(MarketBreadthDaily.date.asc()).all()
        if margin_range in _MARGIN_DOWNSAMPLE_RANGES:
            m_rows = _downsample_weekly(m_rows)

    margin = None
    if m_rows:
        series = [
            MarginPoint(date=str(r.date), balance=r.margin_balance or 0,
                        net_buy=r.margin_net_buy or 0, szzs_close=r.szzs_close or 0,
                        szzs_pe=r.szzs_pe, kc50_pe=r.kc50_pe, bz50_pe=r.bz50_pe)
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

    # 涨跌统计：不传 updown_date 取最新一条；传了则查那天，查不到再回退最新
    # （涨跌统计接口本身只有「当前实时」快照、无法像两融那样一次性回填深历史，
    # 只能靠每日同步逐日累积——可选日期范围仅覆盖开始存储之后的交易日）
    u_query = db.query(MarketBreadthDaily).filter(MarketBreadthDaily.up_count.isnot(None))
    u_row = None
    if updown_date:
        try:
            target = date_cls.fromisoformat(updown_date)
            u_row = u_query.filter(MarketBreadthDaily.date == target).first()
            if u_row is None:
                errors.append(f"涨跌统计: {updown_date} 当天无数据，已显示最新")
        except ValueError:
            errors.append(f"涨跌统计: 日期格式无效 {updown_date}")
    if u_row is None:
        u_row = u_query.order_by(MarketBreadthDaily.date.desc()).first()
    updown = None
    if u_row:
        updown = UpDownData(
            date=str(u_row.date),
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


SINA_MIN_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
)


def _sum_by_date(rows: list, key: str) -> dict[str, list[float]]:
    by_date: dict[str, list[float]] = {}
    for row in rows:
        d = str(row.get("day", ""))[:10]
        try:
            by_date.setdefault(d, []).append(float(row[key]))
        except (KeyError, ValueError, TypeError):
            continue
    return by_date


def _fetch_trends_projection_sina() -> tuple[Optional[dict], Optional[str]]:
    """
    「昨日同期」外推的新浪版本——跟东财 trends2 完全独立的服务器/接口。
    新浪5分钟K线每根bar自带amount（该5分钟区间成交额，非累计），按日期分组
    求和即可还原「今日累计」「昨日同期累计」「昨日全天」，算法跟
    _fetch_trends_projection_eastmoney 完全一致，只是数据源和取样粒度不同
    （5分钟一根，而非1分钟一根）。
    """
    tot_today = tot_yday_same = tot_yday_full = 0.0
    today_str: Optional[str] = None
    today_bars = 0
    try:
        import json as _json
        with httpx.Client(headers=SINA_HEADERS, follow_redirects=True, timeout=10) as client:
            for symbol in ("sh000001", "sz399106"):
                r = client.get(SINA_MIN_KLINE_URL, params={
                    "symbol": symbol, "scale": 5, "ma": "no", "datalen": 100,
                })
                text = r.text
                start, end = text.find("("), text.rfind(")")
                if start < 0 or end <= start:
                    continue
                rows = _json.loads(text[start + 1: end])
                by_date = _sum_by_date(rows, "amount")
                dates = sorted(by_date)
                if len(dates) < 2:
                    continue
                yday_rows, today_rows = by_date[dates[-2]], by_date[dates[-1]]
                tot_today += sum(today_rows)
                tot_yday_full += sum(yday_rows)
                tot_yday_same += sum(yday_rows[: len(today_rows)])
                today_str = dates[-1]
                today_bars = max(today_bars, len(today_rows))
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"

    if today_str is None or tot_today <= 0:
        return None, None
    # 5分钟K线，A股全天 48 根（9:30-11:30 + 13:00-15:00 = 240分钟/5）；不足即仍在盘中
    trading = today_bars < 48
    estimate: Optional[float] = None
    if not trading:
        estimate = tot_today
    elif today_bars >= 1 and tot_yday_same > 0:
        estimate = tot_today + (tot_yday_full - tot_yday_same)
    return {"date": today_str, "amount": tot_today, "estimate": estimate, "is_trading": trading}, None


def _fetch_trends_projection_eastmoney() -> tuple[Optional[dict], Optional[str]]:
    """
    用东财分钟数据（trends2, ndays=2）做「昨日同期」外推——与东财自身口径同源:
      今日盘中累计 = 今日各分钟成交额之和（沪市综指000001 + 深市综指399106,与SUMTVALLIST同口径）
      预估全天 = 今日累计 × 昨日全天 ÷ 昨日同一分钟数累计
    作为新浪失败时的兜底（详见 _fetch_trends_projection）。
    """
    tot_today = tot_yday_same = tot_yday_full = 0.0
    today_str: Optional[str] = None
    today_minutes = 0
    last_err: Optional[str] = None
    for attempt in range(2):  # 预热连接后通常一次就成功；保留1次重试兜底真正的瞬时抖动
        tot_today = tot_yday_same = tot_yday_full = 0.0
        today_str = None
        today_minutes = 0
        try:
            with _warmed_client(timeout=10, tag="trends2") as client:
                for secid in ("1.000001", "0.399106"):
                    r = client.get(TRENDS2_URL, params={
                        "fields1": "f1,f2", "fields2": "f51,f57",
                        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                        "iscr": "0", "iscca": "0", "secid": secid, "time": "0", "ndays": "2",
                    })
                    print(f"[windvane:trends2] {secid} 请求完成 status={r.status_code}", flush=True)
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
            last_err = None
            break
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            print(f"[windvane:trends2] 第{attempt + 1}次尝试失败: {last_err}", flush=True)
            if attempt < 1:
                time.sleep(1.5)

    if last_err:
        return None, last_err
    if today_str is None or tot_today <= 0:
        return None, None
    # A股全天 241 根分钟线（9:30-11:30 + 13:00-15:00）;不足即仍在盘中
    trading = today_minutes < 241
    # 差额外推（与东财风向标同款公式）:预测全天 = 今日累计 + (昨日全天 − 昨日同期累计)
    # 即假设余下时段成交额与昨日相同。开盘前几分钟样本太少不给预估。
    estimate: Optional[float] = None
    if not trading:
        estimate = tot_today
    elif today_minutes >= 5 and tot_yday_same > 0:
        estimate = tot_today + (tot_yday_full - tot_yday_same)
    return {"date": today_str, "amount": tot_today, "estimate": estimate, "is_trading": trading}, None


def _fetch_trends_projection() -> tuple[Optional[dict], Optional[str]]:
    """
    盘中成交额外推，默认走新浪5分钟K线（_fetch_trends_projection_sina，跟东财
    完全独立的服务器），新浪失败或没数据时回退东财trends2分钟数据
    （_fetch_trends_projection_eastmoney，历史上被针对性限流过，详见
    index_trend_service.sync_index_bars 里同类问题的排查记录）。
    返回 (data, error)：
      - data 非空：正常拿到盘中投影，{'date','amount','estimate','is_trading'}；
      - data 为空、error 也为空：两个源都没有当日数据（非交易时段等正常情况），不算错误；
      - error 非空：两个源都请求失败，值得记录排查。
    60s 缓存；缓存命中时不重试、不返回 error。
    """
    now = datetime.now()
    if _trends_cache["data"] is not None and _trends_cache["ts"] and \
            (now - _trends_cache["ts"]).total_seconds() < _TRENDS_TTL:
        return _trends_cache["data"], None

    data, err = _fetch_trends_projection_sina()
    if data is None:
        if err:
            print(f"[windvane:trends2] 新浪分钟数据失败，回退东财: {err}", flush=True)
        data, err = _fetch_trends_projection_eastmoney()

    if err:
        return None, err
    if not data:
        return None, None
    _trends_cache["ts"] = now
    _trends_cache["data"] = data
    return data, None


def _enrich_intraday(resp: WindvaneResponse, db: Optional[Session] = None) -> Optional[str]:
    """
    最新交易日数据未入库时（盘中/收盘后未同步），补实时成交额 + 预估全天。
    日期以分钟数据自带日期为准——周末/节假日 trends2 最新日即上一交易日,
    已在收盘序列里,自然跳过,无需交易日历判断。
    盘中值+预测同时落库到 market_breadth_daily 当日行（收盘后官方值由每日
    数据更新覆盖 deal_amount;predicted_amount 留存供「预测 vs 实际」复盘）。

    返回值：非空字符串表示这次盘中补数抓取失败的原因（调用方决定是否记录/
    展示）；成功、或当前确实没有可补的数据（非交易时段、官方数据已覆盖今天）
    时返回 None。
    """
    t = resp.turnover
    if t is None:
        return None
    proj, err = _fetch_trends_projection()
    if err:
        return err
    if not proj:
        return None
    if t.series and t.series[-1].date >= proj["date"]:
        return None  # 已入库,不覆盖
    t.intraday_date = proj["date"]
    t.intraday_amount = proj["amount"]
    t.intraday_estimate = proj["estimate"]
    t.is_trading = proj["is_trading"]
    # 落库当日盘中快照（与每日数据更新共用同一张表,幂等 upsert）
    if db is not None:
        try:
            d = date_cls.fromisoformat(proj["date"])
            row = db.query(MarketBreadthDaily).filter(MarketBreadthDaily.date == d).first()
            if row is None:
                row = MarketBreadthDaily(date=d)
                db.add(row)
            row.intraday_amount = proj["amount"]
            if proj["estimate"] is not None:
                row.predicted_amount = proj["estimate"]
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            return f"{type(e).__name__}: {e}"
    return None


class UpDownSeriesPoint(BaseModel):
    date: str
    up: int
    down: int


def get_updown_series(db: Session, days: int = 120) -> list[UpDownSeriesPoint]:
    """
    涨跌家数时间序列（最近 days 个有数据的交易日，升序），供主图叠加使用。
    跟 get_updown_dates 同一份数据，这里额外带上 up/down 数值。
    """
    rows = (
        db.query(MarketBreadthDaily)
        .filter(MarketBreadthDaily.up_count.isnot(None))
        .order_by(MarketBreadthDaily.date.desc())
        .limit(days)
        .all()
    )
    rows.reverse()
    return [
        UpDownSeriesPoint(date=str(r.date), up=r.up_count or 0, down=r.down_count or 0)
        for r in rows
    ]


def get_updown_dates(db: Session) -> list[str]:
    """
    涨跌统计可选历史日期列表（升序），供前端下拉选择——同市场效应页
    fetchMarketEffectHistory 的约定。涨跌统计接口本身只有「当前实时」快照、
    没有历史查询能力，只能从"开始每日同步"那天起逐日累积，不是固定周期。
    """
    rows = (
        db.query(MarketBreadthDaily.date)
        .filter(MarketBreadthDaily.up_count.isnot(None))
        .order_by(MarketBreadthDaily.date.asc())
        .all()
    )
    return [str(r[0]) for r in rows]


def get_windvane(
    db: Session, force_refresh: bool = False, margin_range: str = "6m",
    updown_date: Optional[str] = None,
) -> WindvaneResponse:
    """
    市场风向标数据。读 DB（daily_update 每日同步写入）；
    refresh=true 强制重新同步；库空时自愈同步一次（10 分钟防抖）。
    margin_range 控制融资融券图表时间周期，见 MARGIN_RANGES。
    updown_date 指定涨跌统计查看哪一天（不传=最新，历史日期只读库不触发同步）。
    成交分析额外补今日盘中实时成交额 + 预估全天。
    """
    if margin_range not in MARGIN_RANGES:
        margin_range = "6m"
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

        resp = _read_windvane_from_db(db, margin_range=margin_range, updown_date=updown_date)
        try:
            intraday_err = _enrich_intraday(resp, db)
        except Exception as e:  # noqa: BLE001
            intraday_err = f"{type(e).__name__}: {e}"
        if intraday_err:
            print(f"[windvane] 盘中成交额补数失败: {intraday_err}", flush=True)
            sync_errors.append(f"成交分析(盘中实时): {intraday_err}")
        # 同步错误合并展示（去重）
        for e in sync_errors:
            if e not in resp.errors:
                resp.errors.append(e)
        return resp
