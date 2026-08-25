"""
涨停板明细解析器测试（2026-08-25新增，涨停板块雷达）。

下面的 fixture 全部是 2026-08-25 实盘响应片段，不是编的：
  · 000017 深中华A —— 4连板、盘中炸过1次（fbt 09:25:00 ≠ lbt 09:58:12）
  · 600127 金健米业 —— 首板、炸了7次、最终 14:55:03 才封住（最难看的一种封板）
  · 002821 凯莱英 —— 一次封死（fbt==lbt），且它的 hs=5.37 跟腾讯行情的换手率完全
    一致（交叉验证过），p=172230→172.23 也跟独立核实的实盘收盘价一致
"""
from datetime import date, time

from app.services.limit_up_detail_fetcher import (
    parse_em_time, parse_zt_pool_row, parse_zb_pool_row, clean_limit_content,
)

# ── 真实响应片段 ─────────────────────────────────────────────────────────────

_ZT_SZ_ZHONGHUA = {
    "c": "000017", "m": 0, "n": "深中华A", "p": 8600, "zdp": 9.974424362182617,
    "amount": 497483920, "ltsz": 3791068778.6, "tshare": 5926990380.8,
    "hs": 13.12454891204834, "lbc": 4, "fbt": 92500, "lbt": 95812,
    "fund": 122828863, "zbc": 1, "hybk": "饰品", "zttj": {"days": 4, "ct": 4},
}
_ZT_SH_JINJIAN = {
    "c": "600127", "m": 1, "n": "金健米业", "p": 9310, "zdp": 10.047281265258789,
    "amount": 2715416624, "ltsz": 5975001759.58, "tshare": 5975001740.96,
    "hs": 46.16722869873047, "lbc": 1, "fbt": 93154, "lbt": 145503,
    "fund": 28919857, "zbc": 7, "hybk": "农产品加", "zttj": {"days": 7, "ct": 5},
}
_ZT_KAILAIYING = {
    "c": "002821", "m": 0, "n": "凯莱英", "p": 172230, "zdp": 10.0,
    "amount": 2932000000, "ltsz": 54604463663.0, "tshare": 54604463663.0,
    "hs": 5.37, "lbc": 1, "fbt": 101530, "lbt": 101530,
    "fund": 158255257, "zbc": 0, "hybk": "化学制药", "zttj": {"days": 1, "ct": 1},
}
_ZB_JIANGTE = {
    "c": "002176", "m": 0, "n": "江特电机", "p": 9350, "ztp": 10070,
    "zdp": 2.1857922077178955, "amount": 1696070336, "ltsz": 15892761432.35,
    "hs": 10.359594345092773, "fbt": 92500, "zbc": 1, "zf": 7.868852615356445,
    "zs": 0.0, "zttj": {"days": 0, "ct": 0}, "hybk": "电机Ⅱ",
}


# ── 时间解析：HHMMSS 整数，上午只有5位 ────────────────────────────────────────

def test_parse_em_time_handles_five_and_six_digit_forms():
    assert parse_em_time(92500) == time(9, 25, 0)     # 5位：09:25:00 集合竞价封板
    assert parse_em_time(145503) == time(14, 55, 3)   # 6位：14:55:03
    assert parse_em_time(95812) == time(9, 58, 12)


def test_parse_em_time_rejects_garbage_instead_of_guessing():
    for bad in (None, "", "abc", -1, 999999, 96100, 92599 + 100):
        got = parse_em_time(bad)
        assert got is None or isinstance(got, time)
    assert parse_em_time(None) is None
    assert parse_em_time("abc") is None
    assert parse_em_time(996100) is None   # 小时99 → 非法
    assert parse_em_time(126500) is None   # 分钟65 → 非法


# ── 涨停池解析 ───────────────────────────────────────────────────────────────

def test_parse_zt_row_decodes_price_and_times_against_verified_values():
    d = parse_zt_pool_row(_ZT_KAILAIYING)
    assert d.code == "002821" and d.name == "凯莱英"
    assert d.price == 172.23          # p=172230 ÷1000，跟实盘收盘价一致
    assert d.turnover_rate == 5.37    # 跟腾讯行情 turnover_rate 交叉验证过
    assert d.seal_amount == 158255257
    assert d.first_limit_time == time(10, 15, 30)
    assert d.last_limit_time == time(10, 15, 30)   # 一次封死
    assert d.broken_times == 0
    assert d.board_count == 1
    assert d.market == 0


def test_parse_zt_row_keeps_first_and_last_seal_time_separate():
    """
    首封时间和最终封板时间必须分开——它们是完全不同的强度信号。
    金健米业当天 09:31:54 首次封板，之后炸了7次，一直到 14:55:03 才最终封住。
    如果只存一个时间，这只票会跟"09:31封死不动"的票看起来一样强。
    """
    d = parse_zt_pool_row(_ZT_SH_JINJIAN)
    assert d.first_limit_time == time(9, 31, 54)
    assert d.last_limit_time == time(14, 55, 3)
    assert d.first_limit_time != d.last_limit_time
    assert d.broken_times == 7
    assert (d.limit_stat_days, d.limit_stat_count) == (7, 5)   # "7日5板"

    sealed = parse_zt_pool_row(_ZT_KAILAIYING)
    assert sealed.first_limit_time == sealed.last_limit_time   # 对照：一次封死


def test_parse_zt_row_strips_spaces_from_eastmoney_names():
    # 东财个别名称带空格（如 '金 螳 螂'），不去掉就匹配不上本地库
    d = parse_zt_pool_row({**_ZT_SZ_ZHONGHUA, "c": "002081", "n": "金 螳 螂"})
    assert d.name == "金螳螂"


def test_parse_zt_row_reports_missing_fields_as_none_not_zero():
    """
    缺失必须是 None。封单额为 0 和"东财没给封单额"是两回事：前者意味着无人排队，
    后者意味着我们不知道——页面要显示 — 而不是 0.00亿。
    """
    d = parse_zt_pool_row({"c": "300999", "n": "测试股"})
    assert d.code == "300999"
    for field in ("price", "seal_amount", "first_limit_time", "last_limit_time",
                  "board_count", "broken_times", "turnover_rate", "em_industry"):
        assert getattr(d, field) is None, field


def test_parse_zt_row_without_code_is_dropped():
    assert parse_zt_pool_row({"n": "没有代码"}) is None
    assert parse_zt_pool_row({}) is None


def test_parse_zt_row_survives_malformed_zttj():
    d = parse_zt_pool_row({**_ZT_SZ_ZHONGHUA, "zttj": "不是字典"})
    assert d.limit_stat_days is None and d.limit_stat_count is None
    assert d.board_count == 4          # 其余字段不受影响


# ── 炸板池解析 ───────────────────────────────────────────────────────────────

def test_parse_zb_row_has_first_seal_time_but_no_final_seal():
    """炸板股收盘没封住，所以只有首次触板时间，没有最终封板时间。"""
    d = parse_zb_pool_row(_ZB_JIANGTE)
    assert d.code == "002176" and d.name == "江特电机"
    assert d.first_limit_time == time(9, 25, 0)
    assert d.broken_times == 1
    assert d.pct_change == 2.19
    assert not hasattr(d, "last_limit_time")


# ── 涨停原因文本 ─────────────────────────────────────────────────────────────

def test_clean_limit_content_restores_literal_newlines():
    """LIMIT_CONTENT 里的换行是字面量反斜杠+n，不是真换行符。"""
    raw = "行业原因：\\n1. APEC粮食安全会议\\n公司原因：\\n1. 控股股东增持"
    out = clean_limit_content(raw)
    assert "\\n" not in out
    assert out.count("\n") == 3
    assert out.startswith("行业原因：")


def test_clean_limit_content_empty_is_none():
    assert clean_limit_content(None) is None
    assert clean_limit_content("") is None
    assert clean_limit_content("   ") is None
