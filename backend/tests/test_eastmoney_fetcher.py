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
    StockQuote, build_kline_bar, kline_bar_from_quote,
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
    bar, turnover_known = kline_bar_from_quote(q, "002821", False, d)
    assert bar.date == d
    assert bar.close_price == 172.23
    assert bar.is_limit_up is True          # 这正是生产上写成 -6.86% 的那一天
    assert bar.is_limit_down is False
    assert bar.turnover_rate == 5.37
    assert turnover_known is True


def test_kline_bar_from_quote_flags_missing_turnover_instead_of_writing_zero():
    # 新浪那一路没有换手率字段。换手率参与龙头评分的资金容量项，编一个0%比留空
    # 错得更远，所以要用 turnover_known=False 告诉调用方"这个字段别落库"。
    d = date(2026, 8, 25)
    q = StockQuote(code="002821", name="", price=172.23, prev_close=156.57,
                   turnover_rate=None, trade_date=d)
    bar, turnover_known = kline_bar_from_quote(q, "002821", False, d)
    assert turnover_known is False
    assert bar.is_limit_up is True


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
