"""
涨跌统计的解读（2026-08-28新增）。

只用图上已有的 9 个数（+上一交易日同样 9 个），不引入任何新数据源。

**刻意不做加权总分**。用户 2026-08-27 转来的方案里有个 `U = 0.5N₀₋₁ + 2N₁₋₅ +
5N₅₊ + 8N涨停` 的"推力分"，然后 DI=(U-D)/(U+D)。那个数的大小完全由拍出来的四个
权重决定，改一下结论就变，既不能验证也没法辩论——跟涨停板块雷达拒绝加权排序
是同一条原则。这里所有阈值都是**措辞门槛**（只决定用哪个词），不参与数值计算。
"""
from app.services.windvane_service import analyze_thrust


class Row:
    def __init__(self, up, down, flat, lu, ld, ub, db):
        self.up_count, self.down_count, self.flat_count = up, down, flat
        self.limit_up_count, self.limit_down_count = lu, ld
        self.up_buckets, self.down_buckets = ub, db


# 2026-08-27 / 08-28 的真实形态（>5% 那几档按实际合计构造）
D27 = Row(3394, 1944, 0, 78, 4,
          [900, 700, 600, 400, 370, 150, 110, 80, 50, 34],
          [800, 600, 300, 150, 58, 15, 10, 5, 3, 3])
D28 = Row(3013, 2390, 144, 82, 2,
          [1249, 839, 412, 197, 107, 42, 35, 21, 12, 17],
          [966, 616, 373, 189, 108, 55, 38, 17, 11, 15])


def test_两端背离必须被识别出来():
    """
    这是整个功能存在的理由。08-28 的真实数据：
      封板端  涨停 82 : 跌停 2      → 极度偏多，且比昨天还多 4 只
      自然端  涨>5% 127 : 跌>5% 136 → 均衡偏空，比昨天塌了 70%
    合并成一个 TailImbalance 只会从 +0.85 掉到 +0.20，看着"还是正尾"；
    单看涨跌家数更看不出来（3394→3013 只有 -11%）。
    """
    r = analyze_thrust(D28, D27)
    assert r.diverged is True
    assert r.seal_side == "偏多" and r.natural_side == "均衡"
    assert "收缩" in r.headline


def test_变化那句只说动得大的():
    r = analyze_thrust(D28, D27)
    chg = [l for l in r.lines if l.startswith("较上一交易日")]
    assert chg, "应该有变化那一句"
    assert "424→127" in chg[0] and "-70%" in chg[0]
    assert "36→136" in chg[0]
    assert "涨停" not in chg[0], "涨停 78→82 只动了 5%，不该混进来抢戏"


def test_两端同向偏多时不报背离():
    r = analyze_thrust(D27, None)
    assert r.seal_side == "偏多" and r.natural_side == "偏多"
    assert r.diverged is False and "扩散" in r.headline


def test_样本太小不硬下方向结论():
    """平静日子里两端只有个位数，比值会剧烈跳动但没有意义。"""
    quiet = Row(2700, 2600, 300, 3, 2,
                [1400, 800, 300, 100, 50, 3, 2, 1, 0, 0],
                [1350, 800, 300, 100, 45, 2, 2, 1, 0, 0])
    r = analyze_thrust(quiet, None)
    assert r.seal_side == "样本不足" and r.natural_side == "样本不足"
    assert r.diverged is False


def test_中枢收敛能识别():
    calm = Row(2600, 2500, 400, 5, 5,
               [1800, 500, 200, 60, 30, 5, 3, 1, 1, 0],
               [1750, 500, 180, 50, 15, 3, 1, 1, 0, 0])
    r = analyze_thrust(calm, None)
    assert r.compression_pct >= 45.0 and "中枢收敛" in r.headline


def test_没有上一交易日也能出结论():
    r = analyze_thrust(D28, None)
    assert r.headline and len(r.lines) == 2, "只是少了变化那一句"


def test_数据不全返回None不硬凑():
    assert analyze_thrust(None) is None
    assert analyze_thrust(Row(0, 0, 0, 0, 0, [0] * 10, [0] * 10)) is None
