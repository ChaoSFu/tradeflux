"""
eastmoney_fetcher.py 里可脱离网络单测的纯函数。2026-08-23新增：新浪
hq.sinajs.cn / 腾讯 qt.gtimg.cn 行情解析——push2.eastmoney.com 生产环境
被针对性限流/防护后新增的独立兜底数据源（见 fetch_stock_quotes_batch
模块头注释）。腾讯格式里的换手率字段下标（38）是用真实数据（600000/002081）
跟东财权威值交叉核对过的，不是猜的，这里固定用真实响应片段回归测试。
"""
from datetime import date

from app.services.eastmoney_fetcher import (
    _parse_sina_quote_line, _parse_tencent_quote_line, get_limit_pct, get_actual_limit_pct,
    StockQuote, build_kline_bar, kline_bar_from_quote, exact_limit_price,
    _parse_tencent_klines, _parse_sina_klines, KLineBar,
)


def test_parse_sina_quote_line_valid():
    line = (
        'var hq_str_sh600000="浦发银行,9.090,9.110,9.050,9.150,9.030,9.040,9.050,'
        '51270289,465159863.000,375500,9.040,709400,9.030,452100,9.020,714700,9.010,'
        '862200,9.000,436555,9.050,284000,9.060,220600,9.070,110065,9.080,351295,9.090,'
        '2026-08-21,15:34:58,00,D|36500|330325.00";'
    )
    parsed = _parse_sina_quote_line(line)
    assert parsed is not None
    code, quote = parsed
    assert code == "600000"
    assert quote.price == 9.050
    assert quote.prev_close == 9.110
    assert quote.open == 9.090
    assert quote.high == 9.150
    assert quote.low == 9.030
    assert quote.volume == 51270289.0
    assert quote.amount == 465159863.0
    assert quote.turnover_rate is None  # 新浪不提供，不能编造
    assert quote.pct_change == round((9.050 - 9.110) / 9.110 * 100, 2)


def test_parse_sina_quote_line_sz_prefix():
    line = 'var hq_str_sz002081="金螳螂,5.440,5.550,5.150,5.480,5.050,5.150,5.160,408993764,2128232618.140,0";'
    parsed = _parse_sina_quote_line(line)
    assert parsed is not None
    code, quote = parsed
    assert code == "002081"
    assert quote.price == 5.15


def test_parse_sina_quote_line_ignores_non_quote_lines():
    assert _parse_sina_quote_line("") is None
    assert _parse_sina_quote_line("some unrelated line") is None


def test_parse_sina_quote_line_suspended_stock_returns_none():
    # 停牌/无数据：现价和昨收都是0，不构造一个全零的假报价
    line = 'var hq_str_sh600999="停牌股,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0,0.000,0";'
    assert _parse_sina_quote_line(line) is None


def test_parse_sina_quote_line_malformed_returns_none():
    assert _parse_sina_quote_line('var hq_str_sh600000="太短了";') is None


# ── 腾讯 qt.gtimg.cn（2026-08-23新增，真实响应片段，字段下标已跟东财权威值核对过）──

_TENCENT_600000 = (
    'v_sh600000="1~浦发银行~600000~9.05~9.11~9.09~512703~204388~308315~9.04~3755~'
    '9.03~7094~9.02~4521~9.01~7147~9.00~8622~9.05~4366~9.06~2840~9.07~2206~9.08~1101~'
    '9.09~3513~~20260821161442~-0.06~-0.66~9.15~9.03~9.05/512703/465159863~512703~'
    '46516~0.15~5.88~~9.15~9.03~1.32~3014.18~3014.18~0.40~10.02~8.20~0.84~17113~'
    '9.07~4.87~6.03~~~0.03~46515.9863~33.0325~365~   A~GP-A~-24.71~-0.55~4.64~6.03~'
    '0.49~13.83~8.07~-1.74~0.11~2.96~33305838300~33305838300~37.89~-19.12~'
    '33305838300~~~-32.26~0.00~~CNY~0~___D__F__N~8.98~4801~";'
)


def test_parse_tencent_quote_line_matches_eastmoney_authoritative_values():
    # 600000 浦发银行：跟东财真实拉取的 turnover_rate=0.15 交叉核对过，见commit说明
    parsed = _parse_tencent_quote_line(_TENCENT_600000)
    assert parsed is not None
    code, quote = parsed
    assert code == "600000"
    assert quote.price == 9.05
    assert quote.prev_close == 9.11
    assert quote.open == 9.09
    assert quote.high == 9.15
    assert quote.low == 9.03
    assert quote.volume == 512703.0 * 100  # 原始字段是"手"，解析层已换算成"股"（2026-08-23修复）
    assert quote.turnover_rate == 0.15  # 关键字段：新浪没有，腾讯有且跟东财一致
    assert quote.amount == 46516 * 10000


def test_parse_tencent_quote_line_ignores_non_quote_lines():
    assert _parse_tencent_quote_line("") is None
    assert _parse_tencent_quote_line("some unrelated line") is None


def test_parse_tencent_quote_line_malformed_returns_none():
    assert _parse_tencent_quote_line('v_sh600000="太短了";') is None


# ── get_limit_pct（K线判定容差）vs get_actual_limit_pct（真实规则），2026-08-23新增 ──
# 外部评审指出的P0 bug：w2s_refresh_service此前直接拿判定容差阈值去算涨停价/
# 压力止损，导致这几个值系统性比真实规则小0.1个百分点。这里锁定两个函数
# 必须始终保持"容差=真实-0.1"这个关系，不会因为以后改动其中一个而悄悄脱节。
#
# 2026-08-25更新：沪深交易所自2026-07-06起主板ST/*ST涨跌幅由5%上调至10%，跟
# 主板非ST规则完全一致（已用WebSearch核实多个独立信源：新浪财经/澎湃新闻/
# 证券时报，不是那种chatgpt.com转发链接的不可信来源）；创业板/科创板/北交所
# 的ST股本来就跟本板块非ST规则一致，从没有过单独更严格的ST规则。也就是说
# is_st现在对任何板块的百分比都不再产生区分——下面用例把ST案例的期望值从
# 4.95/5.0改成9.90/10.0（等同主板非ST），不再是一个独立分支。

def test_get_limit_pct_is_detection_tolerance_not_real_rule():
    assert get_limit_pct("600000", False) == 9.90
    assert get_limit_pct("300308", False) == 19.90
    assert get_limit_pct("688525", False) == 19.90
    assert get_limit_pct("830001", False) == 29.90
    assert get_limit_pct("600123", True) == 9.90  # 主板ST：2026-07-06新规后与非ST一致


def test_get_actual_limit_pct_is_real_rule():
    assert get_actual_limit_pct("600000", False) == 10.0
    assert get_actual_limit_pct("300308", False) == 20.0
    assert get_actual_limit_pct("688525", False) == 20.0
    assert get_actual_limit_pct("830001", False) == 30.0
    assert get_actual_limit_pct("600123", True) == 10.0  # 主板ST：2026-07-06新规后与非ST一致


def test_actual_limit_pct_is_always_detection_threshold_plus_tolerance():
    for code, is_st, expected_gap in [
        ("600000", False, 0.10), ("300308", False, 0.10),
        ("688525", False, 0.10), ("830001", False, 0.10), ("600123", True, 0.10),
    ]:
        gap = round(get_actual_limit_pct(code, is_st) - get_limit_pct(code, is_st), 2)
        assert gap == expected_gap


def test_st_no_longer_changes_limit_pct_on_any_board():
    """
    2026-07-06新规回归测试：is_st=True/False在同一个板块上必须算出完全一样的
    百分比——这是这次规则调整最容易被未来改动悄悄破坏的地方（比如以后有人
    "顺手"给ST加回一个独立分支）。主板/创业板科创板/北交所各挑一个代码验证。
    """
    for code in ("600000", "300308", "688525", "830001"):
        assert get_limit_pct(code, True) == get_limit_pct(code, False)
        assert get_actual_limit_pct(code, True) == get_actual_limit_pct(code, False)


# ── 行情快照补K线（2026-08-25新增）──────────────────────────────────────────
# daily_update 遇到"当日K线拉不到"此前是降级用旧bar，导致旧收盘价/涨幅/换手率
# 和一整套窗口统计盖着今天的日期入库（凯莱英生产事故）。现在改成用实时行情补
# 一根真的当日bar。这条路径最危险的失效方式是：拿一份自身也过期的行情去"修"
# 过期的K线——等于换个门再犯一次同样的错，所以日期校验必须有测试锁住。

def test_quote_parsers_extract_trade_date():
    # 两个fixture都是真实响应片段，日期字段是 2026-08-21
    assert _parse_tencent_quote_line(_TENCENT_600000)[1].trade_date == date(2026, 8, 21)
    line = (
        'var hq_str_sh600000="浦发银行,9.090,9.110,9.050,9.150,9.030,9.040,9.050,'
        '51270289,465159863.000,375500,9.040,709400,9.030,452100,9.020,714700,9.010,'
        '862200,9.000,436555,9.050,284000,9.060,220600,9.070,110065,9.080,351295,9.090,'
        '2026-08-21,15:34:58,00,D|36500|330325.00";'
    )
    assert _parse_sina_quote_line(line)[1].trade_date == date(2026, 8, 21)


def test_sina_short_line_has_no_trade_date_rather_than_a_guessed_one():
    # 新浪对部分标的返回更短的行情串（没有买卖五档也就没有后面的日期字段）。
    # 这时必须是 None（=不可信、不用于补K线），不能猜一个"今天"。
    line = 'var hq_str_sz002081="金螳螂,5.440,5.550,5.150,5.480,5.050,5.150,5.160,408993764,2128232618.140,0";'
    assert _parse_sina_quote_line(line)[1].trade_date is None


def test_kline_bar_from_quote_rejects_quote_that_is_itself_stale():
    q = StockQuote(code="002821", name="", price=172.23, prev_close=156.57,
                   trade_date=date(2026, 8, 24))
    assert kline_bar_from_quote(q, "002821", False, date(2026, 8, 25)) is None


def test_kline_bar_from_quote_rejects_quote_without_a_date():
    q = StockQuote(code="002821", name="", price=172.23, prev_close=156.57, trade_date=None)
    assert kline_bar_from_quote(q, "002821", False, date(2026, 8, 25)) is None


def test_kline_bar_from_quote_rejects_suspended_or_missing_prev_close():
    d = date(2026, 8, 25)
    assert kline_bar_from_quote(
        StockQuote(code="002821", name="", price=0.0, prev_close=156.57, trade_date=d),
        "002821", False, d) is None
    assert kline_bar_from_quote(
        StockQuote(code="002821", name="", price=172.23, prev_close=None, trade_date=d),
        "002821", False, d) is None


def test_kline_bar_from_quote_builds_limit_up_bar_with_real_002821_numbers():
    # 002821 凯莱英 2026-08-25 实盘：昨收156.57 → 收盘172.23，正好是主板10%涨停价
    d = date(2026, 8, 25)
    q = StockQuote(code="002821", name="", price=172.23, pct_change=10.0, open=160.0,
                   high=172.23, low=158.0, prev_close=156.57, turnover_rate=5.37,
                   trade_date=d)
    bar = kline_bar_from_quote(q, "002821", False, d)
    assert bar.date == d
    assert bar.close_price == 172.23
    assert bar.is_limit_up is True          # 这正是生产上写成 -6.86% 的那一天
    assert bar.is_limit_down is False


def test_repaired_bar_never_carries_turnover_even_when_the_quote_has_it():
    """
    行情兜底补出来的bar一律不带换手率，**即使腾讯那一路的行情其实有真实值**。
    它顶替的是K线接口那一根，而当前K线主力源（腾讯/新浪）本来就不提供换手率、
    全市场每只股票的换手率长期都是缺失的。只给"K线失败走了兜底"的少数几只带上
    真实换手率，它们就会凭空多拿到情绪分(+16)和龙头分(+5)——等于"数据源恰好走了
    哪条路"变成打分优势，而且方向上是"拉取失败反而加分"，说不通。
    """
    d = date(2026, 8, 25)
    with_turnover = StockQuote(code="002821", name="", price=172.23, prev_close=156.57,
                               turnover_rate=5.37, trade_date=d)
    without = StockQuote(code="002821", name="", price=172.23, prev_close=156.57,
                         turnover_rate=None, trade_date=d)
    a = kline_bar_from_quote(with_turnover, "002821", False, d)
    b = kline_bar_from_quote(without, "002821", False, d)
    assert a.turnover_rate is None
    assert b.turnover_rate is None
    assert a.is_limit_up is True and b.is_limit_up is True


def test_build_kline_bar_agrees_whether_prev_close_is_given_or_back_derived():
    """
    build_kline_bar 是 2026-08-25 从 _parse_kline_bar 抽出来给行情兜底共用的。
    K线接口不给昨收（只能从 close/pct 反推），实时行情给权威昨收——两条路径必须
    对同一天得出同样的涨跌停结论，否则同一只股票会因为"这根bar来自哪个源"而
    涨停判定不同，正是这种脱节造成了最初那次事故。
    """
    for prev, close, pct in [(156.57, 172.23, 10.0), (10.0, 9.0, -10.0), (50.0, 60.0, 20.0)]:
        derived = build_kline_bar(
            dt=date(2026, 8, 25), open_p=close, close_p=close, high_p=close, low_p=close,
            pct=pct, turnover=1.0, limit_pct=get_limit_pct("600000", False), prev_close=None,
        )
        explicit = build_kline_bar(
            dt=date(2026, 8, 25), open_p=close, close_p=close, high_p=close, low_p=close,
            pct=pct, turnover=1.0, limit_pct=get_limit_pct("600000", False), prev_close=prev,
        )
        assert derived.is_limit_up == explicit.is_limit_up, (prev, close, pct)
        assert derived.is_limit_down == explicit.is_limit_down, (prev, close, pct)


# ── 数据契约统一（2026-08-25）────────────────────────────────────────────────
# 这轮收口两条契约：
#   1. 缺失字段必须诚实表达缺失（None），不能用0冒充"已知为0"
#   2. 同一个市场事实，无论来自哪个数据源，都必须经过同一套判定函数

def test_tencent_and_sina_klines_report_turnover_as_unknown_not_zero():
    """
    腾讯/新浪的K线接口都没有换手率字段，而腾讯正是当前K线主力源。此前两个解析器
    都写 turnover_rate=0.0 顶替，结果生产库里每一天每只股票的换手率全是0.0——
    情绪分里的 turnover*0.8、龙头分里的 turnover_bonus、以及5日/20日换手趋势
    全部长期恒为0，一个本该参与打分的因子事实上早已失效，却因为"0是个合法数字"
    而完全没有报错。必须是 None。
    """
    tencent = _parse_tencent_klines(
        [["2026-08-24", "156.00", "156.57", "157.0", "155.0", "1000"],
         ["2026-08-25", "160.00", "172.23", "172.23", "158.0", "2000"]],
        is_st=False, limit_pct=9.90,
    )
    sina = _parse_sina_klines(
        [{"day": "2026-08-24", "open": "156.00", "close": "156.57", "high": "157.0", "low": "155.0", "volume": "1000"},
         {"day": "2026-08-25", "open": "160.00", "close": "172.23", "high": "172.23", "low": "158.0", "volume": "2000"}],
        is_st=False, limit_pct=9.90,
    )
    assert all(b.turnover_rate is None for b in tencent)
    assert all(b.turnover_rate is None for b in sina)
    # 顺带锁住这两路对同一段真实数据（002821 8-25涨停）判定一致
    assert tencent[-1].is_limit_up is True
    assert sina[-1].is_limit_up is True
    assert tencent[-1].close_price == sina[-1].close_price == 172.23


def test_exact_limit_price_matches_exchange_rounding():
    # 主板10%：156.57 → 172.227 → 四舍五入到分 = 172.23（002821 2026-08-25实盘价）
    assert exact_limit_price(156.57, 10.0, is_up=True) == 172.23
    assert exact_limit_price(10.00, 10.0, is_up=False) == 9.00
    assert exact_limit_price(50.00, 20.0, is_up=True) == 60.00   # 创业板/科创板
    assert exact_limit_price(20.00, 30.0, is_up=True) == 26.00   # 北交所
    # ROUND_HALF_UP 而不是 Python 内置 round() 的银行家舍入
    assert exact_limit_price(13.57, 10.0, is_up=True) == 14.93   # 14.927 → 14.93


def test_broken_board_requires_actually_touching_the_real_limit_price():
    """
    炸板此前用的是 prev*(1+limit_pct/100)*0.999——limit_pct 是K线判定容差(9.90)
    而不是真实规则(10.0)，再打个0.999，等效阈值只有 +9.79%。一只盘中最高冲到
    +9.85%、根本没碰过涨停价的股票会被判成炸板，而炸板在风险分里是近3日每次
    +28分、龙头分里-12分，是全仓库单笔权重最大的惩罚项之一，这个0.2个百分点的
    宽松带正好覆盖"冲高回落"这类最常见形态。
    """
    def bar(high):
        return build_kline_bar(
            dt=date(2026, 8, 25), open_p=105.0, close_p=105.0, high_p=high, low_p=104.0,
            pct=5.0, turnover=None, limit_pct=9.90, prev_close=100.0,
        )
    # 前收100 → 真实涨停价110.00
    assert bar(109.50).is_broken_board is False
    assert bar(109.85).is_broken_board is False   # 旧逻辑在这里误判为炸板
    assert bar(109.99).is_broken_board is False   # 旧逻辑在这里误判为炸板
    assert bar(110.00).is_broken_board is True    # 真的摸到涨停价又没封住 → 炸板


def test_broken_board_and_limit_up_share_one_price_definition():
    """涨停判定和炸板判定必须用同一个涨停价——收盘就封在涨停价上时不能既算涨停
    又算炸板，两者只能有一个成立。"""
    b = build_kline_bar(
        dt=date(2026, 8, 25), open_p=105.0, close_p=110.0, high_p=110.0, low_p=104.0,
        pct=10.0, turnover=None, limit_pct=9.90, prev_close=100.0,
    )
    assert b.is_limit_up is True
    assert b.is_broken_board is False


def test_derive_limit_close_price_uses_the_same_price_function():
    """反推收盘价跟涨跌停判定必须是同一个价格口径，不能各算各的。"""
    from app.services.screening_service import derive_limit_close_price
    for prev, pct, up in [(156.57, 10.0, True), (13.57, 10.0, True), (10.0, 10.0, False),
                          (50.0, 20.0, True), (20.0, 30.0, True)]:
        close, _ = derive_limit_close_price(prev, pct, is_up=up)
        assert close == exact_limit_price(prev, pct, is_up=up), (prev, pct, up)


# ── K线批量拉取的兜底顺序（2026-08-26，用户看生产日志发现）──────────────────

def test_batch_tries_the_other_primary_source_before_falling_back_to_eastmoney():
    """
    分到腾讯组的股票腾讯失败时，必须先去试新浪（另一个主力源），而不是直接跳东财。

    生产日志实测过这个漏洞：002078 分在腾讯组 → 腾讯 JSONDecodeError → 直接撞
    东财 RemoteProtocolError → 失败，全程没试过新浪，而新浪当时是好的（另一组正常
    在跑）。东财 push2his 生产环境长期被针对性限流，跳过一个健康主力源直接去撞它，
    等于白白放弃。那一轮 169 只里 106 只（63%）最后靠实时行情补当日bar 救回来，
    代价是换手率缺失且只补得到今天一根。
    """
    from unittest.mock import patch
    from app.services.eastmoney_fetcher import fetch_klines_batch, StockBasicInfo

    stocks = [
        StockBasicInfo(code=f"00000{i}", name=f"股{i}", market=0, is_st=False,
                       pct_change=0.0, turnover_rate=0.0)
        for i in range(4)
    ]
    bar = [KLineBar(date=date(2026, 8, 26), open_price=1.0, close_price=1.0,
                    high_price=1.0, low_price=1.0, pct_change=0.0, turnover_rate=None)]

    def _tencent_always_fails(code, market, days, is_st, lp, timeout):
        raise ValueError("腾讯挂了")

    calls = {"sina": [], "em": []}

    def _sina_ok(code, market, days, is_st, lp, timeout):
        calls["sina"].append(code)
        return bar

    def _em(code, market, days, is_st, lp, timeout):
        calls["em"].append(code)
        return bar

    with patch("app.services.eastmoney_fetcher._fetch_kline_tencent", _tencent_always_fails), \
         patch("app.services.eastmoney_fetcher._fetch_kline_sina", _sina_ok), \
         patch("app.services.eastmoney_fetcher._fetch_kline_eastmoney", _em):
        out = fetch_klines_batch(stocks, days=65, max_workers=2, delay_between=0.0)

    assert len(out) == 4 and all(out[s.code] for s in stocks)
    # 腾讯组那两只必须由新浪交叉兜底救回，东财一次都不该被调用
    assert len(calls["sina"]) == 4, "腾讯组失败的股票要交叉去试新浪"
    assert calls["em"] == [], "两个主力源之一能拿到数据时，绝不该轮到已知被限流的东财"


def test_batch_falls_back_to_eastmoney_only_when_both_primaries_fail():
    from unittest.mock import patch
    from app.services.eastmoney_fetcher import fetch_klines_batch, StockBasicInfo

    stocks = [StockBasicInfo(code="000001", name="股", market=0, is_st=False,
                             pct_change=0.0, turnover_rate=0.0)]
    bar = [KLineBar(date=date(2026, 8, 26), open_price=1.0, close_price=1.0,
                    high_price=1.0, low_price=1.0, pct_change=0.0, turnover_rate=3.0)]

    def _fail(*a, **kw):
        raise ValueError("挂了")

    with patch("app.services.eastmoney_fetcher._fetch_kline_tencent", _fail), \
         patch("app.services.eastmoney_fetcher._fetch_kline_sina", _fail), \
         patch("app.services.eastmoney_fetcher._fetch_kline_eastmoney", lambda *a, **kw: bar):
        out = fetch_klines_batch(stocks, days=65, max_workers=2, delay_between=0.0)

    assert out["000001"][0].turnover_rate == 3.0   # 东财兜底能补回换手率


def test_batch_treats_missing_required_date_as_a_miss_not_as_success():
    """
    2026-08-26 定位到的真实问题（生产日志 90→45→45 的"每次恰好一半"规律）：

        腾讯日K：66根，末根 2026-08-26（盘中就发布当天未完成的bar）
        新浪日K：65根，末根 2026-08-25（盘中不发布当天那根）

    盘中跑 daily_update 时，轮询分到新浪组的股票**无论重试多少次都拿不到当日bar**
    ——不是报错，是这个源就没有。而只按"结果为空"判断的话，新浪返回的65根非空
    数据会被当成成功，交叉兜底压根不触发，那批股票只能掉到实时行情兜底。

    实拉验证：12只股票 days=3，不传 require_date 时 6/12 拿到当日bar，
    传了之后 12/12。
    """
    from unittest.mock import patch
    from app.services.eastmoney_fetcher import fetch_klines_batch, StockBasicInfo

    today, yesterday = date(2026, 8, 26), date(2026, 8, 25)
    stocks = [
        StockBasicInfo(code=f"00000{i}", name=f"股{i}", market=0, is_st=False,
                       pct_change=0.0, turnover_rate=0.0)
        for i in range(6)
    ]

    def _bars(last_day):
        return [KLineBar(date=d, open_price=1.0, close_price=1.0, high_price=1.0,
                         low_price=1.0, pct_change=0.0, turnover_rate=None)
                for d in (yesterday, last_day)] if last_day != yesterday else \
               [KLineBar(date=yesterday, open_price=1.0, close_price=1.0, high_price=1.0,
                         low_price=1.0, pct_change=0.0, turnover_rate=None)]

    def _tencent(code, market, days, is_st, lp, timeout):
        return _bars(today)          # 腾讯有当日bar

    def _sina(code, market, days, is_st, lp, timeout):
        return _bars(yesterday)      # 新浪只到昨天——非空，但缺当日

    em_called = []

    def _em(code, market, days, is_st, lp, timeout):
        em_called.append(code)
        return _bars(today)

    with patch("app.services.eastmoney_fetcher._fetch_kline_tencent", _tencent), \
         patch("app.services.eastmoney_fetcher._fetch_kline_sina", _sina), \
         patch("app.services.eastmoney_fetcher._fetch_kline_eastmoney", _em):
        # 不传 require_date：新浪那半组的"65根但缺今天"被当成成功
        without = fetch_klines_batch(stocks, days=3, max_workers=2, delay_between=0.0)
        # 传了：缺当日bar算 miss → 交叉兜底到腾讯 → 全部拿到
        with_req = fetch_klines_batch(stocks, days=3, max_workers=2, delay_between=0.0,
                                      require_date=today)

    assert sum(1 for v in without.values() if v[-1].date == today) == 3, "轮询分组，一半走新浪"
    assert sum(1 for v in with_req.values() if v[-1].date == today) == 6, "交叉兜底后全部拿到当日bar"
    assert em_called == [], "腾讯能补上时不该惊动已被限流的东财"


def test_cross_fallback_never_downgrades_an_existing_history_window():
    """
    交叉兜底拿到的数据更差时不能覆盖原来那份。新浪那份虽然缺当日bar，但65根历史
    窗口是有效的——如果腾讯这次只返回了2根，用它盖掉会让窗口统计（MA60/连板数）
    全部失真。
    """
    from unittest.mock import patch
    from app.services.eastmoney_fetcher import fetch_klines_batch, StockBasicInfo

    today = date(2026, 8, 26)
    stocks = [StockBasicInfo(code="000001", name="股", market=0, is_st=False,
                             pct_change=0.0, turnover_rate=0.0)]
    long_hist = [KLineBar(date=date(2026, 6, 1), open_price=1.0, close_price=1.0,
                          high_price=1.0, low_price=1.0, pct_change=0.0, turnover_rate=None)
                 for _ in range(60)]

    with patch("app.services.eastmoney_fetcher._fetch_kline_tencent",
               lambda *a, **kw: long_hist), \
         patch("app.services.eastmoney_fetcher._fetch_kline_sina", lambda *a, **kw: []), \
         patch("app.services.eastmoney_fetcher._fetch_kline_eastmoney", lambda *a, **kw: []):
        out = fetch_klines_batch(stocks, days=65, max_workers=2, delay_between=0.0,
                                 require_date=today)

    assert len(out["000001"]) == 60, "交叉源没有更好的结果时，保留原有历史窗口"


def test_batch_skips_sina_entirely_when_it_cannot_serve_the_required_date():
    """
    盘中新浪不发布当日未完成的bar，所以把一半股票分给它是纯浪费——那一半必然缺
    当日bar，只能靠交叉兜底再打一次腾讯，等于两倍请求。

    判断用**探测**而不是看时钟：拿一只股票问新浪有没有当日bar。探到没有就全部
    走腾讯，新浪只做失败兜底。（写死市场时段的话，节假日/临时休市/新浪改行为都
    会让它失效。）
    """
    from unittest.mock import patch
    from app.services.eastmoney_fetcher import fetch_klines_batch, StockBasicInfo

    today, yesterday = date(2026, 8, 26), date(2026, 8, 25)
    stocks = [StockBasicInfo(code=f"00000{i}", name=f"股{i}", market=0, is_st=False,
                             pct_change=0.0, turnover_rate=0.0) for i in range(8)]

    def _bar(d):
        return [KLineBar(date=d, open_price=1.0, close_price=1.0, high_price=1.0,
                         low_price=1.0, pct_change=0.0, turnover_rate=None)]

    sina_calls, tencent_calls = [], []

    def _sina_no_today(code, market, days, is_st, lp, timeout):
        sina_calls.append(code)
        return _bar(yesterday)

    def _tencent_ok(code, market, days, is_st, lp, timeout):
        tencent_calls.append(code)
        return _bar(today)

    with patch("app.services.eastmoney_fetcher._fetch_kline_sina", _sina_no_today), \
         patch("app.services.eastmoney_fetcher._fetch_kline_tencent", _tencent_ok), \
         patch("app.services.eastmoney_fetcher._fetch_kline_eastmoney",
               lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不该走到东财"))):
        out = fetch_klines_batch(stocks, days=3, max_workers=2, delay_between=0.0,
                                 require_date=today)

    assert all(v[-1].date == today for v in out.values())
    assert len(tencent_calls) == 8, "探测到新浪给不了当日bar → 全部走腾讯"
    assert len(sina_calls) == 1, "新浪只被探测调用了一次，没有浪费半批请求"


def test_batch_still_splits_across_both_sources_when_sina_has_the_date():
    """收盘后新浪也有当日bar，这时照旧两个源各担一半——对限流更友好。"""
    from unittest.mock import patch
    from app.services.eastmoney_fetcher import fetch_klines_batch, StockBasicInfo

    today = date(2026, 8, 26)
    stocks = [StockBasicInfo(code=f"00000{i}", name=f"股{i}", market=0, is_st=False,
                             pct_change=0.0, turnover_rate=0.0) for i in range(8)]
    bar = [KLineBar(date=today, open_price=1.0, close_price=1.0, high_price=1.0,
                    low_price=1.0, pct_change=0.0, turnover_rate=None)]

    sina_calls, tencent_calls = [], []

    def _sina(code, *a, **kw):
        sina_calls.append(code); return bar

    def _tencent(code, *a, **kw):
        tencent_calls.append(code); return bar

    with patch("app.services.eastmoney_fetcher._fetch_kline_sina", _sina), \
         patch("app.services.eastmoney_fetcher._fetch_kline_tencent", _tencent):
        fetch_klines_batch(stocks, days=3, max_workers=2, delay_between=0.0,
                           require_date=today)

    # 4/4 分摊。新浪那边多一次是探测调用（探的是 stocks[0]，它随后被分到腾讯组），
    # 一个请求的成本换掉"盘中浪费半批"，划算。
    assert len(tencent_calls) == 4, "新浪能给当日bar时，两个源各担一半"
    assert set(sina_calls) - {stocks[0].code} == {s.code for s in stocks[1::2]}
