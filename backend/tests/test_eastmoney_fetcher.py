"""
eastmoney_fetcher.py 里可脱离网络单测的纯函数。2026-08-23新增：新浪
hq.sinajs.cn / 腾讯 qt.gtimg.cn 行情解析——push2.eastmoney.com 生产环境
被针对性限流/防护后新增的独立兜底数据源（见 fetch_stock_quotes_batch
模块头注释）。腾讯格式里的换手率字段下标（38）是用真实数据（600000/002081）
跟东财权威值交叉核对过的，不是猜的，这里固定用真实响应片段回归测试。
"""
from app.services.eastmoney_fetcher import (
    _parse_sina_quote_line, _parse_tencent_quote_line, get_limit_pct, get_actual_limit_pct,
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
