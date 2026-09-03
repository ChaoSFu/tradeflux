"""
破局雷达：高度前沿曲线 + 连板梯队。

重点盯两件事——「上沿不能含当日」和「停牌顺延的假高度必须挡掉」。后者是
2026-09-03 用 verify_board_history.py 在 66 个交易日里实测出来的唯一一个
**向上**的数据错误：603221 爱丽家居 08-03 停牌（腾讯三天无 bar），库里却记了
9 板，而那天全市场真实最高只有 6 板。一只停牌的高标会持续"撑住"高度曲线。
"""
from datetime import date, timedelta

from app.models.stock import Stock, StockDailySnapshot
from app.services.speculation_radar_service import compute_height_series

D0 = date(2026, 8, 3)


def _seed(db, plan):
    """plan: {天偏移: {代码: 连板数}}，自动建股票和涨停快照。"""
    ids = {}
    for offset, per_code in plan.items():
        for code, bc in per_code.items():
            if code not in ids:
                st = Stock(code=code, name=f"股{code}", market="SH")
                db.add(st); db.flush(); ids[code] = st.id
            db.add(StockDailySnapshot(
                stock_id=ids[code], date=D0 + timedelta(days=offset),
                close_price=10.0, pct_change=10.0,
                is_limit_up=True, board_count=bc))
    db.flush()


class TestHeightFrontier:

    def test_梯队与高度基本统计(self, db):
        _seed(db, {0: {"600001": 1, "600002": 1, "600003": 2}})
        pts, warns = compute_height_series(db, days=10)
        p = pts[-1]
        assert p.height == 2
        assert p.ladder == {"1": 2, "2": 1}
        assert p.limit_up_count == 3
        assert p.near_top_count == 3, "2板和1板都算「天花板附近」(>=H-1)"
        assert p.multi_board_count == 0

    def test_上沿不含当日否则永远突破不了自己(self, db, monkeypatch):
        import app.services.speculation_radar_service as srs
        monkeypatch.setattr(srs, "FRONTIER_WINDOW", 2)   # 窗口调小才够攒满
        _seed(db, {0: {"600001": 1}, 1: {"600001": 2}, 2: {"600001": 3}})
        pts, _ = compute_height_series(db, days=10)
        last = pts[-1]
        assert last.height == 3
        assert last.frontier == 2, "上沿必须只看之前那些天"
        assert last.is_breakout is True

    def test_窗口不满就没有上沿也不判突破(self, db):
        """
        生产首测翻车点：库里只有 66 天，却想加载 20 天当基线——根本没有那 20 天。
        于是最早几天用 1~2 天算出个假上沿，06-03/04/05/08 连续四天全标"突破"。
        那不是行情，是滚动窗口没攒够的伪影，跟涨跌统计那张图的虚线是同一类错。
        """
        _seed(db, {0: {"600001": 1}, 1: {"600001": 2}, 2: {"600001": 3}})
        pts, _ = compute_height_series(db, days=10)
        assert all(p.frontier is None for p in pts), "只有3天，凑不满20日窗口"
        assert not any(p.is_breakout for p in pts), "没有上沿就不能判突破"

    def test_没超过上沿就不算突破(self, db, monkeypatch):
        import app.services.speculation_radar_service as srs
        monkeypatch.setattr(srs, "FRONTIER_WINDOW", 2)
        _seed(db, {0: {"600001": 1}, 1: {"600001": 2}, 2: {"600002": 1}})
        pts, _ = compute_height_series(db, days=10)
        assert pts[-1].height == 1 and pts[-1].frontier == 2
        assert pts[-1].is_breakout is False

    def test_停牌顺延的假高度被挡掉并如实报出(self, db):
        """
        爱丽家居的形态：连涨到 N 板后停牌，库里在停牌日仍记 N 板（同一个数字连续
        两天）。真连板每天恰好+1，重复即为陈旧值顺延。
        """
        _seed(db, {
            0: {"600001": 1, "600002": 1},
            1: {"600001": 2, "600002": 1},
            2: {"600001": 2, "600002": 1},   # 600001 连续两天都是 2 板 ← 假
        })
        pts, warns = compute_height_series(db, days=10)
        last = pts[-1]
        assert last.height == 1, "假的 2 板必须被剔除，当日真实最高是 1 板"
        assert "600001" in " ".join(warns) and "剔除" in " ".join(warns), \
            "剔除了什么必须报出来——分不清「真没有高板」和「被我们剔掉了」等于没监控"

    def test_新首板不受递增约束(self, db):
        """断板之后重新起板，board_count 回到 1，不能被当成异常剔除。"""
        _seed(db, {0: {"600001": 3}, 2: {"600001": 1}})
        pts, warns = compute_height_series(db, days=10)
        assert pts[-1].height == 1
        assert not warns, f"新首板不该产生警告，实际：{warns}"

    def test_没有数据时不假装有(self, db):
        pts, warns = compute_height_series(db, days=10)
        assert pts == [] and warns
