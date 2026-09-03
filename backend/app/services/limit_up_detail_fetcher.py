"""
涨停板明细抓取（涨停板块雷达专用），2026-08-25新增。

━━ 实际核实过的东财接口语义（不是照字段名猜的）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

用 2026-08-25 实盘数据逐个请求核对过，结论如下：

1) push2ex.eastmoney.com/getTopicZTPool —— **涨停池，本模块的权威数据源**
   当天返回 65 行，字段（全部实测确认）：
     c    股票代码          n     股票名称
     m    市场 0=深 1=沪（跟本仓库 fetch_kline 的 market 约定一致）
     p    最新价 ×1000     （002821=172230 → 172.23，跟独立核实的实盘收盘价一致）
     zdp  涨跌幅 %          amount 成交额（元）
     ltsz 流通市值（元）    tshare 总市值（元）
     hs   换手率 %          （002821=5.37，跟腾讯行情的 turnover_rate 完全一致，交叉验证过）
     lbc  连板数            zttj  {'days':N,'ct':M} 即"N日M板"
     fbt  首次封板时间      lbt   最终封板时间   —— 均为 HHMMSS 整数，92500=09:25:00
     fund 封单额（元）      zbc   当日炸板次数
     hybk 东财行业板块名
   **必须带 ut 参数**，不带时 data 返回空对象（实测）。

2) push2ex.eastmoney.com/getTopicZBPool —— 炸板池，当天 22 行
   字段与涨停池基本一致，多一个 ztp(涨停价×1000)/zf(振幅)/zs，
   没有 lbt（因为收盘没封住）。用来算封板率。

3) datacenter.eastmoney.com RPT_PCHOT_LIMITLIST_HSDETIAL —— **只提供涨停原因，且不完整**
   用户最初指定的就是这个接口。实测两个关键事实：
   a. `SSLIMITUP_TIME` 是**最终封板时间**，不是首次涨停时间——拿它跟涨停池逐行比对，
      41/41 全部等于 lbt，0 例等于 fbt。它也**没有**封单额、没有首次涨停时间。
   b. 它当天只有 48 行，涨停池有 65 行，**漏了 17 只**（包括 002821 凯莱英这只当天
      确实涨停的）。所以它不是涨停股全集，只是"有AI生成涨停原因的那部分"。
   因此本模块把它降级为**可选的原因补充**：LIMIT_REASON / LIMIT_CONTENT 拿不到就留空，
   绝不用它来决定"今天谁涨停了"。

设计原则跟本仓库数据契约一致（见 daily_update.py / eastmoney_fetcher.py 的相关注释）：
拿不到的字段一律 None，不用 0 或空串冒充"已知"。
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, time
from typing import Dict, List, Optional, Tuple

import httpx

from .eastmoney_fetcher import json_or_explain

# 涨停/炸板池接口必须带 ut，这是东财前端公开使用的固定值（不带则 data 为空）
_PUSH2EX_UT = "7eea3edcaed734bea9cbfc24409ed989"
_ZT_POOL_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
_ZB_POOL_URL = "https://push2ex.eastmoney.com/getTopicZBPool"
_REASON_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

SOURCE_NAME = "eastmoney"


@dataclass
class LimitUpDetail:
    """一只股票某个交易日的涨停事实。字段为 None 一律表示"这个来源没给"，不是0。"""
    code: str
    name: str
    market: Optional[int] = None            # 0=深 1=沪
    price: Optional[float] = None           # 元
    pct_change: Optional[float] = None      # %
    amount: Optional[float] = None          # 成交额（元）
    turnover_rate: Optional[float] = None   # 换手率 %
    float_market_cap: Optional[float] = None  # 流通市值（元）
    board_count: Optional[int] = None       # 连板数
    limit_stat_days: Optional[int] = None   # "N日M板" 的 N
    limit_stat_count: Optional[int] = None  # "N日M板" 的 M
    first_limit_time: Optional[time] = None # 首次封板时间
    last_limit_time: Optional[time] = None  # 最终封板时间
    seal_amount: Optional[float] = None     # 封单额（元）
    broken_times: Optional[int] = None      # 当日炸板次数
    em_industry: Optional[str] = None       # 东财行业板块名（仅参考，不用于归组）
    limit_reason: Optional[str] = None      # 涨停原因短标签（催化剂，不是板块归属）
    limit_content: Optional[str] = None     # 涨停原因详述（AI生成，含免责声明）


@dataclass
class BrokenBoardDetail:
    """
    炸板（盘中触及涨停但收盘没封住）事实。

    2026-08-26 补全字段：此前只解析了 7 个字段，而 getTopicZBPool 的原始响应里
    换手率(hs)/成交额(amount)/流通市值(ltsz)/涨停价(ztp)/连板统计(zttj)/振幅(zf)
    一直都在，只是没人取。用户找来一个新接口(stockextenddata typelist Ty=4)想补
    这些字段，实测那个接口的字段是本接口的子集，还少了振幅、没有 date 参数
    （查不了历史）、要带 dn;/st;/uid; 几个空 header 和一个写死的 ut token——
    换过去是净亏。所以是把现有接口解析全，不是换源。

    炸板池**没有** lbt（最终封板时间）：它收盘就是没封住，本来就没有"最终封板"。
    """
    code: str
    name: str
    market: Optional[int] = None
    pct_change: Optional[float] = None
    first_limit_time: Optional[time] = None
    broken_times: Optional[int] = None
    em_industry: Optional[str] = None
    # ── 以下 2026-08-26 补全 ──────────────────────────────────────────────
    price: Optional[float] = None            # 最新价（元）
    limit_price: Optional[float] = None      # 当日涨停价（元），跟最新价的差=回落幅度
    board_count: Optional[int] = None        # 连板数——高位板炸板和首板炸板不是一回事
    limit_stat_days: Optional[int] = None    # "N天M板" 的 N
    limit_stat_count: Optional[int] = None   # "N天M板" 的 M
    turnover_rate: Optional[float] = None    # 换手率 %
    amount: Optional[float] = None           # 成交额（元）
    float_market_cap: Optional[float] = None # 流通市值（元）
    amplitude: Optional[float] = None        # 振幅 %


def parse_em_time(raw) -> Optional[time]:
    """
    东财 fbt/lbt 是 HHMMSS 整数：92500 → 09:25:00，145503 → 14:55:03。
    注意不能当字符串左填充处理——上午的时间只有5位。异常一律返回 None，不猜。
    """
    if raw is None:
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    if v < 0 or v > 235959:
        return None
    h, m, s = v // 10000, (v // 100) % 100, v % 100
    if h > 23 or m > 59 or s > 59:
        return None
    return time(h, m, s)


def _num(v) -> Optional[float]:
    """数值字段：None/空/非数字一律 None，不降级成 0.0。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _int(v) -> Optional[int]:
    f = _num(v)
    return int(f) if f is not None else None


def parse_zt_pool_row(row: dict) -> Optional[LimitUpDetail]:
    """解析涨停池单行。没有股票代码的行直接丢弃（不构造半个对象）。"""
    code = (row or {}).get("c")
    if not code:
        return None
    price = _num(row.get("p"))
    ltsz = _num(row.get("ltsz"))
    zttj = row.get("zttj") or {}
    if not isinstance(zttj, dict):
        zttj = {}
    return LimitUpDetail(
        code=str(code),
        # 东财个别名称带空格（如 '金 螳 螂'），统一去掉便于跟本地库匹配
        name=str(row.get("n") or "").replace(" ", ""),
        market=_int(row.get("m")),
        price=round(price / 1000, 3) if price is not None else None,  # p 是 ×1000
        pct_change=round(_num(row.get("zdp")), 2) if _num(row.get("zdp")) is not None else None,
        amount=_num(row.get("amount")),
        turnover_rate=round(_num(row.get("hs")), 2) if _num(row.get("hs")) is not None else None,
        float_market_cap=ltsz,
        board_count=_int(row.get("lbc")),
        limit_stat_days=_int(zttj.get("days")),
        limit_stat_count=_int(zttj.get("ct")),
        first_limit_time=parse_em_time(row.get("fbt")),
        last_limit_time=parse_em_time(row.get("lbt")),
        seal_amount=_num(row.get("fund")),
        broken_times=_int(row.get("zbc")),
        em_industry=(str(row["hybk"]) if row.get("hybk") else None),
    )


def parse_zb_pool_row(row: dict) -> Optional[BrokenBoardDetail]:
    """
    解析炸板池单行。字段口径 2026-08-26 用实盘数据逐个对照腾讯行情核实：
    600508 上海能源 p=11450→11.45（现价，×1000）、ztp=11960→11.96（涨停价，
    昨收10.87×1.1=11.957四舍五入）、hs=10.851→换手率%、amount=1218008048→
    成交额12.18亿、ltsz=11585169540→流通市值115.85亿、zbc=1→炸板1次、
    zttj={'days':3,'ct':2}→3天2板，全部与腾讯独立核对一致。
    注意价格是 ×1000（涨停池也是），不是 ×100。
    """
    code = (row or {}).get("c")
    if not code:
        return None
    price, ztp = _num(row.get("p")), _num(row.get("ztp"))
    zdp, hs, zf = _num(row.get("zdp")), _num(row.get("hs")), _num(row.get("zf"))
    zttj = row.get("zttj") or {}
    if not isinstance(zttj, dict):
        zttj = {}
    return BrokenBoardDetail(
        code=str(code),
        name=str(row.get("n") or "").replace(" ", ""),
        market=_int(row.get("m")),
        pct_change=round(zdp, 2) if zdp is not None else None,
        first_limit_time=parse_em_time(row.get("fbt")),
        broken_times=_int(row.get("zbc")),
        em_industry=(str(row["hybk"]) if row.get("hybk") else None),
        price=round(price / 1000, 3) if price is not None else None,
        limit_price=round(ztp / 1000, 3) if ztp is not None else None,
        board_count=_int(row.get("lbc")),
        limit_stat_days=_int(zttj.get("days")),
        limit_stat_count=_int(zttj.get("ct")),
        turnover_rate=round(hs, 2) if hs is not None else None,
        amount=_num(row.get("amount")),
        float_market_cap=_num(row.get("ltsz")),
        amplitude=round(zf, 2) if zf is not None else None,
    )


def clean_limit_content(raw: Optional[str]) -> Optional[str]:
    """
    LIMIT_CONTENT 里的换行是字面量反斜杠+n（不是真换行符），还带一段固定的AI免责声明。
    这里把换行还原成真换行；免责声明保留——它说明这段文字是AI生成的，去掉反而误导。
    """
    if not raw:
        return None
    return str(raw).replace("\\n", "\n").strip() or None


def _get_json(url: str, params: dict, timeout: int) -> dict:
    """涨停池/炸板池/涨停原因共用。解析失败时带上 HTTP 状态和 body 开头——
    这条路径 2026-08-28 在东财兜底上吃过 502 Bad Gateway，光看 `Expecting value`
    查不出来是被拦了还是接口变了。"""
    with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=timeout) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        return json_or_explain(resp, f"东财 {url.rsplit('/', 1)[-1]} ")


def fetch_limit_up_pool(trade_date: date, timeout: int = 20) -> List[LimitUpDetail]:
    """涨停池：当日涨停股全集 + 封单额/首末封板时间/炸板次数/连板数。"""
    payload = _get_json(_ZT_POOL_URL, {
        "ut": _PUSH2EX_UT, "dpt": "wz.ztzt", "Pageindex": "0", "pagesize": "600",
        "sort": "fbt:asc", "date": trade_date.strftime("%Y%m%d"),
    }, timeout)
    pool = ((payload.get("data") or {}) or {}).get("pool") or []
    out = [d for d in (parse_zt_pool_row(r) for r in pool) if d]
    return out


def fetch_broken_board_pool(trade_date: date, timeout: int = 20) -> List[BrokenBoardDetail]:
    """炸板池：盘中触及涨停但收盘没封住的股票，用于算封板率。"""
    payload = _get_json(_ZB_POOL_URL, {
        "ut": _PUSH2EX_UT, "dpt": "wz.ztzt", "Pageindex": "0", "pagesize": "600",
        "sort": "fbt:asc", "date": trade_date.strftime("%Y%m%d"),
    }, timeout)
    pool = ((payload.get("data") or {}) or {}).get("pool") or []
    return [d for d in (parse_zb_pool_row(r) for r in pool) if d]


def fetch_limit_reasons(trade_date: date, timeout: int = 25) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """
    涨停原因（RPT_PCHOT_LIMITLIST_HSDETIAL）。返回 {code: (reason, content)}。

    这个接口覆盖不全（实测 48/65），所以只当补充：查不到的股票原因留空，
    绝不能用它来判断"谁涨停了"。同理它的 SSLIMITUP_TIME 是最终封板时间而不是
    首封时间，本模块一律以涨停池的 fbt/lbt 为准，不读这个字段。
    """
    payload = _get_json(_REASON_URL, {
        "source": "SECURITIES", "client": "APP",
        "reportName": "RPT_PCHOT_LIMITLIST_HSDETIAL",
        "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,LIMIT_REASON,LIMIT_CONTENT",
        "quoteColumns": "lastCloseTime~19~SECURITY_CODE~SSLIMITUP_TIME",
        "filter": f"(TRADE_DATE='{trade_date.strftime('%Y-%m-%d')} 00:00:00')",
        "pageNumber": "-1", "pageSize": "", "sortColumns": "RANK_TIME", "sortTypes": "-1",
    }, timeout)
    rows = ((payload.get("result") or {}) or {}).get("data") or []
    out: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for r in rows:
        code = (r or {}).get("SECURITY_CODE")
        if not code:
            continue
        out[str(code)] = (
            (str(r["LIMIT_REASON"]).strip() or None) if r.get("LIMIT_REASON") else None,
            clean_limit_content(r.get("LIMIT_CONTENT")),
        )
    return out


def fetch_limit_up_details(
    trade_date: date, timeout: int = 20,
) -> Tuple[List[LimitUpDetail], Optional[List[BrokenBoardDetail]], List[str]]:
    """
    一次性取齐涨停板块雷达需要的全部外部事实，返回 (涨停明细, 炸板明细, 警告列表)。

    三个接口互相独立降级：涨停池是主干，它失败就整体抛错（没有涨停名单这个功能无意义）；
    炸板池和涨停原因失败只记 warning、其余数据照常返回——封板率显示不出来、原因留空，
    好过整页打不开。

    **炸板明细失败时返回 None，不是 []**（2026-08-26修，生产上真的删了数据）：
    此前失败返回空列表，调用方无法区分"拉到了，今天确实没有炸板"和"根本没拉到"，
    于是 _prune_stale 把空列表当权威名单，**把已有的 20 条炸板明细全删了**——日志
    里"炸板池拉取失败（ConnectTimeout）"和"清理旧行：炸板 20 条"是同一次运行打出
    来的。这跟 KLineBar.turnover_rate 那次是同一个病：用空值表达"不知道"。
    """
    warnings: List[str] = []

    # 三个接口**并发**打（2026-08-26改）：它们互相独立、分属两个不同域名
    # （push2ex / datacenter），串行等于把三段网络延迟加起来。用户点一次刷新要等
    # 40 秒，这里是大头之一。并发3路对同一家的压力等同于页面正常打开时的请求数。
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_zt = ex.submit(fetch_limit_up_pool, trade_date, timeout)
        f_zb = ex.submit(fetch_broken_board_pool, trade_date, timeout)
        f_rs = ex.submit(fetch_limit_reasons, trade_date, timeout + 5)

        details = f_zt.result()     # 涨停池失败直接抛，由调用方处理：没有涨停名单
                                    # 这个功能就没有意义，不做"部分成功"的降级
        try:
            broken = f_zb.result()
        except Exception as e:  # noqa: BLE001
            broken = None   # None = 没拉到；[] 是"拉到了，今天没有炸板"，两者不能混
            warnings.append(f"炸板池拉取失败（{type(e).__name__}），封板率本次无法计算，"
                            f"已有炸板明细保持不变")
        try:
            reasons = f_rs.result()
        except Exception as e:  # noqa: BLE001
            reasons = {}
            warnings.append(f"涨停原因拉取失败（{type(e).__name__}），本次不显示涨停原因")

    for d in details:
        reason, content = reasons.get(d.code, (None, None))
        d.limit_reason = reason
        d.limit_content = content

    if details and reasons:
        missing = sum(1 for d in details if not d.limit_reason)
        if missing:
            # 这是该接口的固有覆盖缺口，不是故障，只记录不告警
            warnings.append(f"{missing}/{len(details)} 只涨停股东财未提供涨停原因（该接口本身覆盖不全）")
    return details, broken, warnings


# ─── 东财「连板天梯」（权威梯队，2026-09-03 接入）───────────────────────────
#
# 端点与 _REASON_URL 同一个 datacenter，reportName 不同：
#   reportName=RPT_INTSELECTION_MONITORHIS
#   filter=(TRADE_DATE='YYYY-MM-DD')(@N_CLASS<>"NULL")(IS_ST="0")
#   N_CLASS = 连板数
#
# **TRADE_DATE 是入参，任意历史日期都能取**——这一点决定了它的价值。
# 实测 2026-06-02 / 07-09 / 08-06 / 09-01 全部 code=0，pages=1，与东财 APP
# 「涨停专题 → 连板天梯」页面显示逐档一致。
#
# 为什么需要它：此前市场高度是**从我们自己的候选池快照反推**的，而
# verify_board_history 只能拿重拉的 K 线跟库里已标记的高板对账——那是**自指的**，
# 只能证伪"我们说有、实际没有"，抓不到"实际有、我们没记"。2026-08-06 就是活证据：
# 校验报"0 分歧"，而东财天梯是 10 板、我们只有 5 板，整段行情的最高点漏掉了。
# 这个接口是独立的外部标准，补的正是那个方向。
#
# 口径注意：
#   · 只返回 **2 板及以上**，不含首板。跟我们自己算的 "1" 档正好互补，不重叠
#   · IS_ST="0" 已在服务端排除 ST，跟本仓库选股 prompt 的「非ST」口径一致
_LADDER_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"


def fetch_limit_up_ladder(trade_date, timeout: int = 15) -> Optional[Dict[str, int]]:
    """
    取某个交易日的权威连板天梯，返回 {股票代码: 连板数}（只含 2 板及以上）。

    返回值三分，**"没有"和"不知道"绝不能混**（炸板池 fetch 失败返回 [] 曾经直接
    删掉 20 行数据，就是混了这两件事）：

      {code: n, ...}  那天的梯队
      {}              那天确实没有 2 板及以上（东财 code=9201「返回数据为空」）
      None            请求失败 / 响应异常 —— 不知道

    9201 这个码是实测出来的：未来日期和周末都返回 `code=9201, success=false`，
    而正常交易日是 `code=0`。所以东财自己就把"空"和"错"分开了，我们只要别把它们
    重新揉回去。传周末进来会得到 {}（那天确实没有连板股），调用方自己保证只问
    交易日。
    """
    d = trade_date.isoformat() if hasattr(trade_date, "isoformat") else str(trade_date)
    params = {
        "source": "SECURITIES", "client": "APP",
        "reportName": "RPT_INTSELECTION_MONITORHIS",
        "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,N_CLASS",
        "filter": f"(TRADE_DATE='{d}')(@N_CLASS<>\"NULL\")(IS_ST=\"0\")",
    }
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as c:
            resp = c.get(_LADDER_URL, params=params)
        body = json_or_explain(resp, f"东财连板天梯 {d} ")
    except Exception:  # noqa: BLE001
        return None
    code = body.get("code")
    if code == 9201:
        return {}          # 东财明说"返回数据为空"：那天确实没有 2 板及以上
    if code != 0:
        return None        # 其它异常码 = 不知道
    res = body.get("result") or {}
    rows = res.get("data") or []
    out: Dict[str, int] = {}
    for r in rows:
        code = (r.get("SECURITY_CODE") or "").strip()
        n = r.get("N_CLASS")
        if code and isinstance(n, int) and n >= 2:
            out[code] = n
    return out
