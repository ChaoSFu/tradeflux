"""
daily_update.py 落库层的"当日数据新鲜度"回归测试（2026-08-25新增）。

背景：生产上出现过 002821(凯莱英) 同一行快照 today_is_limit_up=True 却
today_pct_change=-6.86% 自相矛盾——K线接口当日那一根拉取失败，代码"降级用历史"
拿上一根旧bar当今天用，而涨跌停方向来自独立的选股API，两者脱节。

前两轮修复都只在纯函数层面加了测试（derive_limit_close_price 的数学、
get_limit_pct 的规则），但真正会复发的是**落库这一层的判断**：哪些字段在什么
新鲜度下允许写。所以这里用 SQLite 内存库真的建表、真的调 _upsert_stock /
_upsert_snapshot，锁住三个具体场景（对应外部评审提的 Golden Case A/B/C）。

只建 stocks / stock_daily_snapshots 两张表：market_breadth_daily 有 JSONB 列，
SQLite 建不了，而这两张表全是 Integer/Float/Boolean/Date/String，可以直接建。
"""
import importlib.util
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import StockBasicInfo
from app.services.screening_service import StockWindowStats

_SPEC = importlib.util.spec_from_file_location(
    "_du_under_test", Path(__file__).resolve().parents[1] / "scripts" / "daily_update.py"
)
du = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(du)

TODAY = date(2026, 8, 25)
YESTERDAY = date(2026, 8, 24)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Stock.__table__, StockDailySnapshot.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _info(code="002821", name="凯莱英"):
    return StockBasicInfo(
        code=code, name=name, market=0, is_st=False, pct_change=0.0, turnover_rate=0.0,
    )


def _stats(bar_date, *, close=100.0, pct=5.0, turnover=3.0, board=2, leader=80.0):
    """构造一份窗口统计。bar_date 就是窗口最后一根K线的真实日期。"""
    return StockWindowStats(
        code="002821", name="凯莱英", is_st=False, is_new_stock=False, trading_days=65,
        today_bar_date=bar_date,
        today_close_price=close, today_pct_change=pct, today_turnover=turnover,
        today_is_limit_up=False, today_is_limit_down=False, today_is_broken_board=False,
        today_is_one_word_limit_up=False, today_is_one_word_limit_down=False,
        board_count_current=board, limit_down_count_current=0,
        board_count_60d=board, board_down_count_60d=0,
        limit_up_days_60d=5, limit_up_days_20d=3, limit_up_days_10d=2,
        pct_change_60d=31.2, pct_change_20d=18.0, pct_change_10d=9.0,
        phase="normal", emotion_score=70.0, risk_score=40.0, leader_score=leader,
        ma30=88.0, ma60=85.0, consecutive_declines=0,
    )


# ── Golden Case A：K线停在昨天、不是涨跌停 → 一个字段都不能冒充今天 ──────────

def test_stale_bar_non_limit_creates_no_fake_today_snapshot(db):
    stock = du._upsert_stock(db, _info(), _stats(YESTERDAY), in_pool=True, derived_fresh=False)
    db.flush()
    du._upsert_snapshot(
        db, stock, _stats(YESTERDAY), TODAY,
        close_pct_fresh=False, turnover_fresh=False, derived_fresh=False,
    )
    db.flush()

    # 今天没有任何可信观测 → 干脆不建这一行，而不是建一行日期是今天、
    # 价格为NULL、连板数为默认0的半真半假记录
    assert db.query(StockDailySnapshot).filter(StockDailySnapshot.date == TODAY).count() == 0
    # Stock 上的计算态也不能被旧窗口重算覆盖（保持未写入状态）
    assert stock.leader_score in (None, 0.0)
    assert stock.board_count_60d in (None, 0)
    # 但元数据（来自选股API，跟K线无关）照常更新
    assert stock.name == "凯莱英"
    assert stock.in_strong_pool is True


def test_stale_bar_does_not_overwrite_existing_trustworthy_today_snapshot(db):
    """Golden Case C：当天早些时候已经拿到过可信数据，后一次跑K线失败，不能倒退。"""
    stock = du._upsert_stock(db, _info(), _stats(TODAY), in_pool=True, derived_fresh=True)
    db.flush()
    du._upsert_snapshot(db, stock, _stats(TODAY, close=172.23, pct=10.0, turnover=5.37, board=3), TODAY)
    db.flush()
    good_leader = stock.leader_score

    # 第二次：K线只拉到昨天
    du._upsert_stock(db, _info(), _stats(YESTERDAY, close=99.0, pct=-6.86, board=1, leader=10.0),
                     in_pool=True, derived_fresh=False)
    du._upsert_snapshot(
        db, stock, _stats(YESTERDAY, close=99.0, pct=-6.86, turnover=1.0, board=1),
        TODAY, close_pct_fresh=False, turnover_fresh=False, derived_fresh=False,
    )
    db.flush()

    snap = db.query(StockDailySnapshot).filter(StockDailySnapshot.date == TODAY).one()
    assert snap.close_price == 172.23      # 不被旧值覆盖
    assert snap.pct_change == 10.0
    assert snap.turnover_rate == 5.37
    assert snap.board_count == 3
    assert stock.leader_score == good_leader


# ── Golden Case B：K线停在昨天但选股API权威确认涨停 → 只能重建价格和涨幅 ─────

def test_limit_reconstructed_writes_price_but_not_turnover_or_derived(db):
    """
    这是上一轮 cefb9f4 真正漏掉的地方：涨跌停规则只能精确反推出收盘价和涨幅，
    反推不出换手率，更反推不出连板数/评分——但那一版把它们绑在同一个
    price_data_fresh 开关上，于是旧那天的换手率盖着今天的日期一起入了库。
    """
    stock = du._upsert_stock(db, _info(), _stats(YESTERDAY), in_pool=True, derived_fresh=False)
    db.flush()
    # 模拟调用方已用 derive_limit_close_price 重建好价格/涨幅
    stats = _stats(YESTERDAY, close=172.23, pct=10.0, turnover=1.23, board=1)
    du._upsert_snapshot(
        db, stock, stats, TODAY,
        is_limit_up=True, is_limit_down=False,
        close_pct_fresh=True, turnover_fresh=False, derived_fresh=False,
    )
    db.flush()

    snap = db.query(StockDailySnapshot).filter(StockDailySnapshot.date == TODAY).one()
    assert snap.close_price == 172.23          # 规则精确反推，可信
    assert snap.pct_change == 10.0
    assert snap.is_limit_up is True            # 独立权威来源，可信
    assert snap.turnover_rate is None          # 反推不出来，绝不能写旧值
    # 窗口统计同样反推不出来，不能拿昨天的顶今天（默认值0而不是旧的1/80分）
    assert snap.leader_score in (None, 0.0)
    assert snap.pct_change_10d in (None, 0.0)


def test_fresh_bar_writes_everything(db):
    """新鲜路径必须保持原样——上面三个"不写"不能误伤正常情况。"""
    stock = du._upsert_stock(db, _info(), _stats(TODAY), in_pool=True, derived_fresh=True)
    db.flush()
    du._upsert_snapshot(db, stock, _stats(TODAY, close=172.23, pct=10.0, turnover=5.37, board=3), TODAY)
    db.flush()

    snap = db.query(StockDailySnapshot).filter(StockDailySnapshot.date == TODAY).one()
    assert snap.close_price == 172.23
    assert snap.turnover_rate == 5.37
    assert snap.board_count == 3
    assert snap.leader_score == 80.0
    assert snap.phase == "normal"
    assert stock.leader_score == 80.0
    assert stock.phase == "normal"


def test_out_of_pool_always_clears_phase_even_when_bar_is_stale(db):
    """
    移出强势池由选股API权威决定，跟K线新不新鲜无关——不能因为"这次不写计算态"
    就把一只已经被移出池的股票的旧阶段留在那里。
    """
    stock = du._upsert_stock(db, _info(), _stats(TODAY), in_pool=True, derived_fresh=True)
    db.flush()
    assert stock.phase == "normal"

    du._upsert_stock(db, _info(), _stats(YESTERDAY), in_pool=False, derived_fresh=False)
    db.flush()
    assert stock.phase is None
    assert stock.in_strong_pool is False
