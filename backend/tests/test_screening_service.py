"""
screening_service.py 里可脱离 DB session 独立测试的纯函数（2026-08-25新增）。

derive_limit_close_price() 回归测试：生产上真实出现过 002821(凯莱英) 同一行数据
today_is_limit_up=True 却 today_pct_change=-6.86% 自相矛盾——根因是K线当日拉取
失败静默退回历史最后一根，但涨跌停方向是从独立的选股API权威来源覆盖的，两者
脱节。这里锁定"给定前收价+真实涨跌停规则+方向，必须精确反推出收盘价/涨幅"这个
反推逻辑本身是对的，不会因为以后改动悄悄脱节。
"""
from app.services.screening_service import derive_limit_close_price


def test_derive_limit_close_price_main_board_up():
    # 主板涨停10%，前收10.00 -> 收盘11.00，涨幅+10.0%
    close, pct = derive_limit_close_price(prev_close=10.00, actual_limit_pct=10.0, is_up=True)
    assert close == 11.00
    assert pct == 10.0


def test_derive_limit_close_price_main_board_down():
    close, pct = derive_limit_close_price(prev_close=10.00, actual_limit_pct=10.0, is_up=False)
    assert close == 9.00
    assert pct == -10.0


def test_derive_limit_close_price_chinext_star_20pct():
    # 创业板/科创板涨停20%
    close, pct = derive_limit_close_price(prev_close=50.00, actual_limit_pct=20.0, is_up=True)
    assert close == 60.00
    assert pct == 20.0


def test_derive_limit_close_price_bse_30pct():
    # 北交所涨停30%
    close, pct = derive_limit_close_price(prev_close=20.00, actual_limit_pct=30.0, is_up=True)
    assert close == 26.00
    assert pct == 30.0


def test_derive_limit_close_price_arbitrary_5pct():
    # 纯函数按传入的actual_limit_pct算，不关心这个百分比对应哪类股票——5%只是
    # 拿来验证数学的任意值，不是在断言"ST=5%"这个事实（2026-07-06新规后主板
    # ST已经改成10%，跟主板非ST一致，不再是5%，这里改名避免继续暗示旧规则）。
    close, pct = derive_limit_close_price(prev_close=8.00, actual_limit_pct=5.0, is_up=False)
    assert close == 7.60
    assert pct == -5.0


def test_derive_limit_close_price_st_matches_main_board_10pct():
    # 2026-07-06新规后主板ST涨跌幅规则=10%，跟主板非ST完全一致（不再是5%）
    close, pct = derive_limit_close_price(prev_close=8.00, actual_limit_pct=10.0, is_up=False)
    assert close == 7.20
    assert pct == -10.0


def test_derive_limit_close_price_rounds_to_cents_and_pct_stays_consistent():
    """
    外部评审指出的真实数学bug回归测试：13.57×1.10=14.927，四舍五入到分是14.93，
    但14.93相对13.57的真实涨幅是10.02%，不是原样返回的10.00%——如果pct_change
    不是从四舍五入后的价格反推，会出现"收盘14.93但涨幅写10.00%"这种close/pct
    两个字段自己又互相对不上的新矛盾（用一个bug"修"另一个bug）。这里锁定两者
    必须真正数学一致：(close-prev)/prev*100 == 返回的pct，不能只是文档说说。
    """
    close, pct = derive_limit_close_price(prev_close=13.57, actual_limit_pct=10.0, is_up=True)
    assert close == 14.93
    assert pct == 10.02
    assert pct == round((close - 13.57) / 13.57 * 100, 2)
