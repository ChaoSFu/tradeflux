"""
涨停明细落库测试（2026-08-25新增，涨停板块雷达）。

重点是"同一交易日多次手动刷新"这个真实使用场景：用户可能 10:00、13:30、14:50
各刷一次，每次封单额/最终封板时间都会变，而且盘中打开的股票会从涨停池掉到炸板池。
"""
from datetime import date, datetime, time
from unittest.mock import patch

from app.models.stock import Stock
from app.models.limit_up_detail import LimitUpDailyDetail, BrokenBoardDailyDetail
from app.services.limit_up_detail_fetcher import LimitUpDetail, BrokenBoardDetail
from app.services.limit_up_detail_service import sync_limit_up_details, get_last_refreshed

TODAY = date(2026, 8, 25)
_PATCH = "app.services.limit_up_detail_service.fetch_limit_up_details"


def _lu(code="002821", name="凯莱英", **kw):
    base = dict(market=0, price=172.23, pct_change=10.0, seal_amount=1.58e8,
                board_count=1, first_limit_time=time(10, 15, 30),
                last_limit_time=time(10, 15, 30), broken_times=0)
    base.update(kw)
    return LimitUpDetail(code=code, name=name, **base)


def _bb(code="002176", name="江特电机", **kw):
    base = dict(market=0, pct_change=2.19, first_limit_time=time(9, 25), broken_times=1)
    base.update(kw)
    return BrokenBoardDetail(code=code, name=name, **base)


def _seed(db, code, name):
    s = Stock(code=code, name=name, market="SZ")
    db.add(s); db.flush()
    return s


def test_repeated_refresh_updates_in_place_without_duplicating_rows(db):
    """同股票同交易日多次刷新只能 UPDATE，不能新增重复行。"""
    _seed(db, "002821", "凯莱英")

    with patch(_PATCH, return_value=([_lu()], [], [])):
        sync_limit_up_details(db, TODAY)
    with patch(_PATCH, return_value=([_lu(seal_amount=2.4e8, last_limit_time=time(14, 30))], [], [])):
        sync_limit_up_details(db, TODAY)
    with patch(_PATCH, return_value=([_lu(seal_amount=3.1e8, last_limit_time=time(14, 55))], [], [])):
        sync_limit_up_details(db, TODAY)

    rows = db.query(LimitUpDailyDetail).filter(LimitUpDailyDetail.trade_date == TODAY).all()
    assert len(rows) == 1
    assert rows[0].seal_amount == 3.1e8            # 最后一次的值
    assert rows[0].last_limit_time == time(14, 55)
    assert rows[0].first_limit_time == time(10, 15, 30)   # 首封时间不受后续刷新影响


def test_refresh_records_provenance_so_page_can_show_freshness(db):
    _seed(db, "002821", "凯莱英")
    before = datetime.now()
    with patch(_PATCH, return_value=([_lu()], [], [])):
        sync_limit_up_details(db, TODAY)

    row = db.query(LimitUpDailyDetail).one()
    assert row.source == "eastmoney"
    assert row.source_trade_date == TODAY
    assert row.refreshed_at is not None and row.refreshed_at >= before.replace(microsecond=0)
    assert get_last_refreshed(db, TODAY) == row.refreshed_at


def test_stock_that_opens_intraday_moves_from_limit_up_to_broken_board(db):
    """
    盘中刷新的真实场景：一只 13:00 还封着的股票 14:30 炸板打开，会从涨停池消失、
    出现在炸板池。如果不清理上一次刷新留下的涨停行，页面会同时把它显示成"涨停"
    和"炸板"——自相矛盾。
    """
    _seed(db, "002821", "凯莱英")
    with patch(_PATCH, return_value=([_lu()], [], [])):
        sync_limit_up_details(db, TODAY)
    assert db.query(LimitUpDailyDetail).count() == 1

    with patch(_PATCH, return_value=([], [_bb(code="002821", name="凯莱英")], [])):
        sync_limit_up_details(db, TODAY)

    assert db.query(LimitUpDailyDetail).filter(LimitUpDailyDetail.trade_date == TODAY).count() == 0
    assert db.query(BrokenBoardDailyDetail).filter(BrokenBoardDailyDetail.trade_date == TODAY).count() == 1


def test_pruning_is_scoped_to_the_day_and_never_touches_history(db):
    _seed(db, "002821", "凯莱英")
    yesterday = date(2026, 8, 24)
    with patch(_PATCH, return_value=([_lu()], [], [])):
        sync_limit_up_details(db, yesterday)
    # 今天这只股票没涨停
    with patch(_PATCH, return_value=([_lu(code="600127", name="金健米业")], [], [])):
        sync_limit_up_details(db, TODAY)

    assert db.query(LimitUpDailyDetail).filter(LimitUpDailyDetail.trade_date == yesterday).count() == 1
    assert db.query(LimitUpDailyDetail).filter(LimitUpDailyDetail.trade_date == TODAY).count() == 1


def test_unknown_stock_gets_a_stub_instead_of_being_dropped(db):
    """
    涨停池是全市场的，完全可能出现从没进过候选池的股票。没有 stock_id 就整只丢掉，
    正是这个功能最不该发生的事——漏掉一只涨停股比多一行存根严重得多。
    """
    with patch(_PATCH, return_value=([_lu(code="920123", name="没见过的票", market=0)], [], [])):
        sync_limit_up_details(db, TODAY)

    stock = db.query(Stock).filter(Stock.code == "920123").one()
    assert stock.name == "没见过的票"      # name 是 NOT NULL，必须在 flush 前给上
    row = db.query(LimitUpDailyDetail).one()
    assert row.stock_id == stock.id and row.stock_code == "920123"


def test_missing_fields_stay_null_rather_than_becoming_zero(db):
    """封单额为0（无人排队）和"东财没给封单额"是两回事，页面要能区分。"""
    _seed(db, "002821", "凯莱英")
    bare = LimitUpDetail(code="002821", name="凯莱英")
    with patch(_PATCH, return_value=([bare], [], [])):
        sync_limit_up_details(db, TODAY)

    row = db.query(LimitUpDailyDetail).one()
    for f in ("seal_amount", "first_limit_time", "last_limit_time",
              "board_count", "broken_times", "price", "limit_reason"):
        assert getattr(row, f) is None, f


def test_fetch_warnings_are_passed_through(db):
    _seed(db, "002821", "凯莱英")
    with patch(_PATCH, return_value=([_lu()], [], ["炸板池拉取失败（TimeoutException），封板率本次无法计算"])):
        lu, bb, warnings = sync_limit_up_details(db, TODAY)
    assert lu == 1 and bb == 0
    assert any("炸板池" in w for w in warnings)


def test_sync_never_touches_stock_scores_or_snapshots(db):
    """
    daily_update 里这一步是"独立步骤，失败不影响主流程"，反过来它成功时也不能
    改动主流程的数据。涨停明细同步只写自己那两张表（唯一例外是给没见过的涨停股
    补 stocks 存根），绝不碰 Stock 的评分/滚动指标，也不碰 StockDailySnapshot。
    """
    from app.models.stock import StockDailySnapshot
    st = _seed(db, "002821", "凯莱英")
    st.leader_score = 88.0
    st.limit_up_days_10d = 3
    st.board_count_60d = 4
    db.add(StockDailySnapshot(stock_id=st.id, date=TODAY, close_price=172.23,
                              pct_change=10.0, board_count=1, leader_score=88.0))
    db.commit()

    with patch(_PATCH, return_value=([_lu(board_count=9, pct_change=99.9)], [], [])):
        sync_limit_up_details(db, TODAY)

    db.refresh(st)
    assert st.leader_score == 88.0          # 评分不被涨停明细改写
    assert st.limit_up_days_10d == 3
    assert st.board_count_60d == 4
    snap = db.query(StockDailySnapshot).filter(StockDailySnapshot.date == TODAY).one()
    assert snap.close_price == 172.23 and snap.pct_change == 10.0 and snap.board_count == 1


# ── 炸板池拉取失败 ≠ 今天没有炸板（2026-08-26 生产事故回归）──────────────────
#
# 事故：18:16 那一跑日志里，"炸板池拉取失败（ConnectTimeout），封板率本次无法计算"
# 和"清理已不在名单中的旧行：涨停 0 条 / 炸板 20 条"是**同一次运行**打出来的。
# fetch 失败时返回空列表，_prune_stale 把这份"空名单"当成权威事实，
# 于是已有的 20 条炸板明细被全删。sync 的 docstring 早就写着"外部接口失败时
# 绝不删除已有数据"，但代码做不到——因为失败和"确实没有"用了同一个值表达。
#
# 这跟 KLineBar.turnover_rate 那次是同一个病：用空值表达"不知道"。

def test_炸板池拉取失败不得删除已有炸板明细(db):
    _seed(db, "002821", "凯莱英")
    _seed(db, "002176", "江特电机")
    with patch(_PATCH, return_value=([_lu()], [_bb()], [])):
        sync_limit_up_details(db, TODAY)
    assert db.query(BrokenBoardDailyDetail).filter_by(trade_date=TODAY).count() == 1

    # 下一次刷新：炸板池挂了 → broken=None（不是 []）
    with patch(_PATCH, return_value=([_lu()], None,
                                     ["炸板池拉取失败（ConnectTimeout），封板率本次无法计算"])):
        lu, bb, warnings = sync_limit_up_details(db, TODAY)

    assert db.query(BrokenBoardDailyDetail).filter_by(trade_date=TODAY).count() == 1, \
        "拉取失败时那份空名单是故障不是事实，不能拿它去 prune"
    assert bb == 0, "本次没写入炸板，返回 0"
    assert any("炸板池" in w for w in warnings)
    assert not any("炸板 1 条" in w for w in warnings), "不该报告删除"


def test_炸板池真的返回空则照常清理(db):
    """区别对待的另一半：拉到了、确实一只炸板都没有，旧行就该清掉。"""
    _seed(db, "002821", "凯莱英")
    _seed(db, "002176", "江特电机")
    with patch(_PATCH, return_value=([_lu()], [_bb()], [])):
        sync_limit_up_details(db, TODAY)
    assert db.query(BrokenBoardDailyDetail).filter_by(trade_date=TODAY).count() == 1

    with patch(_PATCH, return_value=([_lu()], [], [])):
        sync_limit_up_details(db, TODAY)

    assert db.query(BrokenBoardDailyDetail).filter_by(trade_date=TODAY).count() == 0, \
        "[] 是权威的'今天没有炸板'，该清就得清——这正是要跟 None 分开的原因"


# ── 区间涨幅补全：只补东财召回名单之外的股票 ──────────────────────────────────
#
# 那三列此前只认东财核心召回接口的 INTERVAL_CHG，进不了那份名单的股票整行显示 —。
# 今天首板、历史没涨停记录的票必然进不去（金诚信、江西铜业、迪阿股份都是），
# 而它们恰恰是最需要判断"是不是已经涨过一大截"的那批。

def test_区间涨幅优先东财召回_没有才用补全的(db):
    from app.services.limit_up_radar_service import _ic
    from app.services.eastmoney_fetcher import CoreRecallDetail

    em = CoreRecallDetail(code="603580", name="艾艾精工",
                          interval_chg_10d=12.0, interval_chg_20d=None, interval_chg_60d=204.85)
    extra = {"603580": {"10": 99.0, "20": 33.0, "60": 99.0}}

    assert _ic(em, extra, "603580", 10) == 12.0, "东财有值就用东财，同一列不混来源"
    assert _ic(em, extra, "603580", 60) == 204.85
    assert _ic(em, extra, "603580", 20) == 33.0, "东财这个窗口没给，才轮到补全的"
    assert _ic(None, extra, "603580", 10) == 99.0, "根本不在召回名单里，全用补全的"
    assert _ic(None, {}, "603580", 10) is None, "两边都没有就是 —，不拿近似值顶上"


def test_区间涨幅不做隔天退回(db):
    """核心召回名单隔天仍成立（"近期活跃"），但区间涨幅逐日变化，拿昨天的贴今天就是伪造。"""
    from app.models.app_config import AppConfig
    from app.services.limit_up_detail_service import get_interval_chg, interval_chg_key
    import json as _json

    yesterday = date(2026, 8, 24)
    db.add(AppConfig(key=interval_chg_key(yesterday),
                     value=_json.dumps({"603979": {"10": 5.0}})))
    db.commit()

    assert get_interval_chg(db, yesterday) == {"603979": {"10": 5.0}}
    assert get_interval_chg(db, TODAY) == {}, "当天没有就是没有，不退回昨天的值"


def test_没配key时补全静默跳过(db):
    from app.services.limit_up_detail_service import backfill_interval_chg
    from unittest.mock import patch
    with patch("app.services.limit_up_detail_service.get_api_key", return_value=None):
        assert backfill_interval_chg(db, TODAY, [("603979", "SH")]) == (0, [])


def test_补全只写拿到值的窗口(db):
    """None 不写——"没拿到"和"值是0"必须能分开。"""
    from app.services.limit_up_detail_service import backfill_interval_chg, get_interval_chg
    from unittest.mock import patch
    with patch("app.services.limit_up_detail_service.get_api_key", return_value="k"), \
         patch("app.services.limit_up_detail_service.fetch_interval_returns",
               return_value={10: 5.0, 20: None, 60: 0.0}):
        n, fail = backfill_interval_chg(db, TODAY, [("603979", "SH")], max_workers=1, delay=0)
    assert n == 1 and fail == []
    got = get_interval_chg(db, TODAY)["603979"]
    assert got == {"10": 5.0, "60": 0.0}, "0.0 是真实的'没涨没跌'要留，None 是'没拿到'不能写"


def test_请求失败与历史不足必须分得开(db):
    """
    生产上 22 只补到 21 只，日志说不清那 1 只是请求挂了还是上市太短。
    查腾讯才知道 603615 有 81 根完整历史——是请求失败。分不清故障和事实等于没监控。
    """
    from app.services.limit_up_detail_service import backfill_interval_chg
    from app.services.fuyao_dump import FuyaoError
    from unittest.mock import patch

    def _fake(key, code, suffix, **kw):
        if code == "603615":
            raise FuyaoError("ReadTimeout: timed out")     # 请求挂了
        if code == "301999":
            return {10: None, 20: None, 60: None}          # 新股，历史真的不够
        return {10: 5.0, 20: 8.0, 60: 12.0}

    with patch("app.services.limit_up_detail_service.get_api_key", return_value="k"), \
         patch("app.services.limit_up_detail_service.fetch_interval_returns", _fake):
        n, fail = backfill_interval_chg(
            db, TODAY, [("603979", "SH"), ("603615", "SH"), ("301999", "SZ")],
            max_workers=1, delay=0)

    assert n == 1
    assert any("603615" in f and "Timeout" in f for f in fail), "请求失败要带原因"
    assert any("603615" not in f and "历史不足" in f for f in fail), "历史不足是事实，另一类"
    assert len(fail) == 2


# ── 炸板池字段补全（2026-08-26）────────────────────────────────────────────────
#
# getTopicZBPool 的换手率/成交额/流通市值/涨停价/连板统计/振幅一直都返回，
# 但 parse_zb_pool_row 只取了 7 个。用户为了补这些字段找来一个新接口
# （stockextenddata typelist Ty=4），dump 原始响应才发现现成的就够——那个新接口
# 的字段是本接口的子集，还少振幅、没有 date 参数、要带写死的 ut token。
# 下面这行数据是 2026-08-26 上海能源的真实响应，已跟腾讯行情逐项核对。

def test_炸板池字段按实盘口径解析(db):
    from app.services.limit_up_detail_fetcher import parse_zb_pool_row
    b = parse_zb_pool_row({
        'c': '600508', 'n': '上海能源', 'm': 1,
        'p': 11450,        # ×1000，不是 ×100
        'ztp': 11960,      # 涨停价：昨收 10.87 × 1.1 = 11.957 → 11.96
        'zdp': 5.33578634262085, 'hs': 10.851305961608887,
        'amount': 1218008048, 'ltsz': 11585169540.0,
        'zbc': 1, 'zf': 14.811407089233398, 'fbt': 132003,
        'hybk': '煤炭开采', 'zttj': {'days': 3, 'ct': 2}, 'lbc': 2,
    })
    assert b.price == 11.45 and b.limit_price == 11.96, "价格是×1000，按×100解析会差10倍"
    assert b.turnover_rate == 10.85
    assert b.amount == 1218008048 and b.float_market_cap == 11585169540
    assert (b.board_count, b.limit_stat_days, b.limit_stat_count) == (2, 3, 2)
    assert b.amplitude == 14.81
    assert b.first_limit_time.hour == 13 and b.first_limit_time.minute == 20
    # 封板不坚决程度：收盘离涨停价还差多少
    assert round((b.price / b.limit_price - 1) * 100, 2) == -4.26


def test_炸板池缺字段一律None不填0(db):
    from app.services.limit_up_detail_fetcher import parse_zb_pool_row
    b = parse_zb_pool_row({'c': '000001', 'n': '平安银行'})
    for f in ('price', 'limit_price', 'turnover_rate', 'amount',
              'float_market_cap', 'amplitude', 'board_count'):
        assert getattr(b, f) is None, f"{f} 缺失必须是 None，0 会被读成真实的零"


def test_炸板排序按封板不坚决程度(db):
    """高位板炸板排前面；同板位看回落幅度；再看反复开合次数。"""
    from app.services.limit_up_radar_service import sort_broken_stocks
    rows = [
        {'code': 'A', 'board_count': 1, 'gap_to_limit_pct': -0.5, 'broken_times': 1},
        {'code': 'B', 'board_count': 5, 'gap_to_limit_pct': -5.0, 'broken_times': 1},
        {'code': 'C', 'board_count': 1, 'gap_to_limit_pct': -8.0, 'broken_times': 1},
        {'code': 'D', 'board_count': 1, 'gap_to_limit_pct': -0.5, 'broken_times': 4},
    ]
    assert [r['code'] for r in sort_broken_stocks(rows)] == ['B', 'C', 'D', 'A']


def test_北交所代码后缀必须是BJ(db):
    """
    2026-08-27 生产：920895 被拼成 920895.SZ，fuyao 回 code=1002 Unknown thscode。
    起因是拿 Stock.market 当后缀，而那字段只有 SH/SZ——_ensure_stock 里
    `"SH" if market == 1 else "SZ"`，北交所全被归成 SZ。
    判定复用 eastmoney_fetcher._is_bj_code，全仓库一套。
    """
    from app.services.fuyao_dump import thscode_suffix as f
    assert f("920895") == "BJ" and f("430139") == "BJ" and f("830799") == "BJ"
    assert f("600984") == "SH" and f("688828") == "SH"
    assert f("000001") == "SZ" and f("300333") == "SZ"


def test_补全只数必须跟本次要补的只数对得上(db):
    """
    这个数 2026-08-27 一天之内报错了两次，方向相反：
      一版返回当天累计 → "18 只不在名单里，补到 20 只"（多报，分子比分母大）
      一版只计新增覆盖 → "18 只…补到 1 只"（少报，17 只刷新被读成失败）
    分母是本次要补的只数，分子就必须是这批里成功的个数。
    """
    from app.services.limit_up_detail_service import backfill_interval_chg
    from unittest.mock import patch
    with patch("app.services.limit_up_detail_service.get_api_key", return_value="k"), \
         patch("app.services.limit_up_detail_service.fetch_interval_returns",
               return_value={10: 5.0, 20: 8.0, 60: 12.0}):
        n1, _ = backfill_interval_chg(db, TODAY, [("600001", "SH")], max_workers=1, delay=0)
        n2, _ = backfill_interval_chg(db, TODAY, [("600002", "SH")], max_workers=1, delay=0)
        # 第三次重补 600001：它已经有值了，但这批传进来 1 只、成功 1 只，就该报 1
        n3, _ = backfill_interval_chg(db, TODAY, [("600001", "SH")], max_workers=1, delay=0)
        # 一次两只，其中一只已有值 → 仍然报 2（分母是 2，分子就得是 2）
        n4, _ = backfill_interval_chg(db, TODAY, [("600001", "SH"), ("600003", "SH")],
                                      max_workers=1, delay=0)
    assert (n1, n2, n3, n4) == (1, 1, 1, 2)
    from app.services.limit_up_detail_service import get_interval_chg
    assert set(get_interval_chg(db, TODAY)) == {"600001", "600002", "600003"}
