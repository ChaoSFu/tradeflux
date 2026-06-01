"""
行情数据抓取层。

封装东方财富公开 API（主力）及 AkShare/新浪财经（备用），提供：
  - fetch_main_board_stocks()     获取 A 股全市场股票列表（沪主板/科创板 + 深主板/创业板）
  - fetch_kline()                 拉取单股 N 日 K 线
  - fetch_klines_batch()          并发批量拉取 K 线

涨跌停幅度：
  - 主板（600/601/603/605, 000/001/002/003）：±10%（ST ±5%）
  - 科创板（688）、创业板（300/301）：±20%
  - 炸板判断：当日最高价触及涨停价，但收盘未封板

若东方财富 clist 接口被封锁，自动切换 AkShare/新浪备用（约 30s）。
"""
import httpx
import time
import random
import string
from datetime import date
from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

# 纳入范围：主板 + 科创板(688) + 创业板(300/301)
# 排除：北交所(8xxxxx)
SH_INCLUDED_PREFIXES = ("600", "601", "603", "605", "688")
SZ_INCLUDED_PREFIXES = ("000", "001", "002", "003", "300", "301")

# 高幅涨跌停代码前缀（±20%）
_HIGH_LIMIT_PREFIXES = ("688", "300", "301")


@dataclass
class StockBasicInfo:
    """从行情接口获取的股票基础信息（每日刷新）"""
    code: str
    name: str
    market: int               # 1=SH, 0=SZ
    is_st: bool
    pct_change: float         # 今日涨跌幅 %
    turnover_rate: float      # 今日换手率 %（AkShare 备用来源可能为 0）
    listing_date: date | None = None  # 上市日期（AkShare 备用路径可获取；东方财富路径为 None）


@dataclass
class KLineBar:
    """单根 K 线（临时对象，计算后不存入 DB）"""
    date: date
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    pct_change: float
    turnover_rate: float
    is_limit_up: bool = False
    is_limit_down: bool = False
    is_broken_board: bool = False  # 炸板


def _should_include_stock(code: str, market: int) -> bool:
    """判断是否在抓取范围内（主板 + 科创板 + 创业板，排除北交所）"""
    if market == 1:  # 沪
        return code.startswith(SH_INCLUDED_PREFIXES)
    else:            # 深
        return code.startswith(SZ_INCLUDED_PREFIXES)


def get_limit_pct(code: str, is_st: bool) -> float:
    """返回该股票的涨跌停幅度阈值（含 0.1% 误差空间）。"""
    if is_st:
        return 4.95
    if code.startswith(_HIGH_LIMIT_PREFIXES):
        return 19.90  # 科创板 / 创业板 ±20%
    return 9.90       # 主板 ±10%


def _parse_kline_bar(line: str, is_st: bool = False, limit_pct: float = 9.90) -> KLineBar | None:
    """
    解析单行 K 线字符串。
    格式：日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
    limit_pct 由调用方根据股票代码传入（主板 9.90，科创/创业 19.90，ST 4.95）。
    """
    parts = line.split(",")
    if len(parts) < 11:
        return None
    try:
        dt = date.fromisoformat(parts[0])
        open_p  = float(parts[1])
        close_p = float(parts[2])
        high_p  = float(parts[3])
        low_p   = float(parts[4])
        pct     = float(parts[8])
        turnover = float(parts[10])
    except (ValueError, IndexError):
        return None

    # ── 使用实际价格判断涨跌停（比单纯 pct 阈值更准确）────────────────────────
    # 交易所规则：涨跌停价 = round(前收盘 × (1 ± board_limit/100), 2)
    # pct 由东方财富 API 返回，精确到 2 位小数，据此反推前收盘近似值。
    # actual_limit = limit_pct + 0.1：9.90→10.0, 19.90→20.0, 4.95→5.05（ST 近似足够）
    if close_p > 0 and abs(pct) < 99.9:
        prev_close = close_p / (1 + pct / 100)
        actual_limit = limit_pct + 0.1
        lu_price = round(prev_close * (1 + actual_limit / 100), 2)
        ld_price = round(prev_close * (1 - actual_limit / 100), 2)
        is_lu = close_p >= lu_price
        is_ld = close_p <= ld_price
    else:
        prev_close = 0.0
        is_lu = pct >= limit_pct
        is_ld = pct <= -limit_pct

    # 炸板判断：高价触及涨停价，但收盘未封板
    if not is_st and not is_lu and prev_close > 0:
        limit_price = prev_close * (1 + limit_pct / 100)
        is_broken = high_p >= limit_price * 0.999
    else:
        is_broken = False

    return KLineBar(
        date=dt,
        open_price=open_p,
        close_price=close_p,
        high_price=high_p,
        low_price=low_p,
        pct_change=pct,
        turnover_rate=turnover,
        is_limit_up=is_lu,
        is_limit_down=is_ld,
        is_broken_board=is_broken,
    )


# ---------------------------------------------------------------------------
# 股票列表抓取（主力：东方财富；备用：AkShare/新浪）
# ---------------------------------------------------------------------------

def _fetch_from_eastmoney(timeout: int = 10) -> List[StockBasicInfo]:
    """
    东方财富 clist 接口获取 A 股全市场列表（主板 + 科创板 + 创业板）。
    成功返回列表；数据不完整或网络异常则抛出异常，由上层切换备用。

    市场代码（fs 参数）：
      m:1+t:2   沪主板
      m:1+t:23  科创板（688）
      m:0+t:6   深主板
      m:0+t:80  创业板（300/301）
    """
    results: List[StockBasicInfo] = []
    market_configs: List[Tuple[str, int]] = [
        ("m:1+t:2",  1),   # 沪主板
        ("m:1+t:23", 1),   # 科创板
        ("m:0+t:6",  0),   # 深主板
        ("m:0+t:80", 0),   # 创业板
    ]

    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
        for fs, market_id in market_configs:
            page = 1
            while True:
                resp = client.get(CLIST_URL, params={
                    "pn": page, "pz": 200, "po": 1, "np": 1,
                    "fltt": 2, "invt": 2, "fid": "f3",
                    "fs": fs,
                    "fields": "f12,f13,f14,f3,f10",
                })
                data = resp.json().get("data") or {}
                items = data.get("diff") or []
                if not items:
                    break

                for item in items:
                    code = str(item.get("f12", ""))
                    name = str(item.get("f14", ""))
                    pct  = float(item.get("f3") or 0)
                    turn = float(item.get("f10") or 0)

                    if not _should_include_stock(code, market_id):
                        continue

                    results.append(StockBasicInfo(
                        code=code,
                        name=name,
                        market=market_id,
                        is_st="ST" in name,
                        pct_change=pct,
                        turnover_rate=turn,
                    ))

                if len(items) < 200:
                    break
                page += 1

    # 全市场应有 4500+ 只；低于 800 说明被严重限流，数据残缺
    if len(results) < 800:
        raise ValueError(f"东方财富 clist 返回数据不完整（仅 {len(results)} 只），切换备用")
    return results


SINA_HQ_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def _batch_sina_pct_change(
    code_list: List[Tuple[str, int]],  # [(code, market), ...]
    batch_size: int = 150,
    timeout: int = 10,
) -> Dict[str, float]:
    """
    用新浪 hq.sinajs.cn 批量查询今日涨跌幅。
    返回 {code: pct_change}。
    格式：var hq_str_sh600000="名称,今开,昨收,现价,最高,最低,...";
    涨跌幅 = (现价 - 昨收) / 昨收 * 100
    """
    pct_map: Dict[str, float] = {}
    prefix_map = {1: "sh", 0: "sz"}

    # 分批请求
    batches = [code_list[i:i+batch_size] for i in range(0, len(code_list), batch_size)]
    with httpx.Client(headers=SINA_HEADERS, timeout=timeout) as client:
        for batch in batches:
            sina_keys = [f"{prefix_map[mkt]}{code}" for code, mkt in batch]
            try:
                resp = client.get(SINA_HQ_URL + ",".join(sina_keys))
                for line in resp.text.splitlines():
                    # var hq_str_sh600000="..."; → extract code + fields
                    if not line.startswith("var hq_str_"):
                        continue
                    try:
                        key_part = line.split("=")[0]          # var hq_str_sh600000
                        raw_code = key_part.split("_")[-1]     # sh600000
                        pure_code = raw_code[2:]               # 600000
                        content = line.split('"')[1]            # 名称,今开,昨收,现价,...
                        fields = content.split(",")
                        if len(fields) < 4:
                            continue
                        prev_close = float(fields[2])
                        curr_price = float(fields[3])
                        if prev_close > 0:
                            pct = (curr_price - prev_close) / prev_close * 100
                            pct_map[pure_code] = round(pct, 2)
                    except (ValueError, IndexError):
                        continue
            except Exception as e:
                print(f"[fetcher] 新浪批量查询异常: {e}")
                continue

    return pct_map


def _fetch_from_akshare() -> List[StockBasicInfo]:
    """
    AkShare + 新浪财经 备用接口。
    覆盖：沪主板 + 科创板(688) + 深主板 + 创业板(300/301)
    1. 从交易所官方 API 获取代码列表（AkShare）
    2. 从新浪 hq.sinajs.cn 批量查询今日涨跌幅（约 10-20s）
    """
    import akshare as ak
    import pandas as pd

    print("[fetcher] 切换备用接口（交易所列表 + 新浪行情）...")

    # ── 1. 沪市：主板 + 科创板 ────────────────────────────────────────
    sh_frames = []

    sh_main = ak.stock_info_sh_name_code(symbol="主板A股")
    sh_main = sh_main.rename(columns={"证券代码": "code", "证券简称": "name", "上市日期": "listing_date"})
    sh_main["market"] = 1
    sh_main["code"] = sh_main["code"].astype(str).str.zfill(6)
    sh_frames.append(sh_main)

    try:
        sh_star = ak.stock_info_sh_name_code(symbol="科创板")
        sh_star = sh_star.rename(columns={"证券代码": "code", "证券简称": "name", "上市日期": "listing_date"})
        sh_star["market"] = 1
        sh_star["code"] = sh_star["code"].astype(str).str.zfill(6)
        sh_frames.append(sh_star)
        print(f"[fetcher]   科创板: {len(sh_star)} 只")
    except Exception as e:
        print(f"[fetcher]   科创板列表获取失败（跳过）: {e}")

    sh_df = pd.concat(sh_frames, ignore_index=True).drop_duplicates(subset=["code"])

    # ── 2. 深市：主板 + 创业板 ────────────────────────────────────────
    sz_combined = None
    for _attempt in range(3):
        try:
            sz_raw = ak.stock_info_sz_name_code(symbol="A股列表")
            # 板块列可能包含：主板、中小板、创业板
            sz_combined = sz_raw[sz_raw["板块"].isin(["主板", "创业板"])].copy()
            sz_combined = sz_combined.rename(
                columns={"A股代码": "code", "A股简称": "name", "A股上市日期": "listing_date"}
            )
            sz_combined["market"] = 0
            sz_combined["code"] = sz_combined["code"].astype(str).str.zfill(6)
            break
        except Exception as e:
            print(f"[fetcher]   深交所列表第{_attempt+1}次尝试失败: {e}")
            if _attempt < 2:
                time.sleep(3)

    if sz_combined is None:
        print("[fetcher]   深交所接口不可用，跳过深市")
        sz_combined = pd.DataFrame(columns=["code", "name", "market", "listing_date"])
        sz_combined["market"] = 0

    # ── 3. 合并去重 ───────────────────────────────────────────────────
    combined = pd.concat([
        sh_df[["code", "name", "market", "listing_date"]],
        sz_combined[["code", "name", "market", "listing_date"]],
    ], ignore_index=True).drop_duplicates(subset=["code"])
    combined = combined[combined["code"].str.len() == 6]

    # 过滤：只保留我们关心的代码前缀
    combined = combined[combined.apply(
        lambda r: _should_include_stock(str(r["code"]), int(r["market"])), axis=1
    )]

    print(f"[fetcher]   交易所列表: 沪 {len(sh_df)} + 深 {len(sz_combined)} → 合并 {len(combined)} 只")

    # ── 2. 批量查询今日涨跌幅（新浪 hq.sinajs.cn）────────────────────
    print("[fetcher]   批量拉取涨跌幅（新浪，约 5-10s）...")
    code_list = [(row["code"], int(row["market"])) for _, row in combined.iterrows()]
    pct_map = _batch_sina_pct_change(code_list)
    print(f"[fetcher]   涨跌幅获取成功: {len(pct_map)} 只")

    # ── 3. 组装结果 ───────────────────────────────────────────────────
    results: List[StockBasicInfo] = []
    for _, row in combined.iterrows():
        code = row["code"]
        # 解析上市日期（格式可能为 Timestamp 或字符串）
        try:
            raw_ld = row.get("listing_date")
            if pd.notna(raw_ld):
                listing_dt = pd.Timestamp(raw_ld).date()
            else:
                listing_dt = None
        except Exception:
            listing_dt = None

        results.append(StockBasicInfo(
            code=code,
            name=str(row["name"]),
            market=int(row["market"]),
            is_st="ST" in str(row["name"]),
            pct_change=pct_map.get(code, 0.0),
            turnover_rate=0.0,  # 新浪无换手率，从 K 线获取
            listing_date=listing_dt,
        ))

    print(f"[fetcher]   备用接口完成: {len(results)} 只（主板+科创板+创业板）")
    return results


def fetch_main_board_stocks(timeout: int = 60) -> List[StockBasicInfo]:
    """
    获取 A 股全市场（主板 + 科创板 + 创业板）全部股票的当日基础信息。

    主力：AkShare（交易所官方列表 + 新浪涨跌幅），完整约 4500 只，耗时约 30s。
    备用：东方财富 clist，速度快但受 TLS 指纹 + 分页限流影响，数据可能不完整。
    """
    try:
        result = _fetch_from_akshare()
        print(f"[fetcher] AkShare 列表接口成功: 主板 {len(result)} 只")
        return result
    except Exception as e:
        print(f"[fetcher] AkShare 接口失败 ({e})，尝试东方财富备用...")

    try:
        result = _fetch_from_eastmoney(timeout=min(timeout, 15))
        print(f"[fetcher] 东方财富列表接口成功（数据可能不完整）: {len(result)} 只")
        return result
    except Exception as e:
        raise RuntimeError(f"主板列表获取失败（AkShare + 东方财富均不可用）: {e}") from e


# ---------------------------------------------------------------------------
# K 线抓取
# ---------------------------------------------------------------------------

TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_HEADERS = {
    "Referer": "https://finance.qq.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def _parse_tencent_klines(
    raw_bars: list,
    is_st: bool = False,
    limit_pct: float = 9.90,
) -> List[KLineBar]:
    """
    解析腾讯财经 K 线数据。
    格式：[date, open, close, high, low, volume]
    涨跌幅由相邻两根 K 线的收盘价推算；
    换手率无法获取，设为 0（影响评分精度但不影响涨跌停判断）。
    """
    bars: List[KLineBar] = []
    for i, row in enumerate(raw_bars):
        try:
            dt = date.fromisoformat(str(row[0]))
            open_p  = float(row[1])
            close_p = float(row[2])
            high_p  = float(row[3])
            low_p   = float(row[4])
        except (ValueError, IndexError):
            continue

        # 涨跌幅：用前一根收盘价计算
        if i == 0:
            pct = 0.0  # 第一根无前置，设 0
            prev_close = 0.0
        else:
            prev_close = float(raw_bars[i - 1][2])
            pct = (close_p - prev_close) / prev_close * 100 if prev_close > 0 else 0.0
            pct = round(pct, 2)

        # ── 使用实际价格判断涨跌停（与 _parse_kline_bar 保持一致）────────────
        if prev_close > 0:
            actual_limit = limit_pct + 0.1
            lu_price = round(prev_close * (1 + actual_limit / 100), 2)
            ld_price = round(prev_close * (1 - actual_limit / 100), 2)
            is_lu = close_p >= lu_price
            is_ld = close_p <= ld_price
        else:
            is_lu = pct >= limit_pct
            is_ld = pct <= -limit_pct

        # 炸板判断（用正确的涨停价）
        if not is_st and not is_lu and prev_close > 0:
            limit_price = prev_close * (1 + limit_pct / 100)
            is_broken = high_p >= limit_price * 0.999
        else:
            is_broken = False

        bars.append(KLineBar(
            date=dt,
            open_price=open_p,
            close_price=close_p,
            high_price=high_p,
            low_price=low_p,
            pct_change=pct,
            turnover_rate=0.0,   # 腾讯接口无换手率
            is_limit_up=is_lu,
            is_limit_down=is_ld,
            is_broken_board=is_broken,
        ))
    return bars


def _fetch_kline_eastmoney(
    code: str, market: int, days: int, is_st: bool, limit_pct: float, timeout: int
) -> List[KLineBar]:
    """东方财富历史 K 线（含换手率）"""
    from datetime import date as _date
    secid = f"{market}.{code}"
    end_date = _date.today().strftime("%Y%m%d")

    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
        resp = client.get(KLINE_URL, params={
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "lmt": days,
            "klt": 101,
            "fqt": 1,
            "end": end_date,
        })
        payload = resp.json()
        data = payload.get("data") or {}
        klines_raw = data.get("klines") or []
        bars = [
            bar for raw in klines_raw
            if (bar := _parse_kline_bar(raw, is_st, limit_pct)) is not None
        ]
        if not bars:
            raise ValueError("东方财富 K 线返回空数据")
        return bars


def _fetch_kline_tencent(
    code: str, market: int, days: int, is_st: bool, limit_pct: float, timeout: int
) -> List[KLineBar]:
    """腾讯财经历史 K 线（无换手率，从相邻收盘价计算涨跌幅）"""
    prefix = "sh" if market == 1 else "sz"
    full_code = f"{prefix}{code}"

    with httpx.Client(headers=TENCENT_HEADERS, timeout=timeout) as client:
        resp = client.get(TENCENT_KLINE_URL, params={
            "param": f"{full_code},day,,,{days},qfq",
        })
        data = resp.json()
        raw_bars = (
            data.get("data", {}).get(full_code, {}).get("qfqday", [])
        )
        if not raw_bars:
            raise ValueError("腾讯财经 K 线返回空数据")
        return _parse_tencent_klines(raw_bars, is_st, limit_pct)


def fetch_kline(
    code: str,
    market: int,
    days: int = 65,
    is_st: bool = False,
    timeout: int = 15,
) -> List[KLineBar]:
    """
    拉取单股日线 K 线数据。优先东方财富（含换手率），失败切换腾讯财经。
    days=65 保证能算出近60日指标（留5日冗余）。
    涨跌停幅度根据股票代码自动判断（主板±10%，科创板/创业板±20%）。
    """
    lp = get_limit_pct(code, is_st)
    try:
        return _fetch_kline_eastmoney(code, market, days, is_st, lp, timeout)
    except Exception:
        pass

    try:
        return _fetch_kline_tencent(code, market, days, is_st, lp, timeout)
    except Exception as e:
        print(f"[fetcher] K 线拉取最终失败 ({market}.{code}): {e}")
        return []


def fetch_klines_batch(
    stocks: List[StockBasicInfo],
    days: int = 65,
    max_workers: int = 5,
    delay_between: float = 0.1,
) -> Dict[str, List[KLineBar]]:
    """
    并发批量拉取多只股票的 K 线。
    返回 {code: [KLineBar, ...]}
    max_workers 默认 5（保守，避免触发服务器封锁）。
    """
    results: Dict[str, List[KLineBar]] = {}

    def _fetch_one(stock: StockBasicInfo) -> Tuple[str, List[KLineBar]]:
        bars = fetch_kline(stock.code, stock.market, days=days, is_st=stock.is_st)
        if delay_between > 0:
            time.sleep(delay_between)
        return stock.code, bars

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, s): s for s in stocks}
        for future in as_completed(futures):
            try:
                code, bars = future.result()
                results[code] = bars
            except Exception as e:
                stock = futures[future]
                print(f"[fetcher] 批量拉取失败 ({stock.code}): {e}")
                results[stock.code] = []

    return results


# ---------------------------------------------------------------------------
# 强势池筛选 API（东方财富智能选股 search-code 接口）
# ---------------------------------------------------------------------------

STRONG_POOL_SEARCH_URL = (
    "https://np-tjxg-g.eastmoney.com/api/smart-tag/stock/v3/pw/search-code"
)

# 选股关键词：主板非ST、非退市、非新股次新股，满足连板/涨停/涨幅条件之一
STRONG_POOL_KEYWORD = (
    "主板非ST;非退市股；非新股非次新；"
    "近60个交易日最高连板数大于3或者"
    "近60个交易日涨停天数大于9或者"
    "近10个交易日涨停天数大于4或"
    "近20个交易日涨幅前10;"
)

_F10_HEADERS = {
    "Referer": "https://emweb.securities.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def fetch_stock_bk_codes(code: str) -> list[str]:
    """
    通过东财 emweb F10 接口获取个股所属板块代码列表。
    返回 ["BK0665", "BK0940", ...] 格式，与 sectors.code 字段直接对应。
    失败时返回空列表，不阻断主流程。
    """
    mkt = "SH" if code.startswith(("6", "5", "9")) else "SZ"
    url = (
        f"https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax"
        f"?code={mkt}{code}"
    )
    try:
        resp = httpx.get(url, headers=_F10_HEADERS, timeout=10)
        data = resp.json()
        bk_codes = []
        for item in data.get("ssbk", []):
            board_code = str(item.get("BOARD_CODE", "")).strip()
            if board_code:
                bk_codes.append(f"BK{board_code.zfill(4)}")
        return bk_codes
    except Exception:
        return []


_SEARCH_HEADERS = {
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


def fetch_strong_pool_codes(
    xc_id: str = "xc11bd34d6790101033c",
    fingerprint: str = "a3b5b577646954c0a1ff47146894e3d1",
    keyword: str = STRONG_POOL_KEYWORD,
    page_size: int = 50,
) -> Set[str]:
    """
    调用东方财富智能选股 search-code 接口，返回满足强势股条件的股票代码集合。
    自动分页直到拉取全部结果。

    参数说明：
      xc_id       — 选股方案 ID（对应特定的筛选条件组合）
      fingerprint — 客户端指纹（固定值，由东方财富页面生成）
      keyword     — 自然语言选股条件，与 xc_id 对应
    """
    custom_data = f'[{{"type":"text","value":"{keyword}","extra":""}}]'
    codes: Set[str] = set()
    page_no = 1
    total: int | None = None

    while True:
        ts = str(int(time.time() * 1_000_000))
        rid = "".join(random.choices(string.ascii_letters, k=32)) + str(int(time.time() * 1000))
        body = {
            "needAmbiguousSuggest": True,
            "pageSize": page_size,
            "pageNo": page_no,
            "fingerprint": fingerprint,
            "matchWord": "",
            "shareToGuba": False,
            "timestamp": ts,
            "requestId": rid,
            "removedConditionIdList": [],
            "ownSelectAll": False,
            "needCorrect": True,
            "client": "WEB",
            "product": "",
            "needShowStockNum": False,
            "biz": "web_ai_select_stocks",
            "xcId": xc_id,
            "gids": [],
            "dxInfoNew": [],
            "keyWordNew": keyword,
            "customDataNew": custom_data,
        }

        try:
            resp = httpx.post(
                STRONG_POOL_SEARCH_URL,
                headers=_SEARCH_HEADERS,
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[fetcher] 强势池 API 第 {page_no} 页失败: {e}")
            break

        result = data.get("data", {}).get("result", {})
        data_list = result.get("dataList", [])

        if total is None:
            total = result.get("total", len(data_list))

        for item in data_list:
            code = item.get("SECURITY_CODE", "").strip()
            if code:
                codes.add(code)

        if not data_list or len(codes) >= (total or 0):
            break
        page_no += 1
        time.sleep(0.3)

    return codes


# 涨跌停选股关键词
LIMIT_MOVE_KEYWORD = "非ST；非退市股票；涨停股票或者跌停股票"


def fetch_limit_move_codes(
    xc_id: str = "xc11bd34d6790101033c",
    fingerprint: str = "a3b5b577646954c0a1ff47146894e3d1",
    keyword: str = LIMIT_MOVE_KEYWORD,
    page_size: int = 50,
) -> Set[str]:
    """
    调用东方财富智能选股 API，获取今日涨停 + 跌停的非ST非退市股票代码集合。
    替代原来扫描全量 5206 只股票再过滤涨跌停的逻辑。
    """
    # 直接复用 fetch_strong_pool_codes 的实现，只换 keyword
    return fetch_strong_pool_codes(
        xc_id=xc_id,
        fingerprint=fingerprint,
        keyword=keyword,
        page_size=page_size,
    )
