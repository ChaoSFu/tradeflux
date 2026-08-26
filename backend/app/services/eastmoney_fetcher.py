"""
行情数据抓取层。

封装东方财富公开 API（主力）及 AkShare/新浪财经（备用），提供：
  - fetch_main_board_stocks()     获取 A 股全市场股票列表（沪主板/科创板 + 深主板/创业板）
  - fetch_kline()                 拉取单股 N 日 K 线
  - fetch_klines_batch()          并发批量拉取 K 线

涨跌停幅度（2026-07-06新规后，ST/*ST均跟同板块非ST规则一致，不再单独更小；
已用WebSearch核实新浪财经/澎湃新闻/证券时报等多个独立信源，2026-08-25更新）：
  - 主板（600/601/603/605, 000/001/002/003）：±10%（含ST/*ST）
  - 科创板（688）、创业板（300/301）：±20%（含ST/*ST）
  - 北交所：±30%（含ST/*ST）
  - 炸板判断：当日最高价触及涨停价，但收盘未封板

若东方财富 clist 接口被封锁，自动切换 AkShare/新浪备用（约 30s）。
"""
import httpx
import time
import random
import string
import threading
from contextlib import contextmanager
from datetime import date
from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Optional, Callable
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
_WARMUP_URL = "https://quote.eastmoney.com/zs000001.html"


@contextmanager
def _warmed_client(headers: dict = HEADERS, timeout: int = 15, tag: str = ""):
    """
    push2his.eastmoney.com 的行情接口对"冷连接直接打接口"会静默丢弃请求
    （TLS 握手、HTTP 请求都正常发出，服务器不返回任何响应），但同一个
    连接/会话如果先访问过一次普通网页，紧接着再请求接口就能正常拿到数据
    ——跟具体 cookie 内容无关，只是需要连接不是"冷启动直接打 API"。
    用法与 httpx.Client 一致：`with _warmed_client() as client: ...`。
    预热请求失败不阻断——后续请求仍按原样尝试，失败自有上层重试/兜底处理。

    tag 仅用于日志前缀，方便确认这版代码是否真的在跑、以及预热本身
    是否成功（和后面接口请求是否成功分开看，出问题时才能定位到底是
    预热没生效、还是预热成功了但接口依然被拒）。
    """
    label = f"[warmup{f':{tag}' if tag else ''}]"
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        try:
            r = client.get(_WARMUP_URL)
            print(f"{label} 预热请求完成 status={r.status_code} len={len(r.content)}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{label} 预热请求失败（不阻断，继续按原样尝试正式请求）: {type(e).__name__}: {e}", flush=True)
        yield client


_thread_local = threading.local()


def _thread_warmed_client(headers: dict = HEADERS, timeout: int = 15) -> httpx.Client:
    """
    单只股票 K 线请求量小、但批量并发拉取时数量很大（可达上百只），如果
    每只股票都单独预热一次连接（额外一次网页请求），批量拉取会成倍变慢。
    这里改为每个工作线程只预热一次、同一线程后续请求复用同一个 Client——
    ThreadPoolExecutor 的每个线程在一次 fetch_klines_batch 调用内会处理
    多只股票，预热成本被摊薄到接近可忽略。
    Client 不主动关闭，随线程结束由 GC 回收（线程是短生命周期的批量任务
    线程，不会无限累积）。
    """
    client = getattr(_thread_local, "client", None)
    if client is not None:
        return client
    client = httpx.Client(headers=headers, timeout=timeout, follow_redirects=True)
    tid = threading.get_ident()
    try:
        r = client.get(_WARMUP_URL)
        msg = f"[warmup:thread-{tid}] 预热请求完成 status={r.status_code} len={len(r.content)}"
        print(msg, flush=True)
        _w2s_log("QUOTE", msg)
    except Exception as e:  # noqa: BLE001
        msg = f"[warmup:thread-{tid}] 预热请求失败（不阻断，继续按原样尝试正式请求）: {type(e).__name__}: {e}"
        print(msg, flush=True)
        _w2s_log("QUOTE", msg)
    _thread_local.client = client
    return client

# 纳入范围：主板 + 科创板(688) + 创业板(300/301)
# 排除：北交所(8xxxxx)
SH_INCLUDED_PREFIXES = ("600", "601", "603", "605", "688")
SZ_INCLUDED_PREFIXES = ("000", "001", "002", "003", "300", "301")

# 高幅涨跌停代码前缀（±20%）
_HIGH_LIMIT_PREFIXES = ("688", "300", "301")

# 北交所代码前缀（43/83/87/88/920… 东财 secid 用 market=0，腾讯用 bj 前缀，涨跌停 ±30%）
_BJ_PREFIXES = ("4", "8", "92")


def _is_bj_code(code: str) -> bool:
    return code.startswith(_BJ_PREFIXES)


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
    """单根 K 线（临时对象，计算后不存入 DB）

    turnover_rate 是 Optional 且 **None 表示"这个数据源不提供换手率"，不是"换手率
    是0%"**（2026-08-25改）。腾讯/新浪的K线接口都没有换手率字段，此前两个解析器
    都写 turnover_rate=0.0 顶替，而腾讯正是当前K线主力源——结果就是生产库里每一
    天每只股票的换手率全是0.0，情绪分里的 `turnover*0.8`、龙头分里的
    `turnover_bonus`、以及5日/20日换手趋势全部长期恒为0，一个本该参与打分的因子
    事实上已经死掉很久，却因为"0是个合法数字"而完全没有报错、没人发现。
    这是本仓库反复出现的同一类问题：**用0表达"不知道"**。
    """
    date: date
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    pct_change: float
    turnover_rate: Optional[float]
    is_limit_up: bool = False
    is_limit_down: bool = False
    is_broken_board: bool = False  # 炸板
    is_one_word_limit_up: bool = False    # 一字板涨停（全天最低价未跌破涨停价）
    is_one_word_limit_down: bool = False  # 一字板跌停（全天最高价未涨破跌停价）


def _should_include_stock(code: str, market: int) -> bool:
    """判断是否在抓取范围内（主板 + 科创板 + 创业板，排除北交所）"""
    if market == 1:  # 沪
        return code.startswith(SH_INCLUDED_PREFIXES)
    else:            # 深
        return code.startswith(SZ_INCLUDED_PREFIXES)


def get_limit_pct(code: str, is_st: bool) -> float:
    """
    返回该股票的涨跌停**判定容差阈值**（9.90/19.90/29.90，比真实规则少
    0.1个百分点）——专给"这根K线today是不是涨停/跌停/炸板"这类容差判断用
    （浮点取整误差可能让真实10%涨停算出9.998%这种情况，用9.90做阈值才不会
    漏判），不是真实的交易所涨跌幅规则本身。

    2026-08-23起：如果要算的是涨停价/跌停价/压力止损这类**真实价格**，必须用
    下面的 get_actual_limit_pct()，不能用这个——外部评审指出 w2s_refresh_service.py
    此前直接拿这个容差阈值去算 limit_price/limit_room/stress_stop，导致这几个
    值系统性比真实规则小0.1个百分点（比如主板算成9.9%涨停而不是10%），已修复。

    2026-08-25修复真实规则bug（外部评审指出、已用WebSearch核实多个独立信源，
    非chatgpt.com转发链接那种不可信来源）：沪深交易所自2026-07-06起，主板
    ST/*ST涨跌幅由5%上调至10%，与主板普通股规则完全一致；创业板/科创板/
    北交所的ST股本来就跟本板块非ST规则一致（20%/30%，从未有过单独更严格的
    ST规则）。也就是说这个新规生效后，is_st对任何板块的涨跌幅百分比都不再
    产生区分——is_st参数因此保留（call site兼容/未来如需要按trade_date还原
    历史规则时备用），但不再参与判断。这里只反映"当前"生效的规则，不做历史
    日期区分（K线解析这块本来就是拿当前阈值统一套用整个历史窗口，是本仓库
    一直以来的既有简化，不是这次新引入的；如果以后要精确到"哪天用哪天的
    规则"，需要单独把trade_date也参数化，这次没做，范围太大）。
    """
    if _is_bj_code(code):
        return 29.90  # 北交所 ±30%（含ST，同板块规则）
    if code.startswith(_HIGH_LIMIT_PREFIXES):
        return 19.90  # 科创板 / 创业板 ±20%（含ST，同板块规则）
    return 9.90       # 主板 ±10%（含ST，2026-07-06新规后与非ST一致）


def get_actual_limit_pct(code: str, is_st: bool) -> float:
    """
    返回该股票**真实**的涨跌停幅度规则（10/20/30），不含判定容差。
    算涨停价/跌停价/剩余空间/压力止损这类需要真实价格的场景用这个，不要用
    get_limit_pct()（那个是给K线涨跌停/炸板判定用的容差阈值，会系统性偏小
    0.1个百分点）。

    is_st 不再改变返回值——原因见 get_limit_pct() 的 2026-08-25 修复说明
    （2026-07-06新规后主板ST已跟主板非ST规则一致，其余板块ST本来就没有
    单独更严格的规则）。
    """
    if _is_bj_code(code):
        return 30.0  # 北交所 ±30%（含ST，同板块规则）
    if code.startswith(_HIGH_LIMIT_PREFIXES):
        return 20.0  # 科创板 / 创业板 ±20%（含ST，同板块规则）
    return 10.0      # 主板 ±10%（含ST，2026-07-06新规后与非ST一致）


def _parse_kline_bar(line: str, is_st: bool = False, limit_pct: float = 9.90) -> KLineBar | None:
    """
    解析单行 K 线字符串。
    格式：日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
    limit_pct 由调用方根据股票代码传入（主板 9.90，科创/创业 19.90，北交所 29.90；
    ST 2026-07-06新规后跟同板块非ST规则一致，不再是单独更小的容差，见get_limit_pct()）。
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
    return build_kline_bar(
        dt=dt, open_p=open_p, close_p=close_p, high_p=high_p, low_p=low_p,
        pct=pct, turnover=turnover, is_st=is_st, limit_pct=limit_pct,
    )


def exact_limit_price(prev_close: float, actual_limit_pct: float, is_up: bool) -> float:
    """
    交易所真实涨/跌停价 = 四舍五入到分(前收 × (1 ± 实际涨跌幅限制/100))。

    2026-08-25抽出来做唯一口径。此前"涨停价"这个概念在仓库里有三种算法各自为政：
      · 涨跌停判定：round(prev*(1+(limit_pct+0.1)/100), 2) —— 对的
      · 炸板判定：  prev*(1+limit_pct/100)*0.999          —— 错的
      · 反推收盘价：Decimal + ROUND_HALF_UP                —— 对的
    第二个用的 limit_pct 是**K线判定容差**(9.90)而不是真实规则(10.0)，再乘 0.999，
    等效阈值只有 +9.79%——一只盘中最高只冲到 +9.85%、从没碰过涨停价的股票会被
    判成"炸板"。而炸板在风险分里是 近3日每次 +28 分、龙头分里 -12 分，是全仓库
    单笔权重最大的惩罚项之一，这个 0.2 个百分点的宽松带正好覆盖"冲高回落"这类
    最常见形态，误判代价很高。
    用 Decimal + ROUND_HALF_UP 贴近交易所"四舍五入"惯例（Python 内置 round() 是
    银行家舍入，5 会向偶数靠，跟交易所规则不一致）。
    """
    from decimal import Decimal, ROUND_HALF_UP

    signed = actual_limit_pct if is_up else -actual_limit_pct
    prev_d = Decimal(str(prev_close))
    factor = Decimal("1") + Decimal(str(signed)) / Decimal("100")
    return float((prev_d * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def build_kline_bar(
    *, dt: date, open_p: float, close_p: float, high_p: float, low_p: float,
    pct: float, turnover: Optional[float], is_st: bool = False, limit_pct: float = 9.90,
    prev_close: float | None = None,
) -> KLineBar:
    """
    由一组 OHLC + 涨跌幅 + 换手率构造一根 KLineBar，含涨跌停/炸板/一字板判定。

    2026-08-25 从 _parse_kline_bar 里抽出来共用：kline_bar_from_quote() 需要用
    实时行情补一根"今日"K线（K线接口当日拉取失败时的兜底），这两条路径必须用
    完全同一套涨跌停判定，否则同一只股票会因为"这根bar是从哪个源来的"得出不同
    的涨停结论——正是这种脱节造成了凯莱英那次生产事故。

    prev_close 传入时直接用（实时行情有权威昨收字段）；传 None 时按老办法从
    close/pct 反推（K线接口不给昨收，只能这么算）。
    """
    # ── 使用实际价格判断涨跌停（比单纯 pct 阈值更准确）────────────────────────
    # 交易所规则：涨跌停价 = round(前收盘 × (1 ± board_limit/100), 2)
    # pct 由东方财富 API 返回，精确到 2 位小数，据此反推前收盘近似值。
    # actual_limit = limit_pct + 0.1：9.90→10.0, 19.90→20.0, 29.90→30.0（ST走同板块
    # 阈值，2026-07-06新规后不再单独更小，这里不需要再为ST单独举例）
    if prev_close is None and close_p > 0 and abs(pct) < 99.9:
        prev_close = close_p / (1 + pct / 100)   # K线接口不给昨收，只能反推
    actual_limit = limit_pct + 0.1
    lu_price = None
    if prev_close and prev_close > 0 and close_p > 0:
        lu_price = exact_limit_price(prev_close, actual_limit, is_up=True)
        ld_price = exact_limit_price(prev_close, actual_limit, is_up=False)
        # 容差 0.005：规避浮点取整（如 51.699 vs 涨停价 51.70）导致漏判
        is_lu = close_p >= lu_price - 0.005
        is_ld = close_p <= ld_price + 0.005
    else:
        prev_close = 0.0
        is_lu = pct >= limit_pct
        is_ld = pct <= -limit_pct

    # 炸板判断：盘中最高价确实触及了真实涨停价，但收盘没封住。
    # 2026-08-25修正：此前这里用的是 prev*(1+limit_pct/100)*0.999，即判定容差
    # 9.90% 再打个 0.999 折 ≈ +9.79%，比真实涨停价低了 0.2 个百分点，冲高到
    # +9.8% 从没碰过涨停板的股票会被误判炸板（详见 exact_limit_price 注释）。
    # 现在跟涨停判定共用同一个 lu_price，同一个事实只有一套价格口径。
    if not is_st and not is_lu and lu_price is not None:
        is_broken = high_p >= lu_price - 0.005
    else:
        is_broken = False

    # 一字板：涨停且全天最低价未跌破涨停价（涨停时收盘=涨停价，low>=close 即全天一字）；
    # 跌停对称：全天最高价未涨破跌停价（high<=close）
    is_one_word_up = is_lu and low_p > 0 and low_p >= close_p - 0.005
    is_one_word_down = is_ld and high_p > 0 and high_p <= close_p + 0.005

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
        is_one_word_limit_up=is_one_word_up,
        is_one_word_limit_down=is_one_word_down,
    )


def kline_bar_from_quote(
    quote: "StockQuote", code: str, is_st: bool, expect_date: date,
) -> "KLineBar | None":
    """
    用实时行情快照构造 expect_date 这一天的 KLineBar，给"K线接口当日那一根拉不到"
    做定向兜底。无法可信构造时返回 None。

    2026-08-25新增。此前 daily_update 遇到今日K线拉取失败是"降级用历史"——直接拿
    上一根旧bar当今天用，于是收盘价/涨幅/换手率/连板数/各种评分全都是旧的却盖着
    今天的日期（凯莱英那次生产事故的根因）。补一根真的当日bar比事后再去修某几个
    字段干净得多：窗口统计、龙头分、连板数这些全部自动算对，不需要在下游一个个
    字段打补丁。

    严格拒绝的三种情况（宁可没有，不要假的）：
      1. quote.trade_date != expect_date —— 包括 None（数据源没给日期）。不校验
         日期就等于用一个可能同样过期的源去修另一个过期的源。
      2. 没有可信现价或昨收 —— 涨跌停判定完全依赖昨收，缺了只能瞎猜。
      3. 停牌（价格<=0）。

    换手率一律置 None，**即使腾讯这一路的行情其实带了真实换手率**（2026-08-25
    的刻意决定）。理由：这根bar是在顶替K线接口那一根，而当前K线主力源（腾讯/
    新浪）本来就不提供换手率、全市场每只股票的换手率长期都是缺失的。如果只有
    "K线拉取失败、走了行情兜底"的这少数几只带上真实换手率，它们就会凭空多拿到
    情绪分里的 `turnover*0.8`（高换手股可达+16）和龙头分里的 turnover_bonus
    (+5)，等于"数据源恰好走了哪条路"变成了打分优势——龙头分/情绪分是拿来做
    横向排序和板块均值的，来源不一致造成的系统性偏差比少一个因子更有害，而且
    "拉取失败反而加分"这个方向本身就说不通。
    换手率要恢复成一个真正参与打分的因子，应该是给**所有**候选统一补齐，那是
    一个会改变全市场评分分布的产品决策，不能顺手夹带在一次数据契约修复里。
    """
    if quote.trade_date != expect_date:
        return None
    price, prev_close = quote.price, quote.prev_close
    if not price or price <= 0 or not prev_close or prev_close <= 0:
        return None
    pct = quote.pct_change
    if pct is None:
        pct = round((price - prev_close) / prev_close * 100, 2)
    return build_kline_bar(
        dt=expect_date,
        open_p=quote.open if quote.open and quote.open > 0 else price,
        close_p=price,
        # 高开低价缺失时退回现价：会让炸板/一字板判定偏保守（不会误报），
        # 比用0导致 is_one_word 恒假、is_broken 乱判要安全。
        high_p=quote.high if quote.high and quote.high > 0 else price,
        low_p=quote.low if quote.low and quote.low > 0 else price,
        pct=pct,
        turnover=None,          # 见上面 docstring：刻意不带，避免来源相关的打分偏差
        is_st=is_st,
        limit_pct=get_limit_pct(code, is_st),
        prev_close=prev_close,
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
    涨跌幅由相邻两根 K 线的收盘价推算；换手率该接口不提供，置 None（不是0——
    见 KLineBar 的 docstring）。

    2026-08-25：涨跌停/炸板/一字板判定改为调用公用的 build_kline_bar()，不再自己
    抄一份。此前东财/腾讯/新浪三个解析器各写了一套几乎相同的判定代码，而腾讯是
    当前主力源——意味着以后任何一次判定规则调整如果只改了公用函数，主力源反而
    改不到，同一只股票会因为"这根bar来自哪个源"得出不同的涨停结论。凯莱英那次
    事故的本质就是同一个事实在不同来源之间脱节，不能在这里再留一个同样的口子。
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

        # prev_close 显式传 0.0（不是 None）：第一根没有前置bar，pct 是我们自己填的
        # 0.0 而不是真实涨跌幅，交给 build_kline_bar 反推会得出 prev_close=close 的
        # 假前收。传 0.0 表示"确实拿不到前收"，走保守的百分比阈值分支，跟改造前
        # 这两个解析器的行为完全一致。
        bars.append(build_kline_bar(
            dt=dt, open_p=open_p, close_p=close_p, high_p=high_p, low_p=low_p,
            pct=pct, turnover=None, is_st=is_st, limit_pct=limit_pct,
            prev_close=prev_close,
        ))
    return bars


def _fetch_kline_eastmoney(
    code: str, market: int, days: int, is_st: bool, limit_pct: float, timeout: int
) -> List[KLineBar]:
    """东方财富历史 K 线（含换手率）"""
    from datetime import date as _date
    secid = f"{market}.{code}"
    end_date = _date.today().strftime("%Y%m%d")

    client = _thread_warmed_client(timeout=timeout)
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
    prefix = "bj" if _is_bj_code(code) else ("sh" if market == 1 else "sz")
    full_code = f"{prefix}{code}"

    with httpx.Client(headers=TENCENT_HEADERS, timeout=timeout) as client:
        resp = client.get(TENCENT_KLINE_URL, params={
            "param": f"{full_code},day,,,{days},qfq",
        })
        try:
            data = resp.json()
        except Exception as parse_err:  # noqa: BLE001
            # 生产上成片出现 JSONDecodeError，但本机复现不了（16只×30并发全绿），
            # 判断是服务器出口IP被限流。光看异常类型说明不了问题——把状态码和body
            # 前200字符打出来才能区分"403限流页/空body/格式变了"。写法跟
            # fetch_stock_quotes_batch 里东财那段诊断保持一致。
            raise ValueError(
                f"腾讯K线响应无法解析为JSON（{type(parse_err).__name__}）："
                f"HTTP {resp.status_code}，body长度={len(resp.content)}，"
                f"前200字符={resp.text[:200]!r}"
            ) from parse_err
        node = data.get("data", {}).get(full_code, {}) or {}
        # 前复权数据在 qfqday；部分股票（北交所、无复权调整的沪深股）落在 day，回退取之
        raw_bars = node.get("qfqday") or node.get("day") or []
        if not raw_bars:
            raise ValueError("腾讯财经 K 线返回空数据")
        return _parse_tencent_klines(raw_bars, is_st, limit_pct)


def _parse_sina_klines(rows: list[dict], is_st: bool = False, limit_pct: float = 9.90) -> List[KLineBar]:
    """
    解析新浪财经K线数据（quotes.sina.cn 的 CN_MarketDataService.getKLineData
    接口，指数/个股通用格式）：[{day, open, close, high, low, volume}, ...]。
    涨跌幅由相邻两根K线收盘价推算；换手率该接口不提供，置 None（不是0——见
    KLineBar 的 docstring）。涨跌停/炸板判定跟其他所有来源一样走公用的
    build_kline_bar()（2026-08-25统一，理由见 _parse_tencent_klines）。
    跟 Tencent 版的唯一区别是输入是dict不是list，并且用一个运行变量记录上一根
    有效收盘价，比直接下标回看 rows[i-1] 更稳：中间某根数据畸形被跳过时不会拿
    一个解析失败的值去算涨跌幅。
    """
    bars: List[KLineBar] = []
    prev_close = 0.0
    for i, row in enumerate(rows):
        try:
            dt = date.fromisoformat(str(row["day"]))
            open_p = float(row["open"])
            close_p = float(row["close"])
            high_p = float(row["high"])
            low_p = float(row["low"])
        except (KeyError, ValueError, TypeError):
            continue

        this_prev_close = 0.0 if i == 0 else prev_close
        pct = round((close_p - this_prev_close) / this_prev_close * 100, 2) if this_prev_close > 0 else 0.0

        bars.append(build_kline_bar(
            dt=dt, open_p=open_p, close_p=close_p, high_p=high_p, low_p=low_p,
            pct=pct, turnover=None, is_st=is_st, limit_pct=limit_pct,
            prev_close=this_prev_close,   # 0.0 = 确实拿不到前收，不要反推
        ))
        prev_close = close_p
    return bars


def _fetch_kline_sina(
    code: str, market: int, days: int, is_st: bool, limit_pct: float, timeout: int
) -> List[KLineBar]:
    """新浪财经历史K线（无换手率），跟 _fetch_kline_tencent 同级的第三个数据源。"""
    import json as _json
    prefix = "bj" if _is_bj_code(code) else ("sh" if market == 1 else "sz")
    url = (
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
        f"?symbol={prefix}{code}&scale=240&ma=no&datalen={days}"
    )
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
        text = client.get(url).text
    start, end = text.find("("), text.rfind(")")
    if start < 0 or end <= start:
        # 同上：把实际响应打出来，否则"格式异常"四个字定位不了任何东西
        raise ValueError(
            f"新浪K线返回格式异常：body长度={len(text)}，前200字符={text[:200]!r}"
        )
    rows = _json.loads(text[start + 1: end])
    if not rows:
        raise ValueError("新浪财经 K 线返回空数据")
    return _parse_sina_klines(rows, is_st, limit_pct)


def fetch_kline(
    code: str,
    market: int,
    days: int = 65,
    is_st: bool = False,
    timeout: int = 15,
) -> List[KLineBar]:
    """
    拉取单股日线 K 线数据。2026-08-23起：腾讯/新浪并列主力（先试腾讯，失败试
    新浪），东财push2his降级为两者都失败时的最后兜底——push2/push2his这一系
    域名生产环境被持续限流（详见弱转强雷达行情拉取同一次诊断），不再默认
    优先打它。单只股票场景下顺序尝试即可，不需要并发（批量场景的"两路并发
    分摊"在 fetch_klines_batch 里做）。
    days=65 保证能算出近60日指标（留5日冗余）。
    涨跌停幅度根据股票代码自动判断（主板±10%，科创板/创业板±20%）。
    """
    lp = get_limit_pct(code, is_st)
    try:
        return _fetch_kline_tencent(code, market, days, is_st, lp, timeout)
    except Exception as e:  # noqa: BLE001
        print(f"[fetcher] 个股 {market}.{code} 腾讯K线失败，改试新浪: {type(e).__name__}", flush=True)

    try:
        return _fetch_kline_sina(code, market, days, is_st, lp, timeout)
    except Exception as e:  # noqa: BLE001
        print(f"[fetcher] 个股 {market}.{code} 新浪K线也失败，改试东财兜底: {type(e).__name__}", flush=True)

    try:
        return _fetch_kline_eastmoney(code, market, days, is_st, lp, timeout)
    except Exception as e:
        print(f"[fetcher] K 线拉取最终失败 ({market}.{code}): {e}")
        return []


def _fetch_kline_group(
    stocks: List[StockBasicInfo], days: int, max_workers: int, delay_between: float,
    source_fn: Callable, source_label: str,
) -> Dict[str, List[KLineBar]]:
    """薄封装：一组股票并发跑同一个数据源，单只失败就返回空列表（不拖垮整组），
    调用方按 not results.get(code) 判定这只股票是否需要换源兜底。"""
    group_results: Dict[str, List[KLineBar]] = {}
    if not stocks:
        return group_results

    def _fetch_one(stock: StockBasicInfo) -> Tuple[str, List[KLineBar]]:
        lp = get_limit_pct(stock.code, stock.is_st)
        try:
            bars = source_fn(stock.code, stock.market, days, stock.is_st, lp, 15)
        except Exception as e:  # noqa: BLE001
            print(f"[fetcher] 个股 {stock.market}.{stock.code} {source_label}K线失败: {type(e).__name__}", flush=True)
            bars = []
        if delay_between > 0:
            time.sleep(delay_between)
        return stock.code, bars

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(stocks)))) as executor:
        futures = {executor.submit(_fetch_one, s): s for s in stocks}
        for future in as_completed(futures):
            try:
                code, bars = future.result()
                group_results[code] = bars
            except Exception as e:  # noqa: BLE001
                stock = futures[future]
                print(f"[fetcher] 批量拉取失败 ({stock.code}): {e}")
                group_results[stock.code] = []
    return group_results


def fetch_klines_batch(
    stocks: List[StockBasicInfo],
    days: int = 65,
    max_workers: int = 5,
    delay_between: float = 0.1,
) -> Dict[str, List[KLineBar]]:
    """
    并发批量拉取多只股票的 K 线。返回 {code: [KLineBar, ...]}。

    2026-08-23起：候选轮询拆两组，分别用腾讯/新浪并发拉取（组内仍按
    max_workers 并发多只股票）。2026-08-26 增加**交叉兜底**：本组主力失败的股票
    先去试另一个主力源（分到腾讯的试新浪，反之亦然），两个主力源都拿不到才交给
    东财push2his 兜底一次（单跳，不递归）——push2/push2his 这一系域名生产环境被持续限流
    （详见弱转强雷达行情拉取的同一次诊断，K线专属指数早在更早前就已确认
    "长期被针对性限流"，见 fetch_index_kline），不再让它做主力，只留兜底
    角色。腾讯/新浪都没有换手率字段，东财兜底成功的那部分股票换手率能补回，
    走腾讯/新浪主力的则跟弱转强雷达的行情场景一样，换手率诚实缺失，不编造。
    """
    if not stocks:
        return {}

    groups = _split_round_robin(stocks, 2)
    tencent_group, sina_group = groups[0], groups[1]

    results: Dict[str, List[KLineBar]] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {}
        if tencent_group:
            futures[executor.submit(
                _fetch_kline_group, tencent_group, days, max_workers, delay_between,
                _fetch_kline_tencent, "腾讯",
            )] = "tencent"
        if sina_group:
            futures[executor.submit(
                _fetch_kline_group, sina_group, days, max_workers, delay_between,
                _fetch_kline_sina, "新浪",
            )] = "sina"
        for future in as_completed(futures):
            results.update(future.result())

    # ── 交叉兜底：分到腾讯的失败了去试新浪，反之亦然（2026-08-26新增）──────────
    # 原来是"本组主力失败 → 直接跳东财"。问题是东财 push2his 生产环境长期被针对性
    # 限流（RemoteProtocolError 成片），而**另一个主力源当时往往是好的**——它只是
    # 恰好没被分到这只股票而已。生产日志实测：002078 分在腾讯组，腾讯
    # JSONDecodeError → 直接撞东财 RemoteProtocolError → 失败，全程没试过新浪。
    # 那次 169 只里有 106 只（63%）最后是靠实时行情补当日bar 救回来的，代价是换手率
    # 缺失、且只补得到今天这一根。先把另一个健康主力源试完再谈兜底。
    cross_pairs = []
    tencent_codes = {s.code for s in tencent_group}
    retry_on_sina = [s for s in stocks if not results.get(s.code) and s.code in tencent_codes]
    retry_on_tencent = [s for s in stocks if not results.get(s.code) and s.code not in tencent_codes]
    if retry_on_sina:
        cross_pairs.append((retry_on_sina, _fetch_kline_sina, "新浪(交叉兜底)"))
    if retry_on_tencent:
        cross_pairs.append((retry_on_tencent, _fetch_kline_tencent, "腾讯(交叉兜底)"))
    if cross_pairs:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futs = [
                executor.submit(_fetch_kline_group, grp, days, max_workers, delay_between, fn, label)
                for grp, fn, label in cross_pairs
            ]
            for f in as_completed(futs):
                for code, bars in f.result().items():
                    if bars:
                        results[code] = bars

    # 两个主力源都拿不到才轮到东财（单跳，不递归）
    missing_stocks = [s for s in stocks if not results.get(s.code)]
    if missing_stocks:
        em_results = _fetch_kline_group(
            missing_stocks, days, max_workers, delay_between, _fetch_kline_eastmoney, "东财",
        )
        results.update(em_results)

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

# 选股关键词：当日成交额最大的一批股票（用于成交额概览页面）
TURNOVER_POOL_KEYWORD = "成交额排序前60；成交额大于20亿"

# 涨停板块雷达「板块核心召回」用（2026-08-25新增）。跟 limit_up_radar_service 里
# 本地重算的那组阈值一一对应，但由东财服务端计算——它不依赖本仓库快照历史的完整性。
#
# 为什么需要这一路：本地重算是从 stock_daily_snapshots 数出涨停日，而快照只在股票
# 进候选池那天才写。生产实测10只核心股，快照覆盖率94.3%，2只有缺口（600664哈药股份
# 近60日真实涨停9次、快照里只有6次，漏了07-10/13/14三天）。缺口会让重算偏低，
# 而偏低意味着**漏召回**——对这个功能来说比多召回严重得多。
# 这一路只用来兜底"该不该出现在核心区"，展示的次数仍用本地重算值（可验证的下界）。
# 跟 STRONG_POOL_KEYWORD 的关系（2026-08-25 明确，别再问要不要合并）：
#   两者都是在找"强势/龙头"，但**范围和松紧是有意不同的，不能合并**：
#     强势股池：主板非ST、非新股次新；严阈值（60日≥10次/10日≥5次/最高连板≥4）。
#               进池代价大——每天要算K线、评分、写快照，是系统的重资源跟踪对象。
#     核心召回：不限主板（雷达的板块里有大量创业板/科创板股）；宽阈值。
#               代价只是板块卡上多一行，而"漏掉板块核心"是这个功能最不能接受的失败。
#   合并只能二选一：用严阈值会漏核心，用宽阈值会让强势股池膨胀好几倍。
#   两者的包含关系改用**本地条件**保证（recall_core_roles 里 stock.in_strong_pool
#   直接算一条召回理由），这样强势股池怎么改，核心召回都自动覆盖它，不需要在两个
#   自然语言 prompt 之间维护同步——那才是真正会漂移的地方。
# 末尾三个"涨幅大于-100%"是**恒真条件**，加它们不是为了筛选，而是为了让东财在
# 结果里带回 INTERVAL_CHG{...|10/20/60|天} 这几列——这个接口的返回列是跟着条件走的，
# 没提到的指标就不会出现在 payload 里。选股结果不受影响（跌幅不可能超过100%）。
CORE_RECALL_KEYWORD = (
    "非ST；非退市股票；"
    "近10个交易日涨停天数大于等于2或者"
    "近20个交易日涨停天数大于等于3或者"
    "近60个交易日涨停天数大于等于5或者"
    "近60个交易日最高连板数大于等于3；"
    "近10个交易日涨幅大于-100%；近20个交易日涨幅大于-100%；近60个交易日涨幅大于-100%"
)

_F10_HEADERS = {
    "Referer": "https://emweb.securities.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def fetch_stock_bk_codes(code: str, client: "httpx.Client | None" = None) -> list[str]:
    """
    通过东财 emweb F10 接口获取个股所属板块代码列表。
    返回 ["BK0665", "BK0940", ...] 格式，与 sectors.code 字段直接对应。
    失败时返回空列表，不阻断主流程。

    调用方在并发（ThreadPoolExecutor）批量查询多只股票时应传入共享的
    httpx.Client（预设 limits 匹配并发数），避免每只股票各开一条新连接
    重复走 TLS 握手——httpx.Client 对多线程并发调用是线程安全的。
    """
    mkt = "SH" if code.startswith(("6", "5", "9")) else "SZ"
    url = (
        f"https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax"
        f"?code={mkt}{code}"
    )
    try:
        if client is not None:
            resp = client.get(url, headers=_F10_HEADERS, timeout=10)
        else:
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


def _parse_limit_dir(item: dict) -> "str | None":
    """
    从选股 API 条目解析涨跌停方向，返回 'up' | 'down' | None。
    优先用显式字段 IS_LIMIT_UP/IS_LIMIT_DOWN（值 '涨停'/'跌停'，字段名带日期后缀），
    缺失/异常时回退 CHG（涨跌幅）符号。
    """
    lu_key = next((k for k in item if k.startswith("IS_LIMIT_UP")), None)
    ld_key = next((k for k in item if k.startswith("IS_LIMIT_DOWN")), None)
    if lu_key and str(item.get(lu_key)) == "涨停":
        return "up"
    if ld_key and str(item.get(ld_key)) == "跌停":
        return "down"
    try:
        chg = float(item.get("CHG"))
        return "up" if chg > 0 else ("down" if chg < 0 else None)
    except (TypeError, ValueError):
        return None


def _parse_limit_date(item: dict) -> "date | None":
    """
    从选股 API 条目的日期后缀字段（如 'IS_LIMIT_DOWN{2026-06-03}'）解析数据日期。
    用于校验 API 数据日期与快照 target_date 是否一致（避免盘前等场景错配）。
    """
    for k in item:
        if k.startswith(("IS_LIMIT_UP", "IS_LIMIT_DOWN", "IFSTSTOCK", "FIRST_LIMITUP")):
            l, r = k.find("{"), k.find("}")
            if 0 <= l < r:
                try:
                    return date.fromisoformat(k[l + 1:r])
                except ValueError:
                    continue
    return None


def fetch_strong_pool_codes(
    xc_id: str = "xc11bd34d6790101033c",
    fingerprint: str = "a3b5b577646954c0a1ff47146894e3d1",
    keyword: str = STRONG_POOL_KEYWORD,
    page_size: int = 50,
    with_names: bool = False,
    with_detail: bool = False,
    with_raw: bool = False,
) -> "Set[str] | dict | list":
    """
    调用东方财富智能选股 search-code 接口。自动分页直到拉取全部结果。

    返回：
      默认             → 股票代码集合 Set[str]（向后兼容）
      with_names=True  → {code: name}（name 取 SECURITY_SHORT_NAME）
      with_detail=True → {code: {"name": str, "limit_dir": "up"|"down"|None}}

    数据完整性：分页过程中任一页请求失败 → 视为本次 API 不可用，返回空
      （空 set / 空 dict）。绝不返回「部分结果」被误当作权威全集——否则会
      漏判涨跌停并触发错误的对账清除。调用方据空结果回退 DB。

    注：选股关键词均含「非ST」→ 出现在结果中的股票当日均为非 ST，
        调用方可据此把 is_st 刷新为 False（摘帽场景自动修正）。
    """
    custom_data = f'[{{"type":"text","value":"{keyword}","extra":""}}]'
    details: dict[str, dict] = {}
    raw_items: list[dict] = []
    page_no = 1
    total: int | None = None
    complete = True

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
            print(f"[fetcher] 选股 API 第 {page_no} 页失败: {e}（本次视为不可用，回退DB）")
            complete = False
            break

        result = data.get("data", {}).get("result", {})
        data_list = result.get("dataList", [])

        if total is None:
            total = result.get("total", len(data_list))

        for item in data_list:
            code = item.get("SECURITY_CODE", "").strip()
            if code:
                details[code] = {
                    "name": (item.get("SECURITY_SHORT_NAME") or "").strip(),
                    "limit_dir": _parse_limit_dir(item),
                    "limit_date": _parse_limit_date(item),
                }
                raw_items.append(item)

        if not data_list or len(details) >= (total or 0):
            break
        page_no += 1
        time.sleep(0.3)

    # 分页未完整拉完 → 视为不可用，返回空，避免「部分结果」被当作权威全集
    if not complete:
        return [] if with_raw else ({} if (with_names or with_detail) else set())
    if with_raw:
        return raw_items
    if with_detail:
        return details
    if with_names:
        return {c: d["name"] for c, d in details.items()}
    return set(details)


@dataclass
class CoreRecallDetail:
    """
    东财条件选股回传的"近期活跃度"事实（涨停板块雷达核心召回用），2026-08-25新增。
    全部由东财服务端实时计算，**不依赖本仓库快照历史的完整性**。
    """
    code: str
    name: str = ""
    limit_up_days_10d: Optional[int] = None
    limit_up_days_20d: Optional[int] = None
    limit_up_days_60d: Optional[int] = None
    max_board_60d: Optional[int] = None
    # 区间涨幅（INTERVAL_CHG）。**是真实复合区间收益**，跟 compute_window_stats 里的
    # pct_change_Nd（日涨幅简单相加的近似）不是同一个算法：2026-08-25 用 603580
    # 艾艾精工核对，近60日真实区间涨幅204.85%、日涨幅相加只有123.14%，东财报208.09%
    # 跟真实值吻合。大涨股票上这个差距非常大，展示时别跟活跃股池那几列混为一谈。
    interval_chg_10d: Optional[float] = None
    interval_chg_20d: Optional[float] = None
    interval_chg_60d: Optional[float] = None
    pct_change: Optional[float] = None      # 今日涨跌幅（CHG）
    turnover_rate: Optional[float] = None
    price: Optional[float] = None


def _parse_core_recall_item(item: dict) -> Optional[CoreRecallDetail]:
    """
    解析选股结果里的活跃度字段。字段名带动态日期区间，必须按前缀 + 窗口天数匹配，
    不能写死完整键名：
      'DURATION_LIMIT_UP{2026-08-12|2026-08-25|10|天}' = 1   近10日涨停天数
      'DURATION_LIMIT_UP{2026-07-29|2026-08-25|20|天}' = 6   近20日涨停天数
      'DURATION_LIMIT_UP{2026-06-02|2026-08-25|60|天}' = 13  近60日涨停天数
      '区间最高连板[2026-06-02至2026-08-25]'            = 9.00 近60日最高连板
    2026-08-25 用 002432/600664/002437 三只股票跟真实K线逐项核对，**三只全对**，
    包括本地快照重算会漏掉3天的 600664（东财60日=9，快照重算只有6）。
    """
    code = (item.get("SECURITY_CODE") or "").strip()
    if not code:
        return None

    def _i(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    d = CoreRecallDetail(code=code, name=(item.get("SECURITY_SHORT_NAME") or "").strip())
    for key, val in item.items():
        if key.startswith("DURATION_LIMIT_UP{"):
            parts = key.split("|")
            window = parts[2] if len(parts) > 2 else None
            if window == "10":
                d.limit_up_days_10d = _i(val)
            elif window == "20":
                d.limit_up_days_20d = _i(val)
            elif window == "60":
                d.limit_up_days_60d = _i(val)
        elif key.startswith("INTERVAL_CHG{"):
            parts = key.split("|")
            window = parts[2] if len(parts) > 2 else None
            if window == "10":
                d.interval_chg_10d = _f(val)
            elif window == "20":
                d.interval_chg_20d = _f(val)
            elif window == "60":
                d.interval_chg_60d = _f(val)
        elif key.startswith("区间最高连板"):
            d.max_board_60d = _i(val)
    d.pct_change = _f(item.get("CHG"))
    d.turnover_rate = _f(item.get("TURNOVER_RATE"))
    d.price = _f(item.get("NEWEST_PRICE"))
    return d


def fetch_core_recall_details(
    xc_id: str = "xc11bd34d6790101033c",
    fingerprint: str = "a3b5b577646954c0a1ff47146894e3d1",
    keyword: str = CORE_RECALL_KEYWORD,
    page_size: int = 50,
) -> Dict[str, CoreRecallDetail]:
    """
    拉取核心召回名单 + 每只股票的近期活跃度事实。分页任一页失败即整体判不可用返回
    空（跟其它选股函数一致，绝不返回部分结果被当成权威全集）。
    """
    raw = fetch_strong_pool_codes(
        xc_id=xc_id, fingerprint=fingerprint, keyword=keyword,
        page_size=page_size, with_raw=True,
    )
    out: Dict[str, CoreRecallDetail] = {}
    for item in raw:
        d = _parse_core_recall_item(item)
        if d:
            out[d.code] = d
    return out


def _parse_cn_amount(s: str) -> float:
    """把 "675.11亿"/"7232.30万"/纯数字字符串统一解析成元。解析失败返回 0.0。"""
    s = (s or "").strip()
    if not s:
        return 0.0
    try:
        if s.endswith("亿"):
            return float(s[:-1]) * 1e8
        if s.endswith("万"):
            return float(s[:-1]) * 1e4
        return float(s)
    except ValueError:
        return 0.0


def fetch_turnover_top_stocks(keyword: str, page_size: int = 60) -> list[dict]:
    """
    调用东方财富智能选股 search-code 接口（同 fetch_strong_pool_codes 的接口/
    分页逻辑），用于「成交额排序前N」这类 prompt。选股 API 返回的每条数据本身
    就带涨跌幅/成交额/换手率等字段，不需要再对每只股票单独拉K线/快照补数据。

    返回按 API 原始顺序（即已按成交额降序）排列的
    [{"code","name","pct_change","amount","turnover_rate","market"}, ...]。
    分页过程中任一页失败 → 视为本次不可用，返回空列表（同 fetch_strong_pool_codes
    的完整性约束，不把「部分结果」当作权威全集）。
    """
    custom_data = f'[{{"type":"text","value":"{keyword}","extra":""}}]'
    out: list[dict] = []
    seen: set[str] = set()
    page_no = 1
    total: Optional[int] = None

    while True:
        ts = str(int(time.time() * 1_000_000))
        rid = "".join(random.choices(string.ascii_letters, k=32)) + str(int(time.time() * 1000))
        body = {
            "needAmbiguousSuggest": True,
            "pageSize": page_size,
            "pageNo": page_no,
            "fingerprint": "a3b5b577646954c0a1ff47146894e3d1",
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
            "xcId": "xc11bd34d6790101033c",
            "gids": [],
            "dxInfoNew": [],
            "keyWordNew": keyword,
            "customDataNew": custom_data,
        }
        try:
            resp = httpx.post(STRONG_POOL_SEARCH_URL, headers=_SEARCH_HEADERS, json=body, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            print(f"[fetcher] 成交额选股 API 第 {page_no} 页失败: {e}（本次视为不可用）", flush=True)
            return []

        result = data.get("data", {}).get("result", {})
        data_list = result.get("dataList", [])
        if total is None:
            total = result.get("total", len(data_list))

        for item in data_list:
            code = (item.get("SECURITY_CODE") or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            out.append({
                "code": code,
                "name": (item.get("SECURITY_SHORT_NAME") or "").strip(),
                "pct_change": float(item.get("CHG") or 0.0),
                "amount": _parse_cn_amount(item.get("TRADING_VOLUMES")),
                "turnover_rate": float(item.get("TURNOVER_RATE") or 0.0),
                "market": (item.get("MARKET_SHORT_NAME") or "").strip(),
            })

        if not data_list or len(out) >= (total or 0):
            break
        page_no += 1
        time.sleep(0.3)

    return out


# 涨跌停选股关键词
LIMIT_MOVE_KEYWORD = "非ST；非退市股票；涨停股票或者跌停股票"


def fetch_limit_move_codes(
    xc_id: str = "xc11bd34d6790101033c",
    fingerprint: str = "a3b5b577646954c0a1ff47146894e3d1",
    keyword: str = LIMIT_MOVE_KEYWORD,
    page_size: int = 50,
    with_names: bool = False,
    with_detail: bool = False,
    with_raw: bool = False,
) -> "Set[str] | dict | list":
    """
    调用东方财富智能选股 API，获取今日涨停 + 跌停的非ST非退市股票。
    with_names=True → {code: name}；with_detail=True → {code: {name, limit_dir}}；
    否则返回代码集合。替代原来扫描全量 5206 只股票再过滤涨跌停的逻辑。
    """
    # 直接复用 fetch_strong_pool_codes 的实现，只换 keyword
    return fetch_strong_pool_codes(
        xc_id=xc_id,
        fingerprint=fingerprint,
        keyword=keyword,
        page_size=page_size,
        with_names=with_names,
        with_detail=with_detail,
    )


# ─── 异常波动 / 重点监控（严重异常波动 UNUSUAL_TYPE=002）─────────────────────────
REGULATORY_UNUSUAL_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

_REGULATORY_COLUMNS = (
    "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,UNUSUAL_TYPE,START_DATE,END_DATE,"
    "INFO_CODE,NOTICE_DATE,UNUSUAL_REASON,UNUSUAL_REASON_TYPE,MRAKET_TYPE,"
    "PREDICT_START_DATE,PREDICT_END_DATE,IS_HIS"
)


def fetch_regulatory_unusual(is_his: str = "0", page_size: int = 500) -> list[dict]:
    """
    拉取交易所「严重异常波动 / 重点监控」名单（UNUSUAL_TYPE=002）。
    is_his="0" 当前在监管；"1" 历史。自动翻页。

    数据完整性：任一页失败 → 返回空列表（视为本次不可用），调用方据此保留旧数据、
    不做清除，避免「部分结果」被误当作全集。
    """
    items: list[dict] = []
    page_no = 1
    pages: int | None = None
    complete = True

    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20) as client:
        while True:
            params = {
                "pageNumber": page_no,
                "pageSize": page_size,
                "sortColumns": "NOTICE_DATE,END_DATE",
                "sortTypes": "-1,-1",
                "source": "SECURITIES",
                "client": "APP",
                "reportName": "RPT_APP_UNUSUALBASIC",
                "columns": _REGULATORY_COLUMNS,
                "quoteColumns": "",
                "filter": f'(UNUSUAL_TYPE="002")(IS_HIS="{is_his}")',
            }
            try:
                resp = client.get(REGULATORY_UNUSUAL_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[fetcher] 重点监管名单第 {page_no} 页失败: {e}（本次视为不可用）")
                complete = False
                break

            result = data.get("result") or {}
            page_data = result.get("data") or []
            if pages is None:
                pages = int(result.get("pages") or 1)

            items.extend(page_data)

            if not page_data or page_no >= (pages or 1):
                break
            page_no += 1
            time.sleep(0.2)

    if not complete:
        return []
    return items


WATCH_UNUSUAL_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_WATCH_COLUMNS = (
    "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,MARKET_CODE,CHANGE_RATE,"
    "MAX_DAYS,DEVUATION_VALUE,CHANGE_RATE_TARGET,IS_HAPPEN,UNUSUAL_TYPE"
)


def fetch_watch_unusual_fluctuate(page_size: int = 500, timeout: int = 20) -> list[dict]:
    """
    东财官方「严重异动预警」(RPT_WATCH_UNUSUAL_FLUCTUATE)——预计算的累计偏离值与逼近度。
    含字段：DEVUATION_VALUE(累计偏离值)、MAX_DAYS(天数)、CHANGE_RATE_TARGET(今日还需涨跌%)、
    IS_HAPPEN(0未触发/1已触发)、UNUSUAL_TYPE(规则原文)。
    按 TRADE_DATE 降序取最新交易日那批；失败返回空。
    """
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
            resp = client.get(WATCH_UNUSUAL_URL, params={
                "reportName": "RPT_WATCH_UNUSUAL_FLUCTUATE",
                "columns": _WATCH_COLUMNS,
                "pageNumber": 1,
                "pageSize": page_size,
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
                "source": "SECURITIES",
                "client": "APP",
            })
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        print(f"[fetcher] 严重异动预警接口失败: {e}")
        return []

    rows = (data.get("result") or {}).get("data") or []
    if not rows:
        return []
    # 仅保留最新交易日那批（已按 TRADE_DATE 降序）
    latest = (rows[0].get("TRADE_DATE") or "")[:10]
    return [r for r in rows if (r.get("TRADE_DATE") or "")[:10] == latest]


PRICE_ANOMALY_URL = "https://dycalchis.eastmoney.com/price-anomaly/list"


def fetch_price_anomaly_list(page_size: int = 400, timeout: int = 15) -> tuple[list[dict], bool]:
    """
    东财实时「严重异动预测」(dycalchis)。返回 (列表, is_open)。
    字段：c=代码 n=名称 m=市场 x=累计偏离值 d=天数 t=今日还需涨跌幅% a=今日涨跌幅%
      e=规则(4:10日+100% 5:10日-50% 6:30日+200% 7:30日-70%)
      o=状态（盘后 open=0：2=将触发/0=消退；盘中 open=1：1=已触发/0=未触发）
    is_open：True=盘中（o 不区分将触发，需用接近度判定）。失败返回 ([], False)。
    """
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
            resp = client.get(PRICE_ANOMALY_URL, params={
                "team": "h5", "product": "EastMoney", "client": "WAP",
                "version": "9001", "name": "WAP", "user": "12",
                "pageSize": page_size, "pageNo": 1, "sortKey": 0, "sortDir": 0,
            })
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        print(f"[fetcher] 实时严重异动预测接口失败: {e}")
        return [], False
    return (data.get("data") or [], bool(data.get("open")))


QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"


def _fetch_index_amount_sina(secid: str, timeout: int = 10) -> Optional[float]:
    """
    指数当日成交额（元）——新浪 hq.sinajs.cn 实时行情接口，跟东财完全独立的
    数据源/服务器。格式：var hq_str_sh000001="名称,今开,昨收,现价,最高,最低,
    买一,卖一,成交量,成交额,...";（跟 _batch_sina_pct_change 用的是同一接口）。
    失败返回 None。
    """
    market, _, code = secid.partition(".")
    prefix = "bj" if code.startswith("899") else ("sh" if market == "1" else "sz")
    key = f"{prefix}{code}"
    try:
        with httpx.Client(headers=SINA_HEADERS, timeout=timeout) as client:
            resp = client.get(SINA_HQ_URL + key)
        content = resp.text.split('"')[1]
        fields = content.split(",")
        if len(fields) < 10:
            return None
        return float(fields[9])
    except Exception as e:  # noqa: BLE001
        print(f"[fetcher] 指数 {secid} 新浪实时成交额补数失败: {type(e).__name__}: {e}", flush=True)
        return None


def _fetch_index_amount_eastmoney(secid: str, timeout: int = 15) -> Optional[float]:
    """
    指数当日成交额（元）——东财实时行情快照接口（push2，非持续被限流的push2his
    历史K线接口），只取 f48（成交额）这一个字段。作为新浪失败时的兜底。
    """
    try:
        client = _thread_warmed_client(timeout=timeout)
        resp = client.get(QUOTE_URL, params={"secid": secid, "fields": "f48", "invt": 2, "fltt": 1})
        amt = (resp.json().get("data") or {}).get("f48")
        return float(amt) if amt is not None else None
    except Exception as e:  # noqa: BLE001
        print(f"[fetcher] 指数 {secid} 东财实时成交额补数失败: {type(e).__name__}: {e}", flush=True)
        return None


ULIST_QUOTE_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_QUOTE_FIELDS = "f2,f3,f5,f6,f8,f12,f14,f15,f16,f17,f18"
_QUOTE_BATCH_SIZE = 60  # 单次请求 secids 数量上限（实测60只没问题），超出自动分批


def _w2s_log(tag: str, msg: str) -> None:
    """
    诊断日志：追加写到 backend/logs/w2s_radar_{今天}.log，跟 scheduler.py 里
    定时刷新用的是同一个文件——不管这次刷新是定时任务触发还是用户点按钮触发，
    都写进同一份当天的日志，用户点完刷新按钮后直接 `cat`/`tail` 这一个文件
    就能拿到完整诊断信息，不用去翻 journalctl。故意不复用 scheduler.py 的
    `_log`（避免 service 层反向依赖 scheduler 模块），就地实现同样的几行。
    """
    import os
    from datetime import datetime as _dt
    try:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"w2s_radar_{date.today().isoformat()}.log")
        ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a") as f:
            f.write(f"[{ts}] [{tag}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass  # 诊断日志本身不能变成新的故障点


@dataclass
class StockQuote:
    """个股实时快照（弱转强雷达用）。全仓库此前没有任何地方暴露过当前绝对价格
    ——之前所有页面都只用涨跌幅，这是第一个真正拉「现价」的地方。

    2026-08-23起三路数据源并发/兜底后修的一个真实bug：东财原始字段单位是
    "手"、腾讯也是"手"，但新浪是"股"，三者混进同一个 volume 字段却在下游
    统一按"手"处理（*100换算股数），导致新浪来源的候选VWAP系统性缩小100倍
    被合理性校验拦掉、悄悄退化成MA5——不是随机噪音，是"只要这只股票这次
    刷新恰好分到新浪那一路，VWAP就必然丢"。现在统一规定：**volume 字段在
    这个 dataclass 里永远是"股"**，各数据源自己的解析函数负责把各自的原始
    单位换算成股，调用方（比如 _compute_vwap）不用再关心数据来自哪个源。
    """
    code: str
    name: str
    price: Optional[float] = None
    pct_change: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    prev_close: Optional[float] = None
    volume: Optional[float] = None  # 股（不是手）——见上面class docstring
    amount: Optional[float] = None
    turnover_rate: Optional[float] = None
    # 这条报价属于哪个交易日（2026-08-25新增）。新增它是因为 kline_bar_from_quote()
    # 要拿实时行情去补K线拉取失败的那一根"今日"——如果不校验行情本身的日期，
    # 就会用一个可能同样过期的数据源去"修"另一个过期数据源，等于换个门再犯一次
    # 同样的错（本仓库反复踩过的"自证式新鲜度"坑）。None = 该数据源没提供日期，
    # 一律当作不可信、不用于补K线（东财push2这一路就是None：它的字段集里没有
    # 可靠日期字段，而且生产上这一路本来就已经降级为纯兜底）。
    trade_date: Optional[date] = None


TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="


def _parse_tencent_trade_date(fields: list[str]) -> Optional[date]:
    """
    腾讯字段30是 'YYYYMMDDHHMMSS' 形式的行情时间戳（2026-08-25用实盘响应核对：
    sh600000 返回 '20260825161259'，跟当天日期一致）。取不到/格式不对返回 None，
    调用方按"日期未知=不可信"处理，不猜。
    """
    if len(fields) <= 30:
        return None
    raw = fields[30].strip()
    if len(raw) < 8 or not raw[:8].isdigit():
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def _parse_tencent_quote_line(line: str) -> Optional[Tuple[str, "StockQuote"]]:
    """
    解析腾讯 qt.gtimg.cn 单行实时行情：v_sh600000="...~"分隔的约90个字段。
    字段含义没有官方文档，下面几个下标是 2026-08-23 用真实数据跟东财权威值
    交叉核对过的（600000/002081 两只股票，价格/成交量/成交额/换手率均对得上，
    不是拍脑袋猜的位置）：
      1=名称(GBK编码，不用) 2=代码 3=现价 4=昨收 5=今开 6=成交量(手，
      单位跟东财原始f5字段一致，这里在解析层直接*100换算成股——
      StockQuote.volume 的统一约定是"股"，不能指望下游VWAP计算函数自己
      知道"这条数据是腾讯来的所以要*100、新浪来的就不用"，那样跟本仓库
      当前多源并发的架构不匹配，2026-08-23 就是因为这个不一致导致走新浪
      那一路的候选VWAP系统性缩小100倍被合理性校验拦掉、悄悄退化成MA5
      的真实bug，修复后统一在这里转换）
      33=最高 34=最低 37=成交额(万元，需*10000换算成元) 38=换手率(%，
      跟东财 turnover_rate 语义/数值完全一致，是本仓库唯一一个新浪没有
      而腾讯有的关键字段，兜底时用腾讯而不是新浪能保留这个数据不缺失)。
    """
    if not line.startswith("v_"):
        return None
    try:
        key_part = line.split("=")[0]
        raw_code = key_part.split("_", 1)[-1]  # sh600000
        pure_code = raw_code[2:]
        content = line.split('"')[1]
        fields = content.split("~")
        if len(fields) < 39:
            return None
        price = float(fields[3])
        prev_close = float(fields[4])
        open_p = float(fields[5])
        volume = float(fields[6]) * 100  # 手→股
        high = float(fields[33])
        low = float(fields[34])
        amount = float(fields[37]) * 10000
        turnover_rate = float(fields[38]) if fields[38] not in ("", "-") else None
        if price <= 0 and prev_close <= 0:
            return None  # 停牌/无数据，不构造一个全零的假报价
        pct_change = round((price - prev_close) / prev_close * 100, 2) if prev_close > 0 else None
        return pure_code, StockQuote(
            code=pure_code, name="", price=price, pct_change=pct_change,
            open=open_p, high=high, low=low, prev_close=prev_close,
            volume=volume, amount=amount, turnover_rate=turnover_rate,
            trade_date=_parse_tencent_trade_date(fields),
        )
    except (ValueError, IndexError):
        return None


def _fetch_quotes_tencent(codes_markets: list[tuple[str, int]], timeout: int = 10) -> Dict[str, StockQuote]:
    """
    腾讯 qt.gtimg.cn 批量实时行情——push2.eastmoney.com 兜底源，排在新浪之前，
    因为腾讯格式里有换手率（新浪没有），字段完整度更接近东财原始数据，兜底时
    数据损失更小。跟 _fetch_kline_tencent（K线兜底）是同一个"东财→腾讯"模式，
    只是这里是实时快照不是历史K线。
    """
    result: Dict[str, StockQuote] = {}
    if not codes_markets:
        return result
    batch_size = 60
    batches = [codes_markets[i:i + batch_size] for i in range(0, len(codes_markets), batch_size)]
    with httpx.Client(headers=TENCENT_HEADERS, timeout=timeout) as client:
        for batch in batches:
            keys = [
                f"{'bj' if _is_bj_code(code) else ('sh' if market == 1 else 'sz')}{code}"
                for code, market in batch
            ]
            try:
                resp = client.get(TENCENT_QUOTE_URL + ",".join(keys))
                for line in resp.text.splitlines():
                    parsed = _parse_tencent_quote_line(line)
                    if parsed:
                        code, quote = parsed
                        result[code] = quote
            except Exception as e:  # noqa: BLE001
                _w2s_log("QUOTE", f"腾讯兜底批量请求失败（{len(batch)}只）: {type(e).__name__}: {e}")
    return result


def _parse_sina_quote_line(line: str) -> Optional[Tuple[str, "StockQuote"]]:
    """
    解析新浪 hq.sinajs.cn 单行实时行情：var hq_str_sh600000="名称,今开,昨收,现价,
    最高,最低,竞买价,竞卖价,成交量,成交额,...(买卖五档)...,日期,时间,状态,扩展字段";
    成交量字段单位是"股"（不像东财 f5 是"手"，这里不需要 *100 换算）。新浪不
    直接提供换手率，quote.turnover_rate 留 None，不编造。名称字段是GBK编码，
    这里不使用（跟 _batch_sina_pct_change 的既有处理方式一致，只取数值字段）。
    """
    if not line.startswith("var hq_str_"):
        return None
    try:
        key_part = line.split("=")[0]
        raw_code = key_part.split("_")[-1]  # sh600000
        pure_code = raw_code[2:]
        content = line.split('"')[1]
        fields = content.split(",")
        if len(fields) < 10:
            return None
        open_p = float(fields[1])
        prev_close = float(fields[2])
        price = float(fields[3])
        high = float(fields[4])
        low = float(fields[5])
        volume = float(fields[8])
        amount = float(fields[9])
        if price <= 0 and prev_close <= 0:
            return None  # 停牌/无数据，不构造一个全零的假报价
        pct_change = round((price - prev_close) / prev_close * 100, 2) if prev_close > 0 else None
        # 字段30是行情日期（2026-08-25用实盘响应核对：sh600000 返回 '2026-08-25'）。
        # 新浪对部分标的会返回更短的行情串（没有买卖五档也就没有后面的日期），
        # 这时留 None，调用方按"日期未知=不可信"处理。
        trade_date = None
        if len(fields) > 30:
            try:
                trade_date = date.fromisoformat(fields[30].strip())
            except ValueError:
                trade_date = None
        return pure_code, StockQuote(
            code=pure_code, name="", price=price, pct_change=pct_change,
            open=open_p, high=high, low=low, prev_close=prev_close,
            volume=volume, amount=amount, turnover_rate=None,
            trade_date=trade_date,
        )
    except (ValueError, IndexError):
        return None


def _fetch_quotes_sina(codes_markets: list[tuple[str, int]], timeout: int = 10) -> Dict[str, StockQuote]:
    """
    新浪 hq.sinajs.cn 批量实时行情——兜底链路的最后一道（东财push2、腾讯都
    拿不到数据时才用）。2026-08-23 生产环境实测：push2.eastmoney.com 三个
    后端IP针对本服务器出口IP全部返回502（本地测试始终正常），但新浪/腾讯/
    东财智能选股接口（不同子域名）均正常，判断是 push2 这一个域名被针对性
    限流/防护，不是东财整体故障也不是网络链路问题。排在腾讯之后是因为新浪
    的公开行情格式没有换手率字段（腾讯有，且经真实数据核对跟东财完全一致），
    数据完整度更低，只在腾讯也失败时才用。新浪接口本身在本仓库已经用于
    涨跌幅/指数成交额查询（见 _batch_sina_pct_change/_fetch_index_amount_sina），
    是验证过的独立数据源。返回格式跟东财版本完全一致（Dict[code, StockQuote]），
    调用方不需要关心数据来自哪个源。
    """
    result: Dict[str, StockQuote] = {}
    if not codes_markets:
        return result
    prefix_map = {1: "sh", 0: "sz"}
    batch_size = 150  # 沿用 _batch_sina_pct_change 的既有批量大小
    batches = [codes_markets[i:i + batch_size] for i in range(0, len(codes_markets), batch_size)]
    with httpx.Client(headers=SINA_HEADERS, timeout=timeout) as client:
        for batch in batches:
            keys = [f"{prefix_map.get(mkt, 'sz')}{code}" for code, mkt in batch]
            try:
                resp = client.get(SINA_HQ_URL + ",".join(keys))
                for line in resp.text.splitlines():
                    parsed = _parse_sina_quote_line(line)
                    if parsed:
                        code, quote = parsed
                        result[code] = quote
            except Exception as e:  # noqa: BLE001
                _w2s_log("QUOTE", f"新浪兜底批量请求失败（{len(batch)}只）: {type(e).__name__}: {e}")
    return result


def _split_round_robin(items: list, n: int) -> list[list]:
    """轮询分组，尽量均匀——不用顺序切片，避免候选列表本身如果有板块/代码
    聚集，切片后某一路正好全是同一个板块这种偏差。"""
    groups: list[list] = [[] for _ in range(n)]
    for i, item in enumerate(items):
        groups[i % n].append(item)
    return groups


def _fetch_quotes_eastmoney(codes_markets: list[tuple[str, int]], timeout: int = 15) -> Dict[str, StockQuote]:
    """
    东财 push2 批量拉取一组代码：分批（超过 _QUOTE_BATCH_SIZE）+ 批内并发 +
    每批3次快速重试（间隔0.5-1秒，处理连接抖动这类几秒内能过去的瞬时故障）。
    3次都没拿到可解析响应的批次直接放弃、留给调用方（fetch_stock_quotes_batch）
    换别的数据源兜底——不在这里做"同源二次重试"，那属于更高层的兜底策略。
    """
    result: Dict[str, StockQuote] = {}
    if not codes_markets:
        return result
    batches = [codes_markets[i:i + _QUOTE_BATCH_SIZE] for i in range(0, len(codes_markets), _QUOTE_BATCH_SIZE)]

    def _fetch_one_batch(batch: list[tuple[str, int]], batch_label: str) -> None:
        secids = ",".join(f"{market}.{code}" for code, market in batch)
        for attempt in range(3):
            resp = None
            try:
                client = _thread_warmed_client(timeout=timeout)
                resp = client.get(ULIST_QUOTE_URL, params={"secids": secids, "fields": _QUOTE_FIELDS, "fltt": 2})
                try:
                    payload = resp.json()
                except Exception as parse_err:  # noqa: BLE001
                    _w2s_log(
                        "QUOTE",
                        f"东财批次{batch_label}第{attempt + 1}次尝试：HTTP {resp.status_code}响应无法解析为JSON"
                        f"（{type(parse_err).__name__}），Content-Length={resp.headers.get('content-length', '?')}，"
                        f"实际body长度={len(resp.content)}，body前200字符={resp.text[:200]!r}",
                    )
                    raise
                diff = ((payload.get("data") or {}).get("diff")) or []
                for row in diff:
                    code = row.get("f12")
                    if not code:
                        continue
                    raw_volume = row.get("f5")  # 原始单位"手"
                    result[code] = StockQuote(
                        code=code,
                        name=row.get("f14") or "",
                        price=row.get("f2"),
                        pct_change=row.get("f3"),
                        volume=raw_volume * 100 if raw_volume is not None else None,  # 手→股
                        amount=row.get("f6"),
                        turnover_rate=row.get("f8"),
                        high=row.get("f15"),
                        low=row.get("f16"),
                        open=row.get("f17"),
                        prev_close=row.get("f18"),
                    )
                if len(diff) < len(batch):
                    _w2s_log(
                        "QUOTE",
                        f"东财批次{batch_label}第{attempt + 1}次尝试：HTTP {resp.status_code}，"
                        f"请求{len(batch)}只只返回{len(diff)}只（部分缺席，非报错，可能是停牌/退市/代码不存在）",
                    )
                else:
                    _w2s_log("QUOTE", f"东财批次{batch_label}第{attempt + 1}次尝试：成功，{len(batch)}只全部返回")
                return
            except Exception as e:  # noqa: BLE001
                if resp is None:
                    _w2s_log(
                        "QUOTE",
                        f"东财批次{batch_label}第{attempt + 1}次尝试失败（{len(batch)}只，未收到响应）: {type(e).__name__}: {e}",
                    )
                if attempt == 2:
                    print(f"[fetcher] 东财批量行情快照拉取失败（{len(batch)}只，重试3次均失败）: {type(e).__name__}: {e}", flush=True)
                else:
                    time.sleep(0.5 * (attempt + 1))

    if len(batches) == 1:
        _fetch_one_batch(batches[0], "1")
    else:
        with ThreadPoolExecutor(max_workers=min(5, len(batches))) as executor:
            list(executor.map(lambda pair: _fetch_one_batch(pair[1], str(pair[0] + 1)), enumerate(batches)))
    return result


# 2026-08-23 二次调整：push2.eastmoney.com 在生产环境持续（跨越18:30-21:00
# 多轮测试，超过2.5小时）针对本服务器出口IP返回502，不是瞬时抖动，用户
# 明确要求把东财push2从"三路主力之一"降级为"纯兜底"——腾讯+新浪两路并发
# 做主力，东财只在腾讯/新浪都拿不到数据时才顶上（单跳，不递归重试）。
# 腾讯/新浪目前观察下来稳定，主力流量不再分给一个已知持续故障的源，避免
# 白白浪费3次重试+502的时间。东财一旦自己恢复正常，这里不需要再改代码——
# 它依然是兜底源，只是不再是主力，恢复后兜底命中率自然会体现出来。


def fetch_stock_quotes_batch(codes_markets: list[tuple[str, int]], timeout: int = 15) -> Dict[str, StockQuote]:
    """
    批量拉取个股实时快照：现价/涨跌幅/今开/最高/最低/昨收/成交量/成交额/换手率。
    codes_markets: [(code, market), ...]，market 约定同 fetch_kline：1=SH，0=SZ。

    2026-08-23 两次调整：
      1）改为多路并发分摊 + 单跳兜底（原来是"全部先打东财，东财整批失败才
         依次落到腾讯/新浪"，生产环境实测 push2.eastmoney.com 被针对性限流
         后这种"全量集中打一个源"的模式会导致该源持续拿不到数据；改成候选
         轮询拆开分别打不同源并发，某一路彻底拿不到数据时只换一个源单独
         补一次，不原地重试也不轮流试遍所有源，避免连带把兜底目标也打出
         限流）。
      2）push2.eastmoney.com 持续故障超过2.5小时（跨多轮测试确认不是瞬时
         抖动）后，东财从"三路主力之一"降级为"纯兜底"——腾讯+新浪两路
         并发做主力，东财只在两路都拿不到数据时才顶上（单跳兜底，同样不
         递归重试）。腾讯字段完整度最接近东财（含换手率，经真实数据核对
         跟东财权威值完全一致，见 _parse_tencent_quote_line），新浪没有
         换手率、完整度最低，两路都缺席时东财兜底能补回完整数据。

    调用方按 code not in result 处理缺失，不因为个别股票/个别源失败拖垮整批。
    """
    result: Dict[str, StockQuote] = {}
    if not codes_markets:
        return result

    groups = _split_round_robin(codes_markets, 2)
    sources: dict[str, tuple[list[tuple[str, int]], Callable]] = {
        "tencent": (groups[0], lambda pairs: _fetch_quotes_tencent(pairs, timeout=timeout)),
        "sina": (groups[1], lambda pairs: _fetch_quotes_sina(pairs, timeout=timeout)),
    }
    _w2s_log(
        "QUOTE",
        f"两路并发拉取行情（东财push2降级为纯兜底）：请求{len(codes_markets)}只，"
        f"腾讯{len(groups[0])}只/新浪{len(groups[1])}只",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(fn, pairs): name for name, (pairs, fn) in sources.items() if pairs}
        for future in as_completed(futures):
            name = futures[future]
            try:
                partial = future.result()
            except Exception as e:  # noqa: BLE001
                partial = {}
                _w2s_log("QUOTE", f"{name}主路请求异常: {type(e).__name__}: {e}")
            result.update(partial)
            assigned = len(sources[name][0])
            _w2s_log("QUOTE", f"{name}主路完成：分配{assigned}只，成功{len(partial)}只")

    # 单跳兜底：腾讯/新浪两路里没拿到数据的部分，合并成一批统一交给东财push2
    # 兜底一次——不是原地重试，也不是每一路各自单独发一次兜底请求（那样会
    # 对同一个兜底源发两次并发请求，没有必要，合并成一批更省请求量）。
    still_missing = [(code, market) for code, market in codes_markets if code not in result]
    if still_missing:
        _w2s_log("QUOTE", f"腾讯+新浪后仍缺席{len(still_missing)}只，东财push2兜底一次")
        try:
            fb_result = _fetch_quotes_eastmoney(still_missing, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            fb_result = {}
            _w2s_log("QUOTE", f"东财push2兜底时异常: {type(e).__name__}: {e}")
        result.update(fb_result)
        _w2s_log("QUOTE", f"东财push2兜底完成：{len(fb_result)}/{len(still_missing)}只补齐")

    missing = [code for code, _ in codes_markets if code not in result]
    _w2s_log(
        "QUOTE",
        f"批量拉取完成：请求{len(codes_markets)}只，成功{len(result)}只，缺席{len(missing)}只"
        + (f"（前10个：{','.join(missing[:10])}）" if missing else ""),
    )
    return result


def fetch_index_amount(secid: str, timeout: int = 15) -> Optional[float]:
    """
    指数当日成交额（元）——默认走新浪实时行情（跟东财完全独立的服务器，
    历史上没出现过东财这种针对性限流），失败才回退东财 push2 快照接口。
    用于 K 线因东财限流回退腾讯/新浪、但那两个源没有成交额字段时，单独
    补齐"最新一日成交额"。两个源都失败返回 None（调用方按"仍缺失"处理，
    不阻断整体流程）。
    """
    amt = _fetch_index_amount_sina(secid, timeout=min(timeout, 10))
    if amt is not None:
        return amt
    return _fetch_index_amount_eastmoney(secid, timeout=timeout)


def fetch_index_kline(secid: str, days: int = 70, timeout: int = 15) -> list[dict]:
    """
    拉取指数日线（用于偏离值基准）。secid 形如 '1.000001'（上证）/'0.399006'（创业板指）。
    返回 [{'date': 'YYYY-MM-DD', 'close': float, 'pct_change': float}, ...]，按日期升序。
    失败返回空列表。

    2026-08-23起：腾讯为主力，新浪补北证指数历史缺口（腾讯对北证只有最新
    一根），东财push2his降级为两者都不够时的最后兜底——这几个固定指数secid
    此前就已经确认"长期被东财push2his接口针对性限流"（实测重试基本不会
    成功），加上同一次诊断发现的 push2/push2his 域名持续故障，没有理由继续
    把东财当主力，指数只有5个，顺序尝试即可，不需要并发分摊。

    "够不够"要跟这次实际请求的 days 比，不能跟固定的61硬比——daily_update
    的日常同步走 index_trend_service._gap_days()，绝大多数时候只补几天的
    缺口（days个位数到十位数很常见），腾讯只要把这几天补齐就已经完全满足
    这次请求，不该因为"少于61根"就白白再去试新浪、甚至捎带打一次已知会
    失败的东财——61只在"days本来就要得多"（比如首次建库拉320天）时才有
    意义。这是2026-08-23这版重构上线当天从真实daily_update日志里发现的
    真实问题，不是假设。
    """
    threshold = min(days, 61)
    out = _fetch_index_kline_tencent(secid, days=days, timeout=timeout)
    if len(out) < threshold:
        # 腾讯对北证指数只有最新一根 → 用新浪补（有北证50完整历史）
        sina = _fetch_index_kline_sina(secid, days=days, timeout=timeout)
        if len(sina) > len(out):
            out = sina
    if len(out) >= threshold:
        return out

    # 腾讯+新浪都不够，东财兜底（原始 klines 字段：f51 日期, f53 收盘, f59 涨跌幅%）
    end_date = date.today().strftime("%Y%m%d")
    raw: list = []
    try:
        client = _thread_warmed_client(timeout=timeout)
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
        raw = (payload.get("data") or {}).get("klines") or []
    except Exception as e:  # noqa: BLE001
        print(f"[fetcher] 指数 {secid} 腾讯+新浪均不足，东财兜底也失败: {type(e).__name__}", flush=True)
        return out  # 东财也失败，返回目前手头最好的结果（可能仍不足61条，但好过空）

    em_out: list[dict] = []
    for line in raw:
        parts = line.split(",")
        if len(parts) < 9:
            continue
        try:
            em_out.append({
                "date": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": float(parts[5]),   # 成交量（手）
                "amount": float(parts[6]),   # 成交额（元）
                "pct_change": float(parts[8]),
            })
        except (ValueError, IndexError):
            continue
    return em_out if len(em_out) > len(out) else out


def _fetch_index_kline_sina(secid: str, days: int = 320, timeout: int = 15) -> list[dict]:
    """
    指数日线新浪兜底源（东财被指纹封锁、腾讯无北证指数历史时使用）。
    返回与 fetch_index_kline 相同的 dict 结构；失败返回空列表。
    """
    import json as _json
    market, _, code = secid.partition(".")
    prefix = "bj" if code.startswith("899") else ("sh" if market == "1" else "sz")
    url = (
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
        f"?symbol={prefix}{code}&scale=240&ma=no&datalen={days}"
    )
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
            text = client.get(url).text
        # JSONP：var _=([...]) → 取第一个 '(' 与最后一个 ')' 之间
        start, end = text.find("("), text.rfind(")")
        if start < 0 or end <= start:
            return []
        rows = _json.loads(text[start + 1: end])
    except Exception as e:
        print(f"[fetcher] 指数 {secid} 新浪兜底失败: {e}")
        return []

    out: list[dict] = []
    prev_close: float | None = None
    for r in rows:
        try:
            close = float(r["close"])
            out.append({
                "date": str(r["day"]),
                "open": float(r["open"]),
                "close": close,
                "high": float(r["high"]),
                "low": float(r["low"]),
                "volume": float(r.get("volume") or 0),
                "pct_change": round((close - prev_close) / prev_close * 100, 4) if prev_close else 0.0,
            })
            prev_close = close
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _fetch_index_kline_tencent(secid: str, days: int = 70, timeout: int = 15) -> list[dict]:
    """指数日线腾讯兜底源。secid '1.000001'→'sh000001'，'0.399006'→'sz399006'。
    腾讯无涨跌幅字段，用相邻收盘价计算。"""
    market, _, code = secid.partition(".")
    # 北证指数（899xxx）走 bj 前缀；沪 1→sh，深 0→sz
    prefix = "bj" if code.startswith("899") else ("sh" if market == "1" else "sz")
    full = f"{prefix}{code}"
    try:
        with httpx.Client(headers=TENCENT_HEADERS, timeout=timeout) as client:
            resp = client.get(TENCENT_KLINE_URL, params={"param": f"{full},day,,,{days},qfq"})
            data = resp.json()
        node = data.get("data", {}).get(full, {})
        raw = node.get("day") or node.get("qfqday") or []
    except Exception as e:
        print(f"[fetcher] 指数 {secid} 腾讯兜底失败: {e}")
        return []

    out: list[dict] = []
    prev_close: float | None = None
    for bar in raw:
        try:
            d, close = bar[0], float(bar[2])
            open_p, high_p, low_p = float(bar[1]), float(bar[3]), float(bar[4])
        except (ValueError, IndexError):
            continue
        try:
            vol = float(bar[5])
        except (ValueError, IndexError):
            vol = 0.0
        pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0
        out.append({
            "date": d, "open": open_p, "close": close,
            "high": high_p, "low": low_p, "volume": vol, "pct_change": round(pct, 4),
        })
        prev_close = close
    return out
