"""
每日复盘数据更新脚本。

用法：
    cd backend && .venv/bin/python scripts/daily_update.py
    cd backend && .venv/bin/python scripts/daily_update.py --date 2026-05-26  # 补录指定日期
    cd backend && .venv/bin/python scripts/daily_update.py --skip-boards  # 跳过板块同步

流程：
    1. 从东方财富拉取主板股票列表（含今日涨跌幅）
    2. 确定候选股：当前强势池 + 今日高涨幅股（潜在新入池）
    3. 并发拉取候选股 60 日 K 线
    4. 计算窗口统计指标
    5. 与 ScreeningCriteria 对比 → 更新 in_strong_pool
    6. 写入今日 StockDailySnapshot
    7. 更新板块统计 → 刷新板块阶段
    8. 生成并写入今日 DailyReview（市场状态快照）
    9. 输出统计摘要
"""
import sys
import os
import argparse
import time
from datetime import date, datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# 结构化步骤日志
# ---------------------------------------------------------------------------

class StepLogger:
    """
    记录每日更新各步骤的耗时和关键指标，运行结束后输出汇总表。
    同时写入 logs/daily_update_YYYY-MM-DD.log 文件，便于事后排查。
    """

    def __init__(self, run_date: date):
        self.run_date = run_date
        self.started_at = datetime.now()
        self.steps: list[dict] = []          # 已完成步骤
        self._step_start: Optional[float] = None
        self._current_name: str = ""

        # 确保 logs/ 目录存在
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"daily_update_{run_date}.log")
        self._file = open(log_path, "a", encoding="utf-8")
        self._log(f"\n{'='*60}")
        self._log(f"  TradeFlux 每日更新  {run_date}  启动于 {self.started_at.strftime('%H:%M:%S')}")
        self._log(f"{'='*60}")

    def _log(self, msg: str):
        """同时输出到 stdout（被 admin 端捕获）和日志文件。"""
        print(msg)
        self._file.write(msg + "\n")
        self._file.flush()

    def begin(self, name: str):
        """开始计时一个步骤。"""
        self._current_name = name
        self._step_start = time.time()
        self._log(f"\n[{name}]")

    def end(self, ok: bool = True, detail: str = ""):
        """结束当前步骤，记录耗时和状态。"""
        elapsed = time.time() - (self._step_start or time.time())
        status = "✅" if ok else "❌"
        self.steps.append({
            "name": self._current_name,
            "ok": ok,
            "elapsed": elapsed,
            "detail": detail,
        })
        suffix = f"  {detail}" if detail else ""
        self._log(f"  {status} 完成  耗时 {elapsed:.1f}s{suffix}")

    def error(self, msg: str):
        """标记当前步骤失败。"""
        self.end(ok=False, detail=msg)

    def info(self, msg: str):
        """步骤内的详情日志。"""
        self._log(f"  {msg}")

    def summary(self):
        """输出最终汇总表。"""
        total = time.time() - self.started_at.timestamp()
        self._log(f"\n{'─'*60}")
        self._log(f"  {'步骤':<20} {'状态':^4} {'耗时':>7}  关键指标")
        self._log(f"{'─'*60}")
        for s in self.steps:
            status = "✅" if s["ok"] else "❌"
            name = s["name"][:20]
            elapsed = f"{s['elapsed']:.1f}s"
            self._log(f"  {name:<20} {status:^4} {elapsed:>7}  {s['detail']}")
        self._log(f"{'─'*60}")
        self._log(f"  总耗时: {total:.1f}s  完成于 {datetime.now().strftime('%H:%M:%S')}")
        self._log(f"{'='*60}\n")
        self._file.close()

from sqlalchemy import or_
from app.database import SessionLocal, init_db
from app.models.stock import Stock, StockDailySnapshot
from app.models.sector import Sector, StockSectorRelation, SectorDailySnapshot
from app.models.review import DailyReview
from app.services.eastmoney_fetcher import (
    StockBasicInfo, KLineBar,
    fetch_main_board_stocks, fetch_klines_batch, get_limit_pct, get_actual_limit_pct,
    fetch_strong_pool_codes, fetch_stock_bk_codes, fetch_limit_move_codes,
    fetch_turnover_top_stocks, fetch_stock_quotes_batch, kline_bar_from_quote,
)
from app.services.screening_service import (
    StockWindowStats,
    compute_window_stats, get_active_criteria, derive_limit_close_price,
)
from app.services.sector_phase_service import refresh_sector_phases




# ---------------------------------------------------------------------------
# 板块关联补全（针对无关联的涨跌停股票）
# ---------------------------------------------------------------------------


def _sync_missing_sector_relations(db, limit_move_stocks: list, log=None) -> int:
    """
    对今日涨跌停但 stock_sector_relations 为空的股票，并发补建板块关联。

    数据来源：东财 emweb F10 接口（CoreConception）。
    只关联 is_watched=True 的板块，动态噪声板块自动过滤。
    """
    import httpx
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not limit_move_stocks:
        return 0

    # 批量查出有关联的 stock_id（避免 N+1）
    stock_ids = [s.id for s in limit_move_stocks]
    has_rel_ids = {
        row[0]
        for row in db.query(StockSectorRelation.stock_id)
        .filter(StockSectorRelation.stock_id.in_(stock_ids))
        .distinct()
        .all()
    }
    stocks_no_rel = [s for s in limit_move_stocks if s.id not in has_rel_ids]

    if not stocks_no_rel:
        return 0

    if log:
        log.info(f"  {len(stocks_no_rel)} 只无关联股，并发从东财F10补建板块关联...")

    # 预加载所有 watched 板块的 code→Sector 映射
    sector_map: dict = {
        s.code: s
        for s in db.query(Sector).filter(Sector.is_watched == True).all()  # noqa
    }

    # 并发拉取各股的板块归属（共享连接池，避免每只股票各开一条新连接）
    max_workers = 10

    def fetch_one(stock, client) -> tuple:
        return stock, fetch_stock_bk_codes(stock.code, client)

    results: list[tuple] = []
    with httpx.Client(
        limits=httpx.Limits(max_connections=max_workers, max_keepalive_connections=max_workers),
    ) as shared_client:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(fetch_one, s, shared_client): s
                for s in stocks_no_rel
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception:
                    pass

    # 写入关联（单线程操作 DB，跳过已存在的）
    # 先批量读出已有关联，避免重复插入触发 uq_stock_sector 约束
    existing_rel_keys = {
        (r.stock_id, r.sector_id)
        for r in db.query(StockSectorRelation.stock_id, StockSectorRelation.sector_id)
        .filter(StockSectorRelation.stock_id.in_([s.id for s, _ in results]))
        .all()
    }
    total_created = 0
    for stock, bk_codes in results:
        created = 0
        for bk_code in bk_codes:
            sector = sector_map.get(bk_code)
            if not sector:
                continue
            if (stock.id, sector.id) in existing_rel_keys:
                continue
            db.add(StockSectorRelation(stock_id=stock.id, sector_id=sector.id))
            existing_rel_keys.add((stock.id, sector.id))
            created += 1
        if created:
            total_created += created
            if log:
                log.info(f"    {stock.code} {stock.name}: 补建 {created} 条板块关联")

    db.commit()
    return total_created




# ---------------------------------------------------------------------------
# DB 历史快照 → KLineBar 重建（避免重复拉取 60 日 K 线）
# ---------------------------------------------------------------------------

# 当 DB 快照数量达到此阈值时，使用 DB 重建路径；否则走全量拉取路径
_MIN_SNAPSHOTS_FOR_DB_REBUILD = 60


def _snapshots_to_klinebars(snaps: list, code: str = "", is_st: bool = False) -> List[KLineBar]:
    """
    将已排序的 StockDailySnapshot 列表转换为 KLineBar 列表。
    open/high/low 在 compute_window_stats 中未被使用，填 0.0。
    close_price 为 None（老快照迁移前无此字段）时降级为 0.0，
    此时 MA60/MA30 计算结果为 0，阶段判定回退为 "normal"（可接受的保守策略）。

    涨跌停标志：当 code 提供且相邻收盘价可用时，按收盘价 + 0.005 容差重新判定，
    自动修正历史快照里旧逻辑（浮点取整漏判）落库的 is_limit_up/down；
    否则降级用存储值。这样每次 DB 重建都会重算正确的涨停天数/连板等指标。
    """
    snaps = sorted(snaps, key=lambda s: s.date)
    lp = get_limit_pct(code, is_st) if code else None
    bars: List[KLineBar] = []
    prev_close = 0.0
    for s in snaps:
        close = s.close_price or 0.0
        is_lu, is_ld = bool(s.is_limit_up), bool(s.is_limit_down)
        if lp is not None and close > 0 and prev_close > 0:
            actual = lp + 0.1
            lu_price = round(prev_close * (1 + actual / 100), 2)
            ld_price = round(prev_close * (1 - actual / 100), 2)
            is_lu = close >= lu_price - 0.005
            is_ld = close <= ld_price + 0.005
        bars.append(KLineBar(
            date=s.date,
            open_price=0.0,
            close_price=close,
            high_price=0.0,
            low_price=0.0,
            pct_change=s.pct_change or 0.0,
            turnover_rate=s.turnover_rate,   # None 保持 None＝未知，不降级成0.0
            is_limit_up=is_lu,
            is_limit_down=is_ld,
            is_broken_board=bool(s.is_broken_board),
            # DB 重建无 OHLC，一字板沿用快照落库值（保留历史判定）
            is_one_word_limit_up=bool(s.is_one_word_limit_up),
            is_one_word_limit_down=bool(s.is_one_word_limit_down),
        ))
        prev_close = close
    return bars


def _build_klines_from_db(
    candidates: List[StockBasicInfo],
    db,
    target_date,
) -> tuple[dict, List[StockBasicInfo], List[StockBasicInfo]]:
    """
    将候选股分为两组：
      - db_group：DB 已有 ≥60 条快照，从历史快照重建 KLineBar，只需 API 拉今日一根
      - full_group：DB 快照不足，需 API 拉完整 65 日

    返回：
      (db_klines_map, db_group, full_group)
      db_klines_map — {code: [KLineBar, ...]}（含今日占位 KLineBar，today_bar 待替换）
    """
    from sqlalchemy import func as sqlfunc

    codes = [c.code for c in candidates]

    # 查每只股票在 DB 里非今日历史快照数量 & 批量获取最近 65 条快照
    stock_id_map: dict[str, int] = {
        row[0]: row[1]
        for row in db.query(Stock.code, Stock.id).filter(Stock.code.in_(codes)).all()
    }

    # 快照数量统计（排除 target_date，只看历史）
    snap_counts: dict[int, int] = {}
    if stock_id_map:
        rows = (
            db.query(StockDailySnapshot.stock_id, sqlfunc.count())
            .filter(
                StockDailySnapshot.stock_id.in_(list(stock_id_map.values())),
                StockDailySnapshot.date < target_date,
                StockDailySnapshot.close_price.isnot(None),  # 只有存过 close_price 的才算有效历史
            )
            .group_by(StockDailySnapshot.stock_id)
            .all()
        )
        snap_counts = {sid: cnt for sid, cnt in rows}

    # 批量拉最近 65 条历史快照（date < target_date）
    valid_stock_ids = [
        sid for sid in stock_id_map.values()
        if snap_counts.get(sid, 0) >= _MIN_SNAPSHOTS_FOR_DB_REBUILD
    ]

    # {stock_id: [snap, ...]} 按日期升序，最多取 65 条
    snaps_by_stock: dict[int, list] = {}
    if valid_stock_ids:
        from sqlalchemy import and_
        # 用窗口函数方式：每个 stock_id 取最近 65 条
        subq = (
            db.query(StockDailySnapshot)
            .filter(
                StockDailySnapshot.stock_id.in_(valid_stock_ids),
                StockDailySnapshot.date < target_date,
                # close_price 为空的快照必须排除（2026-08-25修复我自己在 cefb9f4
                # 引入的回归）：上面统计"有效历史条数"时已经过滤了 close_price 为
                # 空的行，但这里真正取65条窗口时没有同样过滤——cefb9f4 开始，当日
                # K线过期又没有涨跌停权威可反推时会留下一行 close_price=NULL 的快照，
                # 它会被取进窗口、在 _snapshots_to_klinebars 里降级成 close=0.0，
                # 于是窗口中间凭空出现一根0元K线，把 MA5/MA60 和次日的涨跌停反推
                # （依赖 prev_close>0）一起带偏。跳过这一天让窗口留个缺口，比塞一根
                # 0元假bar安全得多。
                StockDailySnapshot.close_price.isnot(None),
            )
            .order_by(StockDailySnapshot.stock_id, StockDailySnapshot.date.desc())
            .all()
        )
        for snap in subq:
            lst = snaps_by_stock.setdefault(snap.stock_id, [])
            if len(lst) < 65:
                lst.append(snap)
        # 每组反转为升序
        for sid in snaps_by_stock:
            snaps_by_stock[sid].reverse()

    db_group: List[StockBasicInfo] = []
    full_group: List[StockBasicInfo] = []
    db_klines_map: dict[str, List[KLineBar]] = {}

    for info in candidates:
        sid = stock_id_map.get(info.code)
        snaps = snaps_by_stock.get(sid, []) if sid else []
        if len(snaps) >= _MIN_SNAPSHOTS_FOR_DB_REBUILD:
            db_group.append(info)
            db_klines_map[info.code] = _snapshots_to_klinebars(snaps, info.code, info.is_st)
        else:
            full_group.append(info)

    return db_klines_map, db_group, full_group


# ---------------------------------------------------------------------------
# 候选股精算入库（评分 + 快照）
# ---------------------------------------------------------------------------

def _upsert_stock(
    db, info: StockBasicInfo, stats: StockWindowStats, in_pool: bool,
    derived_fresh: bool = True,
) -> Stock:
    """
    更新或创建 Stock 记录。

    derived_fresh=False 时（2026-08-25新增）只写"元数据"，不写"计算态"。
    区别在于数据来源：名称/市场/ST/是否新股/是否在强势池全部来自当次选股API或
    股票列表接口，跟K线拉没拉到无关，永远是新鲜的；而下面那一堆评分和滚动窗口
    指标全是 compute_window_stats() 基于K线窗口算出来的，窗口最后一根不是今天
    时它们描述的其实是"上一根bar那天"的状态，写进去就等于宣称这是今天的状态。
    这类字段还会被强势池、板块情绪、龙头识别、主线判断一路往上消费，比单纯一个
    显示错的涨幅影响面大得多。

    不写的结果是保留上一次可信计算值——这跟"用一个已知基于旧数据的值去覆盖"
    看似结果相近，实际差别很大：前者是"今天没算，沿用昨天的结论"，后者是"今天
    用昨天的数据重算了一遍并声称这是今天的结论"，后者在多日连续拉取失败时会
    越错越离谱，前者会稳定停在最后一次可信状态上。
    """
    stock = db.query(Stock).filter(Stock.code == info.code).first()
    if not stock:
        # name 必须在 flush 之前给上：stocks.name 是 NOT NULL，只带 code 就 flush
        # 会直接违反约束。正常流程走不到这里（新股在候选组装那一步已经建过 Stock
        # 存根），属于兜底分支，但留着一个必炸的兜底没有意义。
        stock = Stock(code=info.code, name=info.name)
        db.add(stock)
        db.flush()  # 确保 id 生成，防止同一 code 重复插入

    # ── 元数据：来自选股API/列表接口，与K线新鲜度无关，始终更新 ──────────────
    stock.name = info.name
    stock.market = "SH" if info.market == 1 else "SZ"
    stock.is_st = info.is_st
    stock.is_new_stock = stats.is_new_stock
    stock.in_strong_pool = in_pool
    # 移出强势池由选股API权威决定，跟K线无关，任何时候都要清空阶段
    if not in_pool:
        stock.phase = None

    if not derived_fresh:
        return stock

    # ── 计算态：全部依赖K线窗口，只有窗口真的算到今天才更新 ────────────────
    stock.emotion_score = stats.emotion_score
    stock.risk_score = stats.risk_score
    stock.leader_score = stats.leader_score
    stock.board_count_60d = stats.board_count_60d
    stock.board_down_count_60d = stats.board_down_count_60d
    stock.limit_up_days_60d = stats.limit_up_days_60d
    stock.limit_up_days_20d = stats.limit_up_days_20d
    stock.limit_up_days_10d = stats.limit_up_days_10d
    stock.pct_change_60d = round(stats.pct_change_60d, 2)
    stock.pct_change_20d = round(stats.pct_change_20d, 2)
    stock.pct_change_10d = round(stats.pct_change_10d, 2)
    stock.top_10_pct_change_20d = (stats.pct_change_20d > 0 and stats.pct_change_20d > 30)  # 粗判
    # 阶段：仅对强势池股票标记，移出池时清空
    if in_pool:
        stock.phase = stats.phase
    return stock


def _upsert_snapshot(
    db, stock: Stock, stats: StockWindowStats, today: date,
    is_limit_up: bool | None = None, is_limit_down: bool | None = None,
    close_pct_fresh: bool = True, turnover_fresh: bool = True,
    derived_fresh: bool = True,
) -> None:
    """
    写入今日快照（存在则更新，不存在则新建）。
    is_limit_up / is_limit_down 传入非 None 时为权威值（来自涨跌停选股 API），
    覆盖本地 K 线反推结果；传入 None 时退回本地计算值。

    三个新鲜度开关分开传（2026-08-25，上一轮只有一个 price_data_fresh 是不够的）：

      close_pct_fresh — 收盘价/涨跌幅可信。当日K线拿到了，或者K线过期但选股API
        确认了涨跌停方向、用交易所规则精确反推出来了。
      turnover_fresh  — 换手率可信。**它和上面那个不是一回事**：用涨跌停规则只能
        反推出价格和涨幅，反推不出换手率，此前把这两者绑在同一个开关上，导致
        涨跌停反推成功的股票会顺手把旧K线那天的换手率也当成今天的写进去（外部
        评审发现的真实bug）。行情兜底那条路也有类似情况——新浪没有换手率字段，
        腾讯有，同一只股票分到哪一路是轮询决定的。
      derived_fresh   — 连板数/涨停天数/N日涨幅/阶段/三个评分可信。这些全部来自
        K线窗口统计，只有窗口真的算到今天才成立，理由同 _upsert_stock()。

    三个都为 False 且这一天本来还没有快照行时，直接不建行——"今天这只股票没有
    可信观测"就应该表现为没有记录，而不是一行日期是今天、字段全是默认值0的记录。
    下游 _refresh_sector_stats 用的是 `if sid in today_snap_map` 逐个判断，缺行会被
    自然跳过；建一行全0的反而会被当成"今天0连板"参与板高统计。
    这同时修掉了 cefb9f4 留下的另一个隐患：那版会建出 close_price=NULL 的快照行，
    次日DB重建窗口时被降级成一根0元K线（另见 _build_klines_from_db 的过滤）。
    """
    snap = (
        db.query(StockDailySnapshot)
        .filter(
            StockDailySnapshot.stock_id == stock.id,
            StockDailySnapshot.date == today,
        )
        .first()
    )
    if not snap:
        if not (close_pct_fresh or turnover_fresh or derived_fresh):
            return          # 今天这只股票没有任何可信观测，不建空行
        snap = StockDailySnapshot(stock_id=stock.id, date=today)
        db.add(snap)

    if close_pct_fresh:
        snap.close_price = stats.today_close_price
        snap.pct_change = stats.today_pct_change
    if turnover_fresh:
        snap.turnover_rate = stats.today_turnover
    # 涨跌停标志：选股API给了权威值就用权威值（跟K线新不新鲜无关，它是独立来源）；
    # 没有权威值时才用K线反推，而K线反推的前提是这根bar确实是今天的。
    if is_limit_up is not None:
        snap.is_limit_up = is_limit_up
    elif derived_fresh:
        snap.is_limit_up = stats.today_is_limit_up
    if is_limit_down is not None:
        snap.is_limit_down = is_limit_down
    elif derived_fresh:
        snap.is_limit_down = stats.today_is_limit_down

    if derived_fresh:
        snap.is_broken_board = stats.today_is_broken_board
        # 一字板仅在最终判定为涨停/跌停时成立（选股 API 可能覆盖 K 线的涨跌停判定）
        snap.is_one_word_limit_up = bool(stats.today_is_one_word_limit_up) and bool(snap.is_limit_up)
        snap.is_one_word_limit_down = bool(stats.today_is_one_word_limit_down) and bool(snap.is_limit_down)
        snap.board_count = stats.board_count_current
        snap.limit_down_count = stats.limit_down_count_current
        snap.board_count_60d = stats.board_count_60d
        snap.board_down_count_60d = stats.board_down_count_60d
        snap.limit_up_days_60d = stats.limit_up_days_60d
        snap.limit_up_days_20d = stats.limit_up_days_20d
        snap.limit_up_days_10d = stats.limit_up_days_10d
        snap.pct_change_60d = round(stats.pct_change_60d, 2)
        snap.pct_change_20d = round(stats.pct_change_20d, 2)
        snap.pct_change_10d = round(stats.pct_change_10d, 2)
        snap.top_10_pct_change_20d = stats.pct_change_20d > 30  # 粗判阈值
        snap.phase = stats.phase                              # 落库当日阶段，供次日赚钱效应分组用
        snap.emotion_score = stats.emotion_score
        snap.risk_score = stats.risk_score
        snap.leader_score = stats.leader_score


# ---------------------------------------------------------------------------
# 板块统计更新
# ---------------------------------------------------------------------------

def _refresh_sector_stats(db, target_date) -> None:
    """
    重新计算板块统计指标，使用直接 DB 联查替代懒加载，避免 N+1 查询。
    limit_up_count / limit_down_count 限定 target_date 当日快照，
    覆盖 stock_sector_relations 中的所有成员（不限于强势池）。
    """
    from sqlalchemy import func as sqlfunc

    # ── 一次性批量查询今日所有快照 ────────────────────────────────────────────
    # {stock_id: StockDailySnapshot}
    today_snap_map: dict = {
        snap.stock_id: snap
        for snap in db.query(StockDailySnapshot)
        .filter(StockDailySnapshot.date == target_date)
        .all()
    }

    # ── 一次性批量查询所有板块的成员关系 ─────────────────────────────────────
    # {sector_id: [stock_id, ...]}
    from collections import defaultdict
    sector_stock_map: dict = defaultdict(list)
    for rel in db.query(StockSectorRelation).all():
        sector_stock_map[rel.sector_id].append(rel.stock_id)

    # ── 按 stock_id 查强势股 ─────────────────────────────────────────────────
    strong_ids: set = {
        s.id for s in db.query(Stock.id).filter(Stock.in_strong_pool == True).all()  # noqa: E712
    }
    # 强势股完整对象（用于 leader_score / emotion_score 等字段）
    strong_map: dict = {
        s.id: s for s in db.query(Stock).filter(Stock.in_strong_pool == True).all()  # noqa: E712
    }

    sectors = db.query(Sector).all()
    for sector in sectors:
        stock_ids = sector_stock_map.get(sector.id, [])
        if not stock_ids:
            continue

        # 今日有快照的成员（非强势股也参与涨停/跌停统计）
        snaps_today = [today_snap_map[sid] for sid in stock_ids if sid in today_snap_map]

        # 板块内强势股（用于情绪/风险/板高等评分）
        strong_in_sector = [strong_map[sid] for sid in stock_ids if sid in strong_map]

        sector.strong_stock_count = len(strong_in_sector)
        sector.limit_up_count   = sum(1 for s in snaps_today if s.is_limit_up)
        sector.limit_down_count = sum(1 for s in snaps_today if s.is_limit_down)
        sector.one_word_up_count   = sum(1 for s in snaps_today if s.is_one_word_limit_up)
        sector.one_word_down_count = sum(1 for s in snaps_today if s.is_one_word_limit_down)
        sector.board_height = max(
            (today_snap_map[s.id].board_count for s in strong_in_sector if s.id in today_snap_map),
            default=0,
        )
        sector.emotion_score = (
            sum(s.emotion_score for s in strong_in_sector) / len(strong_in_sector)
            if strong_in_sector else 0.0
        )
        sector.risk_score = (
            sum(s.risk_score for s in strong_in_sector) / len(strong_in_sector)
            if strong_in_sector else 0.0
        )
        sector.continuity_score = min(100.0, sector.board_height * 15.0 + len(strong_in_sector) * 5.0)

        if strong_in_sector:
            leader = max(strong_in_sector, key=lambda s: s.leader_score)
            sector.leader_stock_id = leader.id

    db.commit()


# ---------------------------------------------------------------------------
# 板块排名 tag 写入
# ---------------------------------------------------------------------------

def _refresh_sector_ranks(db) -> None:
    """
    对 is_watched=True 的板块，计算7个维度的 dense rank（前5名，value>0）并落库。
    必须在 _refresh_sector_stats / refresh_sector_phases 之后调用。
    """
    sectors = db.query(Sector).filter(Sector.is_watched == True).all()  # noqa

    RANK_FIELDS = [
        ("rank_5d",     "pct_change_5d"),
        ("rank_10d",    "pct_change_10d"),
        ("rank_20d",    "pct_change_20d"),
        ("rank_60d",    "pct_change_60d"),
        ("rank_lu",     "limit_up_count"),
        ("rank_board",  "board_height"),
        ("rank_strong", "strong_stock_count"),
    ]

    # 先全部清空本轮 rank（防止旧数据残留）
    for s in sectors:
        for rank_key, _ in RANK_FIELDS:
            setattr(s, rank_key, None)

    for rank_key, field in RANK_FIELDS:
        eligible = [s for s in sectors if (getattr(s, field, 0) or 0) > 0]
        eligible.sort(key=lambda s: getattr(s, field) or 0, reverse=True)
        rank, prev_val, count = 1, None, 0
        for s in eligible:
            val = getattr(s, field) or 0
            if prev_val is not None and val != prev_val:
                rank = count + 1
                if rank > 5:
                    break
            if rank <= 5:
                setattr(s, rank_key, rank)
            prev_val = val
            count += 1

    db.commit()


# ---------------------------------------------------------------------------
# 每日 Review 快照
# ---------------------------------------------------------------------------

def _update_primary_sectors(db) -> None:
    """
    为所有强势股计算并落库主板块（primary_sector_id / primary_sector_name）。
    优先级：watched板块 → strong_stock_count 最多 → board_height 最高 → emotion_score 最高。
    必须在 _refresh_sector_stats / refresh_sector_phases 之后调用（保证统计数据当日最新）。
    """
    stocks = db.query(Stock).all()  # 全量：包含曾经入池的股票也要更新
    if not stocks:
        return

    stock_ids = [s.id for s in stocks]
    all_rels = (
        db.query(StockSectorRelation)
        .filter(StockSectorRelation.stock_id.in_(stock_ids))
        .all()
    )

    sector_id_set = {rel.sector_id for rel in all_rels}
    sector_map: dict[int, Sector] = {}
    if sector_id_set:
        sector_map = {
            s.id: s
            for s in db.query(Sector).filter(
                Sector.id.in_(sector_id_set),
                Sector.is_watched == True,  # noqa: E712
            ).all()
        }

    # stock_id → [sector_id, ...]（保留原始关联顺序）
    stock_rel_sids: dict[int, list[int]] = {}
    for rel in all_rels:
        stock_rel_sids.setdefault(rel.stock_id, []).append(rel.sector_id)

    updated = 0
    for stock in stocks:
        sids = stock_rel_sids.get(stock.id, [])
        watched = [sector_map[sid] for sid in sids if sid in sector_map]

        if not watched:
            if stock.primary_sector_id is not None or stock.primary_sector_name is not None:
                stock.primary_sector_id = None
                stock.primary_sector_name = None
                updated += 1
            continue

        # 优先级：股票数最多 → 连板高度最高 → 情绪分最高
        best = max(watched, key=lambda s: (s.strong_stock_count, s.board_height, s.emotion_score))

        if stock.primary_sector_id != best.id:
            stock.primary_sector_id = best.id
            stock.primary_sector_name = best.name
            updated += 1

    db.commit()
    print(f"  主板块已更新: {updated} 只股票")


def _save_daily_review(db, today: date) -> None:
    """将今日市场状态写入 DailyReview 表（用于情绪曲线历史及赚钱效应历史）"""
    from app.services.market_state_service import (
        _compute_profit_effect, _compute_loss_effect,
        _emotion_temperature, _classify_market_phase, _classify_emotion_cycle,
        _suggested_position, get_profit_effect,
    )
    from app.services.dragon_leader_service import identify_dragon_leaders

    # 只使用「已关注」板块（与 get_current_market_state 保持一致）
    sectors = db.query(Sector).filter(Sector.is_watched == True).all()  # noqa: E712
    profit = _compute_profit_effect(sectors)
    loss = _compute_loss_effect(sectors)
    temp = _emotion_temperature(profit, loss)
    phase = _classify_market_phase(temp)
    cycle = _classify_emotion_cycle(temp)
    position = _suggested_position(phase, loss)

    # ── 强势股今日快照（批量，统一用于均涨幅 + 涨跌停统计）─────────────────
    today_snaps = (
        db.query(StockDailySnapshot)
        .join(Stock, Stock.id == StockDailySnapshot.stock_id)
        .filter(
            StockDailySnapshot.date == today,
            Stock.in_strong_pool == True,  # noqa: E712
        )
        .all()
    )
    pcts = [s.pct_change for s in today_snaps if s.pct_change is not None]
    strong_pool_avg_pct = round(sum(pcts) / len(pcts), 2) if pcts else None

    def _cls(p: float) -> str:
        return "up" if p > 0.5 else ("down" if p < -0.5 else "flat")

    overall_up    = sum(1 for p in pcts if _cls(p) == "up")
    overall_down  = sum(1 for p in pcts if _cls(p) == "down")
    overall_lu    = sum(1 for s in today_snaps if s.is_limit_up)
    overall_ld    = sum(1 for s in today_snaps if s.is_limit_down)

    # ── 赚钱效应分组 & 板块快照 ──────────────────────────────────────────
    pe = get_profit_effect(db)
    profit_groups = None
    profit_sectors_json = None
    if pe.has_data:
        profit_groups = [
            {
                "key": g.key, "label": g.label,
                "stock_count": g.stock_count, "avg_pct": g.avg_pct,
                "up_count": g.up_count, "down_count": g.down_count, "flat_count": g.flat_count,
            }
            for g in pe.groups
        ]
        profit_sectors_json = [
            {
                "sector_code": s.sector_code, "sector_name": s.sector_name,
                "stock_count": s.stock_count, "avg_pct": s.avg_pct,
                "up_count": s.up_count, "down_count": s.down_count,
            }
            for s in pe.sectors
        ]

    # ── 活跃板块快照（phase >= 2）────────────────────────────────────────
    active_sectors_json = [
        {
            "code": s.code, "name": s.name,
            "phase": s.phase, "emotion_score": s.emotion_score,
            "strong_stock_count": s.strong_stock_count, "board_height": s.board_height,
        }
        for s in sorted(sectors, key=lambda x: x.emotion_score, reverse=True)
        if s.phase in (2, 3)
    ]

    # ── 板块强弱名单 ──────────────────────────────────────────────────────
    strong_sectors_list   = [s.name for s in sectors if s.phase in (2, 3)]
    dangerous_sectors_list = [s.name for s in sectors if s.phase in (5, 6)]

    # ── 龙头股快照 ────────────────────────────────────────────────────────
    leaders = identify_dragon_leaders(db)
    dragon_changes_json = [
        {
            "stock_code": l.stock_code, "stock_name": l.stock_name,
            "sector_name": l.sector_name, "leader_type": l.leader_type,
            "board_height": l.board_height, "leader_score": l.leader_score,
            "risk_score": l.risk_score,
        }
        for l in leaders
    ]

    # ── 写入 DailyReview ─────────────────────────────────────────────────
    db.query(DailyReview).filter(DailyReview.date == today).delete()
    review = DailyReview(
        date=today,
        market_phase=phase,
        profit_effect_score=round(profit, 1),
        loss_effect_score=round(loss, 1),
        strong_pool_avg_pct=strong_pool_avg_pct,
        overall_up_count=overall_up,
        overall_down_count=overall_down,
        overall_limit_up_count=overall_lu,
        overall_limit_down_count=overall_ld,
        emotional_temperature=round(temp, 1),
        suggested_position_level=round(position, 1),
        emotion_cycle=cycle,
        strong_sectors=strong_sectors_list,
        dangerous_sectors=dangerous_sectors_list,
        active_sectors=active_sectors_json,
        dragon_changes=dragon_changes_json,
        profit_effect_groups=profit_groups,
        profit_effect_sectors=profit_sectors_json,
        market_summary=(
            f"[{today}] 自动生成 — 情绪温度 {round(temp, 1)}，"
            f"市场阶段 {phase}，强势股均涨幅 {strong_pool_avg_pct}%，"
            f"涨停 {overall_lu} 只"
        ),
    )
    db.add(review)
    db.commit()
    print(f"  DailyReview 已写入: 龙头 {len(dragon_changes_json)} 只，"
          f"活跃板块 {len(active_sectors_json)} 个，"
          f"赚钱效应分组 {len(profit_groups or [])} 组，"
          f"板块快照 {len(profit_sectors_json or [])} 个")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_daily_update(target_date: date, skip_boards: bool = False) -> dict:
    """
    执行每日更新。返回汇总 dict：
      {"degraded": bool, "warnings": [str]}
    degraded=True 表示有数据源 API 降级（已回退 DB），界面应提示数据可能不完整/过时。
    抛异常的硬失败由调用方捕获，不在此返回。
    """
    log = StepLogger(target_date)
    db = SessionLocal()
    api_warnings: list[str] = []   # API 降级告警（供界面提示，数据可能不完整或过时）

    def _result() -> dict:
        return {"degraded": bool(api_warnings), "warnings": list(api_warnings)}

    try:
        init_db()

        # ── 筛选条件加载 ─────────────────────────────────────────
        criteria = get_active_criteria(db)
        if not criteria:
            log.info("❌ 未找到生效的筛选条件，请先运行 scripts/init_screening.py")
            return _result()
        log.info(f"筛选条件: {criteria.name} | "
                 f"连板>={criteria.min_board_count_60d+1} | "
                 f"60日涨停>={criteria.min_limit_up_days_60d+1} | "
                 f"10日涨停>={criteria.min_limit_up_days_10d+1} | "
                 f"20日涨幅前{criteria.top_pct_rank_20d}%")

        # ── 第1步：确定候选股（通过东财选股 API）──────────────────
        log.begin("确定候选股")

        # 读取可编辑的选股 API prompt（界面可改；未设置则用默认常量）
        from app.services.pool_config_service import get_pool_keywords
        _kw = get_pool_keywords(db)
        strong_kw, limit_kw, turnover_kw = _kw["strong_pool_keyword"], _kw["limit_move_keyword"], _kw["turnover_pool_keyword"]
        log.info(f"强势池 prompt（{'自定义' if _kw['is_strong_custom'] else '默认'}）: {strong_kw}")
        log.info(f"涨跌停 prompt（{'自定义' if _kw['is_limit_custom'] else '默认'}）: {limit_kw}")

        # 并发调三个东财选股 API（with_names=True 顺带带回股票名，省去全市场拉取）
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as ex:
            fut_strong   = ex.submit(fetch_strong_pool_codes, keyword=strong_kw, with_names=True)
            fut_limit    = ex.submit(fetch_limit_move_codes, keyword=limit_kw, with_detail=True)
            fut_turnover = ex.submit(fetch_turnover_top_stocks, turnover_kw)
            api_pool_names    = fut_strong.result()    # {code: name}
            api_limit_detail  = fut_limit.result()     # {code: {"name", "limit_dir"}}
            api_turnover_rows = fut_turnover.result()  # [{"code","name",...}, ...]

        api_pool_codes    = set(api_pool_names)
        api_limit_codes   = set(api_limit_detail)
        api_turnover_codes = {row["code"] for row in api_turnover_rows}
        if api_turnover_codes:
            log.info(f"成交额选股 API: {len(api_turnover_codes)} 只（并入候选股，补全强势股字段）")
        # 用于刷新已知候选股的 name，并给新股直接命名，替代全市场列表拉取——三路来源
        # 都可以用来补名字。
        api_name_map: dict[str, str] = {
            **api_pool_names,
            **{c: d["name"] for c, d in api_limit_detail.items()},
            **{row["code"]: row["name"] for row in api_turnover_rows},
        }
        # 真实bug修复（2026-08-25，外部评审指出）：is_st=False（摘帽自动修正）此前
        # 只要出现在上面任一来源的合并map里就触发——但 TURNOVER_POOL_KEYWORD（"成交额
        # 排序前60；成交额大于20亿"）没有"非ST"这个筛选条件，不像STRONG_POOL_KEYWORD/
        # LIMIT_MOVE_KEYWORD那样明确写了"非ST"。一只真实ST股票只要成交额够大进了成交额
        # 前60榜单（ST股完全可能放量），就会被这个逻辑误判成"摘帽"清空is_st，进而影响
        # 涨跌停幅度判定（5%错判成10%）。只有明确带"非ST"筛选条件的两路来源才能触发
        # is_st=False这个修正，成交额来源只用来补名字，不参与ST状态判断。
        api_non_st_codes = api_pool_codes | api_limit_codes

        # 涨跌停 API 是否真正返回数据（决定是否以其为涨跌停的权威来源）。
        # 失败回退到 DB 时不做权威覆盖/对账，避免循环依赖与误清。
        limit_api_ok = bool(api_limit_codes)

        # 强势池：API 结果为准，失败回退 DB
        db_pool_codes = {
            s.code for s in db.query(Stock).filter(Stock.in_strong_pool == True).all()  # noqa
        }
        if api_pool_codes:
            strong_pool_codes = api_pool_codes
            log.info(f"强势股 API: {len(api_pool_codes)} 只")
            in_db_not_api = db_pool_codes - api_pool_codes
            in_api_not_db = api_pool_codes - db_pool_codes
            if in_db_not_api:
                log.info(f"  DB有但API无（待退出）: {sorted(in_db_not_api)}")
            if in_api_not_db:
                log.info(f"  API有但DB无（待入池）: {sorted(in_api_not_db)}")
        else:
            strong_pool_codes = db_pool_codes
            log.info(f"强势股 API 不可用，回退 DB {len(db_pool_codes)} 只")
            api_warnings.append("强势股选股 API 调用失败，已回退数据库历史，强势池可能未更新")

        if api_limit_codes:
            log.info(f"涨跌停 API: {len(api_limit_codes)} 只")
        else:
            # 涨跌停 API 失败时回退：从昨日快照补充
            api_limit_codes = {
                row[0]
                for row in db.query(Stock.code)
                .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
                .filter(
                    StockDailySnapshot.date == target_date,
                    or_(
                        StockDailySnapshot.is_limit_up == True,    # noqa
                        StockDailySnapshot.is_limit_down == True,  # noqa
                    ),
                ).all()
            }
            log.info(f"涨跌停 API 不可用，回退 DB {len(api_limit_codes)} 只")
            api_warnings.append("涨跌停选股 API 调用失败，已回退数据库历史，今日涨跌停数据可能不完整或过时")

        # 候选股 = 强势池 ∪ 涨跌停 ∪ 成交额概览今日上榜（并入后，这些高成交额但不常涨跌停
        # /不常入强势池的大盘股，也能在 Stock 表里留下真实的龙头分/风险分/多周期涨幅，
        # 而不是永远缺失——否则「成交额概览」板块详情面板里这些股票只能显示"—"）
        all_candidate_codes = strong_pool_codes | api_limit_codes | api_turnover_codes

        # 强势池回收：DB 标记 in_strong_pool 但已不在选股 API 结果的「待退出」股，
        # 并入候选重抓 → 走入池判断(in_pool=code in strong_pool_codes=False)自动回收，
        # 同时补今日快照。否则它们永久滞留强势池(幽灵)、且 today_* 长期陈旧。
        # 仅在强势池 API 成功时执行（失败回退 DB 时 strong_pool_codes==db_pool_codes，无差集）。
        if api_pool_codes:
            retire_codes = db_pool_codes - strong_pool_codes
            if retire_codes:
                all_candidate_codes = all_candidate_codes | retire_codes
                log.info(f"强势池回收：并入 {len(retire_codes)} 只待退出股重抓并回收")

        # 涨跌停复核：当日快照已标涨跌停、但已不在 API 名单里的股票，并入候选重抓，
        # 以收盘价重算（解决盘中涨跌停、尾盘打开后因退出候选集而状态无法更新的问题）。
        if limit_api_ok:
            recheck_codes = {
                row[0]
                for row in db.query(Stock.code)
                .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
                .filter(
                    StockDailySnapshot.date == target_date,
                    or_(
                        StockDailySnapshot.is_limit_up == True,    # noqa: E712
                        StockDailySnapshot.is_limit_down == True,  # noqa: E712
                    ),
                    Stock.code.notin_(api_limit_codes),
                ).all()
            }
            if recheck_codes:
                all_candidate_codes = all_candidate_codes | recheck_codes
                log.info(f"涨跌停复核：并入 {len(recheck_codes)} 只（曾标记今已退出名单）重抓")

        # 从 DB 构建 StockBasicInfo（已知股票直接读库，未知股票创建 stub）
        known_stocks = {
            s.code: s
            for s in db.query(Stock).filter(Stock.code.in_(all_candidate_codes)).all()
        }

        # 刷新已知候选股的 name / is_st：名字由选股 API 直接带回（api_name_map，三路
        # 来源都可以补名字）；is_st=False 只由明确带「非ST」筛选条件的来源触发
        # （api_non_st_codes，不包括成交额来源，见上面注释）。
        refreshed = 0
        for code, s in known_stocks.items():
            fresh = api_name_map.get(code)
            is_confirmed_non_st = code in api_non_st_codes
            if fresh and (s.name != fresh or (s.is_st and is_confirmed_non_st)):
                s.name = fresh
                if is_confirmed_non_st:
                    s.is_st = False
                refreshed += 1
        if refreshed:
            log.info(f"  刷新股票名称/ST状态 {refreshed} 只（选股API）")

        # 仍缺名字的新代码（不在选股结果里，如复核股/极少数新股）才回退全市场列表补名
        new_codes = all_candidate_codes - set(known_stocks.keys())
        unnamed_new = {c for c in new_codes if c not in api_name_map}
        fallback_name_map: dict[str, str] = {}
        if unnamed_new:
            log.info(f"  {len(unnamed_new)} 只新代码不在选股结果，回退全市场列表补名...")
            try:
                fallback_name_map = {s.code: s.name for s in fetch_main_board_stocks()}
            except Exception as e:
                log.info(f"  全市场列表补名失败（忽略，用 code 占位）: {e}")

        candidates: List[StockBasicInfo] = []
        for code in all_candidate_codes:
            s = known_stocks.get(code)
            if s:
                candidates.append(StockBasicInfo(
                    code=s.code,
                    name=s.name,
                    market=1 if s.market == "SH" else 0,
                    is_st=s.is_st,
                    pct_change=0.0,
                    turnover_rate=0.0,
                    listing_date=getattr(s, "ipo_date", None),
                ))
            else:
                # 新股：按代码前缀推断市场；名称优先选股API，其次全市场列表，最后用 code 占位
                # 注意：北交所(920/8/4 开头)东财 secid 用 market=0；仅 6/5/900(沪B) 为沪市
                mkt = 1 if code.startswith(("6", "5", "900")) else 0
                real_name = api_name_map.get(code) or fallback_name_map.get(code) or code
                is_st_new = "ST" in real_name   # 选股结果非ST→False；fallback名字含ST则True
                stub = Stock(
                    code=code,
                    name=real_name,
                    market="SH" if mkt == 1 else "SZ",
                    is_st=is_st_new,
                    is_new_stock=False,
                )
                db.add(stub)
                db.flush()
                candidates.append(StockBasicInfo(
                    code=code, name=real_name, market=mkt,
                    is_st=is_st_new, pct_change=0.0, turnover_rate=0.0,
                ))
        db.commit()

        stock_map: Dict[str, StockBasicInfo] = {c.code: c for c in candidates}

        if not candidates:
            log.end(ok=False, detail="无候选股，退出")
            return _result()

        lu_cnt = len(api_limit_codes - strong_pool_codes)
        ld_cnt = 0   # API 不区分涨停/跌停，统计合并
        log.end(detail=(
            f"强势池 {len(strong_pool_codes)} 只，涨跌停 {len(api_limit_codes)} 只，"
            f"候选共 {len(candidates)} 只（去重后）"
        ))

        # 预取板块龙头 codes
        from app.models.sector import StockSectorRelation
        leader_code_set: set = {
            row[0]
            for row in db.query(Stock.code)
            .join(StockSectorRelation, StockSectorRelation.stock_id == Stock.id)
            .filter(StockSectorRelation.is_leader == True)  # noqa
            .all()
        }

        # ── 第3步：拉取 K 线 ─────────────────────────────────────
        log.begin("拉取K线数据")

        # 分组：DB 历史足够的只拉今日，其余拉完整 65 日
        db_klines_map, db_group, full_group = _build_klines_from_db(candidates, db, target_date)
        log.info(f"DB重建 {len(db_group)} 只（拉近2日），全量拉取 {len(full_group)} 只")

        # 全量拉取（新股 / 历史不足）：提高并发并去掉逐请求延迟。
        # 同一 K 线接口在 DB 重建组已用 20 并发/0 延迟稳定运行，这里取 15 留余量。
        full_klines = fetch_klines_batch(
            full_group, days=65, max_workers=15, delay_between=0.0,
        ) if full_group else {}

        # DB 重建组拉取天数：仅拉 2 天、payload 极小，可用更高并发（取 30 防限流）。
        # 边界2：按「DB 最新快照 → target_date」的最大缺口决定天数，避免连续停机多日后
        # days=2 只补 1 根、中间留空洞（MA60/连板数会偏差）。常态 gap=1 → 拉 3 天。
        db_fetch_days = 2
        if db_group:
            latest_hist = [bars[-1].date for bars in db_klines_map.values() if bars]
            if latest_hist:
                gap = (target_date - min(latest_hist)).days
                db_fetch_days = max(2, min(gap + 2, 65))
        if db_fetch_days > 3:
            log.info(f"  DB重建检测到缺口，拉取近 {db_fetch_days} 天补齐")
        today_klines = fetch_klines_batch(
            db_group, days=db_fetch_days, max_workers=30, delay_between=0.0,
        ) if db_group else {}

        # 高并发批量拉取下，个别股票会因限流/连接重置静默拉空（fetch_kline 内部
        # 东财→腾讯双重兜底都失败）。若不重试，这些股票会一直被下面"今日无数据，
        # 降级用历史"吞掉——今日涨跌停/连板数永久卡在缺today这天的旧值，且不会
        # 自愈：因为下次再跑，同样的高并发批量请求大概率又撞上同样的限流。
        # 低并发单独重试一轮：脱离批量并发环境后单只股票拉取成功率接近100%。
        #
        # 2026-08-25修复真实bug（外部评审指出）：判断"缺不缺今日数据"此前只看
        # `today_klines.get(code)` 是否非空——但接口完全可能返回非空list、却没有
        # target_date这一根（比如只返回到昨天），这种情况被当成"拉取成功"，既不
        # 会触发下面的低并发重试，也不会被下面 db_today_missing 计入degraded告警，
        # 是比"完全拉空"更隐蔽的一种静默过期。改成明确检查最后一根bar的日期是不是
        # target_date，而不是list是否为空。
        def _has_today_bar(bars: list) -> bool:
            return bool(bars) and bars[-1].date == target_date

        missing_codes = [info for info in db_group if not _has_today_bar(today_klines.get(info.code))]
        if missing_codes:
            log.info(f"  DB重建组 {len(missing_codes)} 只今日K线拉取失败，低并发重试...")
            retry_klines = fetch_klines_batch(
                missing_codes, days=db_fetch_days, max_workers=3, delay_between=0.3,
            )
            recovered = sum(1 for bars in retry_klines.values() if _has_today_bar(bars))
            if recovered:
                log.info(f"  重试补齐 {recovered}/{len(missing_codes)} 只")
            today_klines.update(retry_klines)

        # full_group 同样按日期判断缺不缺今日那一根 + 同样低并发重试（2026-08-25补齐）：
        # 上一轮把"非空≠有今日数据"这个判断只改到了 db_group，full_group 这边还停留在
        # "list非空就算成功"，于是一只返回了65根但最后一根是昨天的股票会被当成拉取成功，
        # 既不重试也不计入 degraded 告警——跟 db_group 那个已修的bug是同一个，只是漏改了
        # 另一半。全量拉取本身耗时更长，重试单独限制并发防止把限流打得更严重。
        full_missing_codes = [info for info in full_group if not _has_today_bar(full_klines.get(info.code))]
        if full_missing_codes:
            log.info(f"  全量组 {len(full_missing_codes)} 只今日K线缺失，低并发重试...")
            retry_full = fetch_klines_batch(
                full_missing_codes, days=65, max_workers=3, delay_between=0.3,
            )
            recovered_full = sum(1 for bars in retry_full.values() if _has_today_bar(bars))
            if recovered_full:
                log.info(f"  重试补齐 {recovered_full}/{len(full_missing_codes)} 只")
            # 只用真的补到今日那一根的结果覆盖，避免重试拿到一份更短/同样过期的历史
            # 反而把原来那份完整历史挤掉（全量组的历史窗口是后面所有指标的基础）。
            for code, bars in retry_full.items():
                if _has_today_bar(bars):
                    full_klines[code] = bars

        # 合并：历史快照 + 新拉 bar，按日期并集去重（新数据覆盖同日历史并补齐缺口日）
        klines_map: dict = {}
        for info in full_group:
            klines_map[info.code] = full_klines.get(info.code, [])
        for info in db_group:
            hist_bars = db_klines_map[info.code]
            new_bars = today_klines.get(info.code, [])
            if not new_bars:
                klines_map[info.code] = hist_bars  # 今日无数据，降级用历史
                continue
            by_date = {b.date: b for b in hist_bars}
            for b in new_bars:
                by_date[b.date] = b                # 新数据覆盖/补齐缺口
            klines_map[info.code] = [by_date[d] for d in sorted(by_date)]

        fetched = sum(1 for v in klines_map.values() if v)
        failed = len(candidates) - fetched

        # target_date 跟随「最新一根 K 线」自动修正：用所有股票的最大日期，
        # 避免个别停牌股的陈旧末日 bar 把 target_date 误导到过去。
        all_latest = [bars[-1].date for bars in klines_map.values() if bars]
        if all_latest:
            kline_latest_date = max(all_latest)
            if kline_latest_date != target_date:
                log.info(f"⚠️  {target_date} 非交易日/无当日数据，自动修正为 {kline_latest_date}")
                target_date = kline_latest_date

        # ── 定向行情兜底：只给"今日那一根还是没拉到"的少数股票补 ─────────────
        # 2026-08-25新增，针对上一轮修复后仍然存在的根因。此前的做法是"今日无数据
        # 就降级用历史"，然后在下游一个个字段去打补丁——但补丁只能救那几个能被独立
        # 权威来源交叉验证的字段（收盘价/涨幅/涨跌停），窗口统计算出来的连板数、
        # 涨停天数、10/20/60日涨幅、龙头分/风险分/情绪分/阶段全都还是基于旧bar的，
        # 而且它们在 compute_window_stats() 里一次性算完，比任何字段级补丁都更早。
        #
        # 与其在下游修字段，不如在上游把那一根真的补回来：拿实时行情快照构造一根
        # 当日 bar 塞进窗口，后面所有指标自动全部算对。请求量极小（只补缺的那几只，
        # 常态个位数），而且走的是腾讯/新浪两路——跟K线接口不是同一个源，K线这边
        # 被限流时这两路通常还是好的，正是需要兜底的那种场景。
        #
        # 关键约束：kline_bar_from_quote 会校验行情自身的 trade_date 必须等于
        # target_date，日期对不上（含数据源没给日期）一律拒绝。不校验就成了"用一个
        # 可能同样过期的源去修另一个过期的源"，等于换个门再犯一次同样的错。
        # 放在 target_date 自动修正之后：非交易日跑的时候 target_date 已经先被修正到
        # 真实的最后交易日，这里才不会把全市场都当成"缺今日数据"去拉一遍行情。
        # 一根历史都没有的股票不补：compute_window_stats 对空bars本来就返回None、
        # 整只跳过，硬塞一根孤零零的当日bar反而会让它带着"基于1根K线"的连板数/
        # N日涨幅/评分混进结果里，比跳过更糟。有历史的才补——补上去等于把K线接口
        # 本该返回的那一根还原回去，窗口统计口径完全不变。
        stale_infos = [
            info for info in candidates
            if klines_map.get(info.code) and not _has_today_bar(klines_map.get(info.code))
        ]
        if stale_infos:
            log.info(f"  {len(stale_infos)} 只今日K线仍缺失，用实时行情定向补当日bar...")
            try:
                quotes = fetch_stock_quotes_batch([(i.code, i.market) for i in stale_infos])
            except Exception as e:  # noqa: BLE001
                quotes = {}
                log.info(f"  ⚠️  行情兜底整体失败（{type(e).__name__}: {e}），保持K线过期状态")
            repaired = rejected_stale_quote = 0
            for info in stale_infos:
                q = quotes.get(info.code)
                if not q:
                    continue
                bar = kline_bar_from_quote(q, info.code, info.is_st, target_date)
                if not bar:
                    if q.trade_date != target_date:
                        rejected_stale_quote += 1
                    continue
                bars = [b for b in klines_map.get(info.code, []) if b.date != target_date]
                bars.append(bar)
                klines_map[info.code] = bars
                repaired += 1
            log.info(
                f"  行情兜底补回 {repaired}/{len(stale_infos)} 只当日bar"
                + (f"（另有 {rejected_stale_quote} 只行情自身日期也不是{target_date}，已拒绝）"
                   if rejected_stale_quote else "")
            )

        # 上一交易日：全体候选K线里 target_date 之前的最大日期。跟 target_date 自身
        # "取所有股票最新bar的最大值"同源——几百只股票一起取最大值，个别股票停牌/
        # 缺数据不会带偏。给下面涨跌停反推校验"前收价确实来自上一交易日"用。
        prev_dates = [b.date for bars in klines_map.values() for b in bars if b.date < target_date]
        prev_trading_date = max(prev_dates) if prev_dates else None

        # 今日数据缺失检测（疑似限流）：在行情兜底之后统计，反映的是"最终还是没有
        # 可信当日数据"的真实规模，而不是"K线接口这一路失败了多少"——后者已经被
        # 上面的重试和行情兜底救回来一部分，拿它告警会虚高。
        missing_today = sum(1 for info in candidates if not _has_today_bar(klines_map.get(info.code)))
        if candidates and missing_today / len(candidates) >= 0.1:
            api_warnings.append(
                f"K线今日数据缺失 {missing_today}/{len(candidates)} 只（疑似限流/接口异常），"
                f"今日涨跌停与评分可能不完整"
            )
            log.info(f"⚠️  今日数据缺失 {missing_today}/{len(candidates)} 只（K线重试+行情兜底后仍缺）")

        # 边界3：选股API数据日期须与（修正后的）target_date 一致，才以其为涨跌停权威。
        # 盘前等场景 API 可能返回另一交易日的数据，错配会把标志写到错误日期或误清对账。
        # 不一致时回退本地 K 线判定（不做权威覆盖/对账）。
        limit_dates = {d.get("limit_date") for d in api_limit_detail.values() if d.get("limit_date")}
        limit_authority_ok = limit_api_ok and (
            not limit_dates or (len(limit_dates) == 1 and next(iter(limit_dates)) == target_date)
        )
        if limit_api_ok and not limit_authority_ok:
            log.info(f"⚠️  选股API数据日期 {sorted(map(str, limit_dates))} ≠ target_date {target_date}，"
                     f"跳过涨跌停权威覆盖，回退本地K线判定")

        existing_snap_count = (
            db.query(StockDailySnapshot)
            .filter(StockDailySnapshot.date == target_date)
            .count()
        )
        if existing_snap_count > 0:
            log.info(f"ℹ️  {target_date} 已有 {existing_snap_count} 条快照（覆盖更新）")
        log.end(detail=f"成功 {fetched}/{len(candidates)} 只" + (f"，失败 {failed} 只" if failed else ""))

        # ── 第4步：计算指标 & 写入快照 ───────────────────────────
        log.begin("计算指标&写入快照")
        stats_list: List[StockWindowStats] = []
        for info in candidates:
            bars = klines_map.get(info.code, [])
            stats = compute_window_stats(
                code=info.code, name=info.name, is_st=info.is_st, bars=bars,
                new_stock_months=criteria.new_stock_months,
                listing_date=getattr(info, "listing_date", None),
                is_sector_leader=info.code in leader_code_set,
            )
            if stats:
                stats_list.append(stats)

        new_in_pool = removed_from_pool = total_in_pool = 0

        for stats in stats_list:
            # 入池判断：直接以选股 API 返回的代码集合为准
            in_pool = stats.code in strong_pool_codes
            info = stock_map.get(stats.code)
            if not info:
                continue
            was_in_pool = stats.code in db_pool_codes
            if in_pool and not was_in_pool:
                new_in_pool += 1
            elif not in_pool and was_in_pool:
                removed_from_pool += 1
            if in_pool:
                total_in_pool += 1
            # 这只股票的K线窗口是不是真的算到了今天。行情兜底已经在上游尽力补过
            # 一轮，走到这里还是 False 的，就是K线接口和腾讯/新浪行情都拿不到当日
            # 数据（或行情自身日期也过期）的少数情况。
            bar_fresh = stats.today_bar_date == target_date

            stock = _upsert_stock(db, info, stats, in_pool, derived_fresh=bar_fresh)
            db.flush()
            # 涨跌停以选股 API 名单为权威来源：方向取 API 显式字段 limit_dir，
            # 缺失时回退 pct 符号。规避本地用前收价反推跌停价的脆弱逻辑
            # （脏前收→漏判 / 北交所阈值缺失）。仅在数据日期与 target_date 一致时生效。
            #
            # 2026-08-25新增 not stats.is_st 这个条件（外部评审指出，核实属实）：
            # 涨跌停选股的Prompt是"非ST；非退市股票；涨停股票或者跌停股票"，它的
            # 全集天生就不含ST股。但候选池是三路并集，成交额选股那一路**没有**非ST
            # 筛选（生产上确实有 *ST威领 这样的ST股因成交额进候选）。于是一只今天
            # 真涨停的ST股：K线正确判出涨停 → 但它永远不会出现在涨跌停API名单里 →
            # detail=None → auth_lu=False 被当成权威值写回去，把正确结果覆盖成"没
            # 涨停"。这是典型的 Authority Universe ≠ Candidate Universe：一个数据源
            # 的权威性只在它真正覆盖的范围内成立，超出范围的"查不到"是"我不知道"，
            # 不是"不存在"。ST股这里退回本地K线判定（传None给_upsert_snapshot）。
            use_limit_authority = limit_authority_ok and not stats.is_st
            if use_limit_authority:
                detail = api_limit_detail.get(stats.code)
                if detail:
                    d = detail["limit_dir"]
                    if d is None:
                        # API 未给方向 → 回退当日 pct 符号，但**只在当日K线可信时**。
                        # 2026-08-25收紧（外部评审指出）：bar过期时 today_pct_change
                        # 是旧那天的涨跌幅，拿它猜方向可能把一只今天跌停的股票判成
                        # 涨停，再据此反推出一个凭空捏造的涨停价——比不判更糟。
                        # _parse_limit_dir 已经先试过显式字段再试过CHG字段才返回
                        # None，到这里的 None 是"权威源明确表示方向不可判定"，
                        # 业务层不该拿一份更差的本地旧数据替它重新编一个答案。
                        pct = stats.today_pct_change or 0.0
                        if bar_fresh:
                            d = "up" if pct > 0 else ("down" if pct < 0 else None)
                    auth_lu = d == "up"
                    auth_ld = d == "down"
                else:
                    auth_lu = auth_ld = False
            else:
                auth_lu = auth_ld = False

            # 真实bug修复（2026-08-25，用户在生产上发现凯莱英today_is_limit_up=True
            # 却today_pct_change=-6.86%自相矛盾）：K线拉取当日失败时会静默退回历史
            # 最后一根（今日无数据，降级用历史那段逻辑），bars[-1]其实是旧数据，
            # today_close_price/today_pct_change因此是错的；但涨跌停方向是从独立的
            # 选股API权威来源覆盖的，跟K线是否成功无关，于是出现"权威涨跌停标记正确，
            # 但价格/涨幅字段是几天前的旧值"这种组合。
            #
            # 三个字段组各自的可信度分开判（2026-08-25拆开，上一轮混成一个开关是
            # 外部评审确认的真实bug：涨跌停反推只重建得出价格和涨幅，重建不出换手率，
            # 却把换手率也一起标成了"新鲜"，于是旧那天的换手率盖着今天的日期入了库）：
            #   close_pct_fresh — bar是今天的就可信；bar过期但选股API确认了涨跌停
            #     方向时，用交易所规则精确反推同样可信（涨跌停当天的收盘价由规则唯一
            #     确定，不是估计值）。
            #   turnover_fresh  — 只有bar是今天的、**并且这个数据源真的提供了换手率**
            #     才可信。2026-08-25改成直接看 stats.today_turnover is not None，而不是
            #     另外维护一张"哪些股票走了哪条路"的边表：换手率知不知道是 KLineBar
            #     自己的属性，谁构造的这根bar谁最清楚，不该由下游去猜。当前腾讯/新浪
            #     K线都不提供换手率（而腾讯是主力源），所以这个值常态就是 None——
            #     此前它们统一写0.0冒充"已知0%"，生产库里因此每一天每只股票的换手率
            #     都是0.0，情绪分/龙头分里的换手因子事实上长期失效。
            #   bar_fresh       — 决定所有窗口统计/评分能不能写，见 _upsert_stock 注释。
            close_pct_fresh = bar_fresh
            turnover_fresh = bar_fresh and stats.today_turnover is not None
            if not close_pct_fresh and (auth_lu or auth_ld):
                prev_snap = (
                    db.query(StockDailySnapshot)
                    .filter(StockDailySnapshot.stock_id == stock.id, StockDailySnapshot.date < target_date)
                    .order_by(StockDailySnapshot.date.desc())
                    .first()
                )
                # prev_snap 必须**确实是上一个交易日**才能拿来当前收价（2026-08-25
                # 收紧，外部评审指出）。它原本只是"DB里 date<target_date 的最近一条"，
                # 中间完全可能缺了几天：拿 08-21 的收盘价去反推 08-25 的涨停价，算出
                # 来的是一个凭空捏造的价格，还会被标成 close_pct_fresh=True。
                # prev_trading_date 取自全体候选K线里 target_date 之前的最大日期——
                # 跟 target_date 自身的推导同源，几百只股票取最大值足够可靠，不需要
                # 等独立交易日历落地。证明不了就宁可没有：留着权威涨跌停标记，也比
                # 捏一个价格出来安全，这跟这轮"宁可缺失，不要伪造确定性"是同一条原则。
                if prev_snap and prev_snap.close_price and prev_snap.date == prev_trading_date:
                    actual_pct = get_actual_limit_pct(stats.code, stats.is_st)
                    stats.today_close_price, stats.today_pct_change = derive_limit_close_price(
                        prev_snap.close_price, actual_pct, is_up=auth_lu,
                    )
                    close_pct_fresh = True
                    log.info(f"[涨跌停修正] {stats.code} K线+行情当日均拉取失败(退回{stats.today_bar_date})，"
                             f"用权威涨跌停方向+前收价({prev_snap.date})反推今日收盘价={stats.today_close_price}"
                             f"/涨幅={stats.today_pct_change}%（换手率与连板数/评分等窗口指标无法反推，本次不更新）")
                elif prev_snap and prev_snap.close_price:
                    log.info(f"[反推放弃] {stats.code} 已确认涨跌停，但DB里最近的前一条快照是"
                             f"{prev_snap.date}，不是上一交易日{prev_trading_date}，中间有缺口，"
                             f"拿它当前收价会算出错误的涨跌停价——保留权威涨跌停标记，不反推价格")
            if not close_pct_fresh:
                log.info(f"[数据过期跳过] {stats.code} K线+行情当日均拉取失败(退回{stats.today_bar_date})"
                         f"且非已确认涨跌停，本次不更新今日快照，保留上次可信值")

            if use_limit_authority:
                _upsert_snapshot(db, stock, stats, target_date,
                                 is_limit_up=auth_lu, is_limit_down=auth_ld,
                                 close_pct_fresh=close_pct_fresh, turnover_fresh=turnover_fresh,
                                 derived_fresh=bar_fresh)
            else:
                _upsert_snapshot(db, stock, stats, target_date,
                                 close_pct_fresh=close_pct_fresh, turnover_fresh=turnover_fresh,
                                 derived_fresh=bar_fresh)

        db.commit()
        log.end(detail=f"快照写入 {len(stats_list)} 只，强势池: +{new_in_pool}/-{removed_from_pool}，当前 {total_in_pool} 只")

        # ── 第4.05步：历史快照自举 ────────────────────────────────
        # full_group 这次全量拉到的 65 日 K 线，把历史日(< target_date)一并落库，
        # 使该股下次更新即可走 DB 重建（仅拉今日）——每只股票全量拉取一生只发生一次。
        # 历史快照仅存 K 线原始字段（close_price/pct/换手/涨跌停标志），供窗口重建用。
        if full_group:
            fg_codes = [info.code for info in full_group]
            sid_by_code = {
                row[0]: row[1]
                for row in db.query(Stock.code, Stock.id).filter(Stock.code.in_(fg_codes)).all()
            }
            sids = list(sid_by_code.values())
            existing_pairs = {
                (r[0], r[1])
                for r in db.query(StockDailySnapshot.stock_id, StockDailySnapshot.date)
                .filter(StockDailySnapshot.stock_id.in_(sids)).all()
            } if sids else set()
            backfilled = 0
            for info in full_group:
                sid = sid_by_code.get(info.code)
                if not sid:
                    continue
                for bar in klines_map.get(info.code, []):
                    if bar.date >= target_date or (bar.close_price or 0) <= 0:
                        continue
                    if (sid, bar.date) in existing_pairs:
                        continue
                    db.add(StockDailySnapshot(
                        stock_id=sid, date=bar.date,
                        close_price=round(bar.close_price, 4),
                        pct_change=round(bar.pct_change or 0.0, 4),
                        # None＝该数据源没有换手率，落 NULL 而不是假的0.0
                        turnover_rate=(round(bar.turnover_rate, 4)
                                       if bar.turnover_rate is not None else None),
                        is_limit_up=bar.is_limit_up,
                        is_limit_down=bar.is_limit_down,
                        is_broken_board=bar.is_broken_board,
                        is_one_word_limit_up=bar.is_one_word_limit_up,
                        is_one_word_limit_down=bar.is_one_word_limit_down,
                    ))
                    existing_pairs.add((sid, bar.date))
                    backfilled += 1
            if backfilled:
                db.commit()
                log.info(f"历史快照自举：补录 {backfilled} 条（full_group {len(full_group)} 只，下次可走DB重建）")

        # ── 第4.1步：涨跌停对账 ──────────────────────────────────
        # 当日快照中仍标着涨跌停、但已不在选股 API 名单里的股票，强制清除标记。
        # 解决「盘中涨跌停、尾盘打开」的票因退出候选集而无法被后续更新修正的问题。
        # 仅在数据日期与 target_date 一致时执行，避免用错日期的名单误清。
        #
        # 2026-08-25新增 Stock.is_st == False 这个条件：这套对账的逻辑前提是
        # "api_limit_codes 是今日涨跌停的完整全集，不在里面就是没涨跌停"。但涨跌停
        # 选股的Prompt写死了"非ST"，这个全集对ST股根本不成立——一只今天真涨停的ST股
        # 永远不可能出现在名单里，于是每次跑都会被这里强制清掉涨停标记，等于系统性
        # 地否认所有ST股的涨跌停。ST股不参与这套negative reconciliation，只信本地
        # K线判定（跟上面写快照那里 use_limit_authority 的口径保持一致）。
        if limit_authority_ok:
            stale_snaps = (
                db.query(StockDailySnapshot)
                .join(Stock, Stock.id == StockDailySnapshot.stock_id)
                .filter(
                    StockDailySnapshot.date == target_date,
                    or_(
                        StockDailySnapshot.is_limit_up == True,    # noqa: E712
                        StockDailySnapshot.is_limit_down == True,  # noqa: E712
                    ),
                    Stock.code.notin_(api_limit_codes),
                    Stock.is_st == False,   # noqa: E712  权威名单不覆盖ST，不能拿它否认ST
                )
                .all()
            )
            for snap in stale_snaps:
                snap.is_limit_up = False
                snap.is_limit_down = False
                snap.is_one_word_limit_up = False
                snap.is_one_word_limit_down = False
            if stale_snaps:
                db.commit()
                log.info(f"涨跌停对账：清除过期标记 {len(stale_snaps)} 只")

        # ── 第4.5步：补全涨跌停股板块关联 ────────────────────────
        # 对今日涨跌停但 stock_sector_relations 为空的股票，
        # 实时拉取东方财富板块归属并建立关联，确保涨停池展示正确的板块
        log.begin("补全涨跌停板块关联")
        limit_move_stocks = (
            db.query(Stock)
            .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
            .filter(
                StockDailySnapshot.date == target_date,
                or_(
                    StockDailySnapshot.is_limit_up == True,    # noqa: E712
                    StockDailySnapshot.is_limit_down == True,  # noqa: E712
                ),
            )
            .all()
        )
        created_rels = _sync_missing_sector_relations(db, limit_move_stocks, log=log)
        log.end(detail=f"涨跌停共 {len(limit_move_stocks)} 只，补建关联 {created_rels} 条")

        # ── 第5步：刷新板块统计 & 阶段 & 排名tag ────────────────
        log.begin("刷新板块统计")
        _refresh_sector_stats(db, target_date)
        refresh_sector_phases(db)
        _refresh_sector_ranks(db)
        sector_count = db.query(Sector).count()
        watched_count = db.query(Sector).filter(Sector.is_watched == True).count()  # noqa
        log.end(detail=f"共 {sector_count} 个板块，关注 {watched_count} 个")

        # ── 第6步：更新主板块 ────────────────────────────────────
        log.begin("更新主板块")
        _update_primary_sectors(db)
        log.end()

        # ── 第7步：写入 DailyReview ──────────────────────────────
        log.begin("写入DailyReview")
        _save_daily_review(db, target_date)
        review = db.query(DailyReview).filter(DailyReview.date == target_date).first()
        log.end(detail=(
            f"市场阶段={review.market_phase}，温度={review.emotional_temperature:.0f}，"
            f"仓位={review.suggested_position_level:.0f}%"
        ) if review else "写入成功")

        # ── 重点监管名单同步（独立步骤，失败不影响主流程）──────────────────
        # 注：「即将进入监管」预警改用东财实时接口，无需本地指数偏离值管道。
        # 须在 log.summary() 之前执行——summary() 会关闭日志文件。
        try:
            from app.services.regulatory_service import sync_regulatory_unusual
            reg = sync_regulatory_unusual(db)
            if reg.get("ok"):
                log.info(f"重点监管名单同步完成：{reg.get('count')} 条")
            else:
                api_warnings.append("重点监管名单 API 调用失败，已保留旧名单")
        except Exception as e:
            log.info(f"[regulatory] 重点监管名单同步失败（不影响主流程）: {e}")
            db.rollback()

        # ── 大盘趋势数据同步（独立步骤，失败不影响主流程）──────────────────
        # 指数日线（东财→腾讯→新浪兜底）+ 市场宽度（两融/涨跌统计/成交额）入库，
        # 大盘趋势页读库展示。
        try:
            from app.services.index_trend_service import sync_index_bars
            from app.services.windvane_service import sync_market_breadth
            r_idx = sync_index_bars(db)
            r_brd = sync_market_breadth(db)
            log.info(
                f"大盘趋势数据同步：指数 {r_idx['ok']}/5（upsert {r_idx['upserts']} 行）；"
                f"市场宽度 {r_brd['ok']}/3 模块"
            )
            for w in (r_idx.get("errors") or []) + (r_brd.get("errors") or []):
                api_warnings.append(f"大盘数据: {w}")
        except Exception as e:
            log.info(f"[market-trend] 大盘趋势数据同步失败（不影响主流程）: {e}")
            db.rollback()

        # ── 成交额概览数据同步（独立步骤，失败不影响主流程）────────────────
        # 每天存一份成交额前列快照，供成交额概览页面按板块聚合赚钱效应、
        # 跟前一天名单对比标「新进」股票；靠自愈懒同步无法保证每天都有存档
        # （没人手动刷新页面就不会触发），所以在这里主动跑一次。
        try:
            from app.services.turnover_service import sync_turnover_pool
            r_tov = sync_turnover_pool(db)
            log.info(f"成交额概览数据同步：{r_tov['count']} 只")
            for w in (r_tov.get("errors") or []):
                api_warnings.append(f"成交额概览: {w}")
        except Exception as e:
            log.info(f"[turnover] 成交额概览数据同步失败（不影响主流程）: {e}")
            db.rollback()

        # ── 涨停板块雷达：当日涨停/炸板明细归档（独立步骤，失败不影响主流程）──
        # 盘后跑到的是当天的最终封板状态（封单额/最终封板时间不会再变），作为正式
        # 存档。页面上的手动刷新是同一个同步函数，盘中随时可以把最新状态刷进来，
        # 两者写同一张表、互相覆盖没有冲突。
        # 放在这里而不是更靠前：它只写自己的两张新表，跟本次更新的 K线/评分/板块
        # 统计没有任何依赖关系；用 try 包住是为了保证东财这个接口挂了也绝不拖垮
        # 已有的 KLine/Stock/Sector 主链路——涨停明细缺一天，页面显示上一份并标注
        # 时间即可，比整个 daily_update 失败的代价小得多。
        try:
            from app.services.limit_up_detail_service import (
                sync_limit_up_details, sync_core_recall_codes,
            )
            lu_n, bb_n, lu_warnings = sync_limit_up_details(db, target_date)
            # 板块核心召回名单：本地从快照数涨停日会因历史缺口偏低（实测覆盖率94.3%），
            # 偏低就是漏召回，用东财服务端算好的结果兜底，见 CORE_RECALL_KEYWORD 注释
            recall_n = sync_core_recall_codes(db, target_date)
            log.info(f"涨停板块雷达：涨停明细 {lu_n} 只 / 炸板明细 {bb_n} 只 / 核心召回名单 {recall_n} 只")
            for w in lu_warnings:
                log.info(f"  {w}")
        except Exception as e:
            log.info(f"[limit-up-radar] 涨停明细归档失败（不影响主流程）: {e}")
            api_warnings.append("涨停明细归档失败，涨停板块雷达可能缺少当日数据")
            db.rollback()

        # ── 弱转强雷达：板块每日快照 + 候选池发现（独立步骤，失败不影响主流程）──
        # 板块快照必须在第5步「刷新板块统计」之后跑（依赖今日 Sector 字段已是最新），
        # 候选发现依赖 Stock.pct_change_20d / close_price 等同样由本次更新写入的字段。
        try:
            from app.services.w2s_sector_gate_service import upsert_sector_daily_snapshot
            from app.services.w2s_candidate_service import discover_candidates
            snap_count = upsert_sector_daily_snapshot(db, target_date)
            cand_stats = discover_candidates(db, target_date)
            log.info(
                f"弱转强雷达：板块快照 {snap_count} 条；候选池 raw={cand_stats['prompt1_raw']}/{cand_stats['prompt2_raw']}，"
                f"verified={cand_stats['verified']}（新增{cand_stats['new']}，续期{cand_stats['renewed']}，失活{cand_stats['expired']}）"
            )
        except Exception as e:
            log.info(f"[weak-to-strong-radar] 板块快照/候选池发现失败（不影响主流程）: {e}")
            db.rollback()

        # ── 弱转强雷达：候选状态刷新（独立步骤，失败不影响主流程）──────────────
        # 候选发现只维护"名单"（新增/续期/失活），不会重算已有候选的 price/结构态/
        # BUYABLE 判断——那部分只有用户手动点「刷新数据并重新评估」才会算。这意味着
        # 如果用户当天没打开页面点刷新，候选列表会一直停在上次手动刷新时的旧状态，
        # 哪怕候选名单本身每天都在正常更新（2026-08-25 用户指出的真实缺口）。这里在
        # 收盘后批量更新的同一个时间点顺手跑一次，保证至少每天有一次收盘后的准确
        # 状态落库，不依赖用户当天有没有点开页面——这跟之前移除的09:26盘中自动刷新
        # 不是一回事：09:26那次是"盘中高频轮询"的第一步，已经确认对判断没有增量
        # 价值而移除；这里是跟其余所有步骤同频（每天一次、收盘后）的批量收尾，性质
        # 上更接近"写入复盘"这类日终归档，不是新增自动轮询。用跟手动/refresh接口
        # 同一把锁文件，避免撞上用户手动点刷新的并发写入。
        try:
            import fcntl as _fcntl
            from app.services.w2s_refresh_service import run_refresh
            W2S_LOCK_FILE = "/tmp/tradeflux_w2s_radar.lock"
            lock_fd = open(W2S_LOCK_FILE, "w")
            try:
                _fcntl.flock(lock_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except BlockingIOError:
                log.info("[weak-to-strong-radar] 候选状态刷新跳过：锁已被占用（用户可能正在手动刷新）")
            else:
                try:
                    refresh_stats = run_refresh(db)
                    log.info(f"弱转强雷达：候选状态刷新 {refresh_stats}")
                finally:
                    _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
                    lock_fd.close()
        except Exception as e:
            log.info(f"[weak-to-strong-radar] 候选状态刷新失败（不影响主流程）: {e}")
            db.rollback()

        log.summary()

    except Exception as e:
        import traceback
        log.error(str(e))
        log.info(traceback.format_exc())
        log.summary()
        db.rollback()
        raise
    finally:
        db.close()
        from app.database import engine
        engine.dispose()

    # ── 板块同步（独立步骤，失败不影响主流程）──────────────────────────
    if not skip_boards:
        try:
            from scripts.sync_boards import run_sync_boards
            run_sync_boards()
        except Exception as e:
            print(f"[sync_boards] 板块同步失败（不影响主流程）: {e}")

    return _result()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradeFlux 每日复盘更新")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="指定更新日期，格式 YYYY-MM-DD，默认为今天",
    )
    parser.add_argument(
        "--skip-boards",
        action="store_true",
        default=False,
        help="跳过东财概念板块同步（sync_boards.py），节省约10分钟",
    )
    args = parser.parse_args()

    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = date.today()

    run_daily_update(target, skip_boards=args.skip_boards)
