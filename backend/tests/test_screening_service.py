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


def test_derive_limit_close_price_st_5pct():
    # ST股跌停5%
    close, pct = derive_limit_close_price(prev_close=8.00, actual_limit_pct=5.0, is_up=False)
    assert close == 7.60
    assert pct == -5.0


def test_derive_limit_close_price_rounds_to_cents():
    # 前收价不是整数时正确四舍五入到分
    close, pct = derive_limit_close_price(prev_close=13.57, actual_limit_pct=10.0, is_up=True)
    assert close == round(13.57 * 1.10, 2)
    assert pct == 10.0
