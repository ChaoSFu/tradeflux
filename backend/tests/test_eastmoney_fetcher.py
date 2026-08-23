"""
eastmoney_fetcher.py 里可脱离网络单测的纯函数。2026-08-23新增：新浪
hq.sinajs.cn 行情解析——push2.eastmoney.com 生产环境被针对性限流/防护后
新增的独立兜底数据源（见 fetch_stock_quotes_batch 模块头注释）。
"""
from app.services.eastmoney_fetcher import _parse_sina_quote_line


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
