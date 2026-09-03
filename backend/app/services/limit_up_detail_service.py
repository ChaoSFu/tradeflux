"""
涨停明细同步：外部接口 → LimitUpDailyDetail / BrokenBoardDailyDetail 落库。
2026-08-25新增（涨停板块雷达）。

这一层只做"把外部涨停事实存下来"，不做任何聚合、不碰 Stock/Sector/Snapshot。
聚合在 limit_up_radar_service 里，读的是本地库。
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models.app_config import AppConfig
from .fuyao_dump import FuyaoError, fetch_interval_returns, get_api_key
from ..models.stock import Stock
from ..models.limit_up_detail import LimitUpDailyDetail, BrokenBoardDailyDetail
from .eastmoney_fetcher import (
    CoreRecallDetail, StockBasicInfo, fetch_core_recall_details, fetch_klines_batch,
    market_label, market_int,
)
from .limit_up_detail_fetcher import (
    SOURCE_NAME, BrokenBoardDetail, LimitUpDetail, fetch_limit_up_details,
)

# 东财选股召回结果按交易日存一份（沿用仓库既有的 AppConfig KV 模式，不为一份
# 代码清单单独建表）
CORE_RECALL_KEY_PREFIX = "limit_up_radar:core_recall:"


def core_recall_key(trade_date: date) -> str:
    return f"{CORE_RECALL_KEY_PREFIX}{trade_date.isoformat()}"


def sync_core_recall(db: Session, trade_date: date) -> int:
    """
    用东财条件选股拉一份"近期活跃股 + 各自的滚动涨停统计"存下来。

    这是核心召回**唯一的权威口径**，本地从快照重算只是它拉取失败时的兜底。
    2026-08-25 用 002432/600664/002437 跟真实K线逐项核对，东财三只全对；而本地
    快照重算在 600664 上少了3次（近60日真实9次、快照里只有6次，漏了07-10/13/14），
    因为快照只在股票进候选池那天才写，历史有缺口就会数少。数少 ⇒ 漏召回，而
    "不能漏掉板块核心"是这个功能的第一原则。

    顺带拿到 CHG（今日涨跌幅），这解决了另一个局限：核心锚大多不在候选池内、
    没有当日快照，盘中原本看不出"老核心正/负反馈"。
    """
    details = fetch_core_recall_details()
    if not details:
        return 0                      # 拉空视为失败，不覆盖上一份（避免整体漏召回）
    payload = {
        c: {"n": d.name, "lu10": d.limit_up_days_10d, "lu20": d.limit_up_days_20d,
            "lu60": d.limit_up_days_60d, "mb": d.max_board_60d, "chg": d.pct_change,
            "ic10": d.interval_chg_10d, "ic20": d.interval_chg_20d, "ic60": d.interval_chg_60d}
        for c, d in details.items()
    }
    key = core_recall_key(trade_date)
    row = db.query(AppConfig).filter(AppConfig.key == key).first()
    val = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if row:
        row.value = val
    else:
        db.add(AppConfig(key=key, value=val))
    db.commit()
    return len(details)


def get_core_recall_details(db: Session, trade_date: date) -> Dict[str, CoreRecallDetail]:
    """读当日的东财召回数据；当天没有就退回最近一份（"近期活跃"隔天仍然成立）。"""
    row = db.query(AppConfig).filter(AppConfig.key == core_recall_key(trade_date)).first()
    if not row:
        row = (
            db.query(AppConfig)
            .filter(AppConfig.key.like(f"{CORE_RECALL_KEY_PREFIX}%"))
            .order_by(AppConfig.key.desc())
            .first()
        )
    if not row or not row.value:
        return {}
    try:
        raw = json.loads(row.value)
    except (ValueError, TypeError):
        return {}
    return {
        c: CoreRecallDetail(
            code=c, name=v.get("n", ""),
            limit_up_days_10d=v.get("lu10"), limit_up_days_20d=v.get("lu20"),
            limit_up_days_60d=v.get("lu60"), max_board_60d=v.get("mb"),
            pct_change=v.get("chg"),
            interval_chg_10d=v.get("ic10"), interval_chg_20d=v.get("ic20"),
            interval_chg_60d=v.get("ic60"),
        )
        for c, v in raw.items() if isinstance(v, dict)
    }


def _stock_id_map(db: Session, codes: List[str]) -> Dict[str, int]:
    if not codes:
        return {}
    return {
        row[0]: row[1]
        for row in db.query(Stock.code, Stock.id).filter(Stock.code.in_(codes)).all()
    }


def _ensure_stock(db: Session, code: str, name: Optional[str], market: Optional[int]) -> int:
    """
    涨停股不在 stocks 表里时建一个存根。涨停池是全市场的，完全可能出现从没进过
    候选池的股票（北交所、冷门票）；没有 stock_id 就只能整只丢掉，那正是这个功能
    最不该发生的事——漏掉一只涨停股比多一行存根严重得多。
    name 必须在 flush 前给上（stocks.name 是 NOT NULL）。
    """
    stock = Stock(
        code=code,
        name=name or code,
        market=market_label(code, market or 0),
    )
    db.add(stock)
    db.flush()
    return stock.id


def sync_limit_up_details(
    db: Session, trade_date: date, timeout: int = 20,
) -> Tuple[int, int, List[str]]:
    """
    拉取并 upsert 指定交易日的涨停/炸板明细。
    返回 (涨停写入数, 炸板写入数, 警告列表)。

    只写这两张新表，**不触碰** Stock 的评分/快照/板块统计等任何既有数据
    （唯一的例外是给完全没见过的涨停股补一行 stocks 存根，见 _ensure_stock）。
    外部接口失败时抛异常交给调用方处理——绝不删除已有数据，页面会继续显示上一份
    并明确标注刷新失败时间，这跟本仓库"stale ≠ fresh，但 stale 也好过没有"的
    数据可信度原则一致。
    """
    details, broken, warnings = fetch_limit_up_details(trade_date, timeout=timeout)
    now = datetime.now()

    all_codes = [d.code for d in details] + [b.code for b in (broken or [])]
    id_map = _stock_id_map(db, all_codes)

    lu_written = _upsert_limit_ups(db, details, id_map, trade_date, now)
    bb_written = _upsert_broken_boards(db, broken, id_map, trade_date, now) if broken is not None else 0

    # 当日已不在涨停名单里的旧行要清掉：盘中多次刷新时，一只14:30炸板打开的股票
    # 会从涨停池消失、进入炸板池，如果不删除它上一次刷新留下的涨停行，页面会同时
    # 把它显示成"涨停"和"炸板"。以 target_date 当日为范围，不影响历史。
    #
    # **清理的前提是这次真的拿到了权威名单**（2026-08-26修，生产上真的删了数据）：
    # broken is None 表示炸板池根本没拉到，此时那份"空名单"不是事实而是故障，
    # 拿它去 prune 会把已有的 20 条炸板明细全删光——日志里"炸板池拉取失败
    # （ConnectTimeout）"和"清理旧行：炸板 20 条"就是同一次运行打出来的。
    # 上面 docstring 里"外部接口失败时绝不删除已有数据"这句话，到这里才真的成立。
    lu_removed = _prune_stale(db, LimitUpDailyDetail, trade_date, {d.code for d in details})
    bb_removed = (_prune_stale(db, BrokenBoardDailyDetail, trade_date, {b.code for b in broken})
                  if broken is not None else 0)
    if lu_removed or bb_removed:
        warnings.append(f"清理已不在名单中的旧行：涨停 {lu_removed} 条 / 炸板 {bb_removed} 条")

    db.commit()
    return lu_written, bb_written, warnings


def _upsert_limit_ups(
    db: Session, details: List[LimitUpDetail], id_map: Dict[str, int],
    trade_date: date, now: datetime,
) -> int:
    existing = {
        r.stock_code: r
        for r in db.query(LimitUpDailyDetail)
        .filter(LimitUpDailyDetail.trade_date == trade_date).all()
    }
    written = 0
    for d in details:
        sid = id_map.get(d.code) or _ensure_stock(db, d.code, d.name, d.market)
        id_map[d.code] = sid
        row = existing.get(d.code)
        if not row:
            row = LimitUpDailyDetail(stock_id=sid, stock_code=d.code, trade_date=trade_date)
            db.add(row)
        row.stock_id = sid
        row.stock_name = d.name or row.stock_name
        row.limit_reason = d.limit_reason
        row.limit_content = d.limit_content
        row.first_limit_time = d.first_limit_time
        row.last_limit_time = d.last_limit_time
        row.seal_amount = d.seal_amount
        row.broken_times = d.broken_times
        row.board_count = d.board_count
        row.limit_stat_days = d.limit_stat_days
        row.limit_stat_count = d.limit_stat_count
        row.price = d.price
        row.pct_change = d.pct_change
        row.amount = d.amount
        row.turnover_rate = d.turnover_rate
        row.float_market_cap = d.float_market_cap
        row.em_industry = d.em_industry
        row.source = SOURCE_NAME
        row.source_trade_date = trade_date
        row.refreshed_at = now
        written += 1
    return written


def _upsert_broken_boards(
    db: Session, broken: List[BrokenBoardDetail], id_map: Dict[str, int],
    trade_date: date, now: datetime,
) -> int:
    existing = {
        r.stock_code: r
        for r in db.query(BrokenBoardDailyDetail)
        .filter(BrokenBoardDailyDetail.trade_date == trade_date).all()
    }
    written = 0
    for b in broken:
        sid = id_map.get(b.code) or _ensure_stock(db, b.code, b.name, b.market)
        id_map[b.code] = sid
        row = existing.get(b.code)
        if not row:
            row = BrokenBoardDailyDetail(stock_id=sid, stock_code=b.code, trade_date=trade_date)
            db.add(row)
        row.stock_id = sid
        row.stock_name = b.name or row.stock_name
        row.first_limit_time = b.first_limit_time
        row.broken_times = b.broken_times
        row.pct_change = b.pct_change
        row.em_industry = b.em_industry
        row.price = b.price
        row.limit_price = b.limit_price
        row.board_count = b.board_count
        row.limit_stat_days = b.limit_stat_days
        row.limit_stat_count = b.limit_stat_count
        row.turnover_rate = b.turnover_rate
        row.amount = b.amount
        row.float_market_cap = b.float_market_cap
        row.amplitude = b.amplitude
        row.source = SOURCE_NAME
        row.source_trade_date = trade_date
        row.refreshed_at = now
        written += 1
    return written


def _prune_stale(db: Session, model, trade_date: date, keep_codes: set) -> int:
    rows = db.query(model).filter(model.trade_date == trade_date).all()
    removed = 0
    for r in rows:
        if r.stock_code not in keep_codes:
            db.delete(r)
            removed += 1
    return removed


def get_last_refreshed(db: Session, trade_date: date) -> Optional[datetime]:
    """这一交易日的涨停明细最后一次成功刷新的时刻（页面新鲜度展示用）。"""
    row = (
        db.query(LimitUpDailyDetail.refreshed_at)
        .filter(LimitUpDailyDetail.trade_date == trade_date)
        .order_by(LimitUpDailyDetail.refreshed_at.desc())
        .first()
    )
    return row[0] if row else None


def get_latest_detail_date(db: Session) -> Optional[date]:
    """库里最新有涨停明细的交易日——页面不传日期时默认展示它。"""
    row = (
        db.query(LimitUpDailyDetail.trade_date)
        .order_by(LimitUpDailyDetail.trade_date.desc())
        .first()
    )
    return row[0] if row else None


SCORES_KEY_PREFIX = "limit_up_radar:scores:"
INTERVAL_KEY_PREFIX = "limit_up_radar:interval_chg:"


def interval_chg_key(d: date) -> str:
    return f"{INTERVAL_KEY_PREFIX}{d.isoformat()}"


def get_interval_chg(db: Session, trade_date: date) -> Dict[str, dict]:
    """
    读当日补全的区间涨幅 {code: {"10": pct, "20": pct, "60": pct}}。

    这是东财核心召回 INTERVAL_CHG 的**补充而非替代**：召回名单里有的股票仍用
    东财的值（同一来源，口径统一），只有进不了召回名单的今日涨停股才走这里。
    跟 core_recall 不同，这份数据**不做隔天退回**——区间涨幅是逐日变化的，
    拿昨天的值贴今天的标签就是伪造。当天没有就是没有，页面显示 —。
    """
    row = db.query(AppConfig).filter(AppConfig.key == interval_chg_key(trade_date)).first()
    if not row or not row.value:
        return {}
    try:
        return json.loads(row.value) or {}
    except (ValueError, TypeError):
        return {}


def backfill_interval_chg(db: Session, trade_date: date, codes_markets,
                          max_workers: int = 12, delay: float = 0.0) -> Tuple[int, List[str]]:
    """
    给"区间涨幅缺失"的股票逐只补全，写进 AppConfig。返回补到的股票数。

    并发 12（2026-08-26 实测后从 3 提上来）。起初压到 3，是因为 fuyao 的 QPS 上限
    文档没写、错误码只有一个 `4001 频率超限 | 超过约定 QPS`，而当时唯一的并发实测
    打的是 prices/snapshot——那个端点返回一行，这个返回 200+ 根 bar，重两个数量级，
    限流常按端点或响应成本分别设，结论不能直接搬。

    后来对 **prices/historical 本身**实测：12 并发 × 24 次全部 code=0，无 4001、
    无丢包。24 次正好是真实用量（一次性 22~50 只）的形状，突发测试直接适用。
    注意这只证明突发没问题，没有证明不存在持续速率限制——但我们一天只跑一次，
    突发就是全部。

    这个任务不在关键路径上（补不到就显示 —），但 dump 在。万一哪天真见到 4001，
    先怀疑是不是按 key 跨端点共享配额，别只盯着这一处。

    返回 (**本次这批里成功拿到数据的只数**, 失败明细)。**失败必须报出来**：第一版只返回一个数字，
    生产上 22 只补到 21 只，日志说不清那 1 只是"请求失败"还是"上市不满60个交易日
    所以本来就没有"——查了腾讯才发现 603615 有 81 根完整历史，是请求挂了。
    分不清故障和事实，就等于没有监控。

    codes_markets: [(6位代码, "SH"/"SZ"/"BJ")]
    """
    key = get_api_key()
    if not key or not codes_markets:
        return 0, []

    out: Dict[str, dict] = get_interval_chg(db, trade_date)
    failures: List[str] = []
    # 计的是**本次这批里成功拿到数据的只数**，跟传进来的 codes_markets 一一对应。
    # 这个数被改错过两次（2026-08-27 同一天）：
    #   一版返回 len(out)，而 out 是当天累计 → "18 只不在名单里，补到 20 只"，多报
    #   一版只计"新增覆盖" → "18 只…补到 1 只"，把 17 只刷新读成失败，少报
    # 两次都是同一个毛病：报出来的数跟句子里的另一个数对不上。分母是本次要补的
    # 18 只，分子就必须是这 18 只里成功的个数，别的口径放这句话里都是误导。
    succeeded = 0

    def _one(cm):
        code, suffix = cm
        time.sleep(delay)
        try:
            return code, fetch_interval_returns(key, code, suffix), None
        except FuyaoError as e:
            return code, None, str(e)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for code, res, err in ex.map(_one, codes_markets):
            if err:
                failures.append(f"{code}({err})")
                continue
            # 只存拿到值的窗口。None 不写——"没拿到"和"值是0"必须能分开，
            # 这是本仓库反复踩的同一个坑
            got = {str(w): v for w, v in (res or {}).items() if v is not None}
            if got:
                out[code] = got
                succeeded += 1
            else:
                # 请求成功但一个窗口都算不出 = 上市太短，是事实不是故障
                failures.append(f"{code}(历史不足)")

    if not succeeded:
        return 0, failures
    row = db.query(AppConfig).filter(AppConfig.key == interval_chg_key(trade_date)).first()
    if row:
        row.value = json.dumps(out, ensure_ascii=False)
    else:
        db.add(AppConfig(key=interval_chg_key(trade_date),
                         value=json.dumps(out, ensure_ascii=False)))
    db.commit()
    return succeeded, failures


def scores_key(trade_date: date) -> str:
    return f"{SCORES_KEY_PREFIX}{trade_date.isoformat()}"


def refresh_radar_scores(db: Session, trade_date: date, max_stocks: int = 200) -> int:
    """
    只给**这个页面上出现的股票**重算龙头分/风险分/情绪分，2026-08-25新增。

    背景：这两个分数是本仓库用65日K线窗口自己算的（compute_window_stats），东财给
    不了。而 daily_update 只对候选池内的股票重算，核心锚大多在池外——那里的分数是
    冻结旧值（跟九安医疗那个涨停次数的bug同一类）。用户希望点刷新按钮就能把这个
    页面的数据全部刷新，所以在这里补上。

    范围严格限定在页面上的股票：今日涨停股 ∪（东财召回名单 ∩ 关注板块成员）。
    3/3门槛下实测只有41只，41次K线拉取几秒钟就够，仍然属于"轻量手动刷新"。

    **结果不写进 Stock**，而是单独存一份页面作用域的 AppConfig。这是刻意的：
    刷新接口有一条硬保证"不改动主流程数据"（见 test_sync_never_touches_stock_
    scores_or_snapshots），盘中用半日K线算出来的分数写进 Stock 会污染强势池/板块
    情绪/龙头识别等一系列下游消费者。页面要的是"现在什么样"，主流程要的是"收盘
    定论"，两者不能共用一个字段。
    """
    from .screening_service import compute_window_stats
    from ..models.sector import Sector, StockSectorRelation

    lu_rows = (
        db.query(LimitUpDailyDetail.stock_id, LimitUpDailyDetail.board_count)
        .filter(LimitUpDailyDetail.trade_date == trade_date).all()
    )
    lu_ids = {r[0] for r in lu_rows}
    board_by_sid = {r[0]: (r[1] or 0) for r in lu_rows}

    watched_ids = [s.id for s in db.query(Sector.id).filter(Sector.is_watched == True).all()]  # noqa: E712
    rels = (
        db.query(StockSectorRelation.sector_id, StockSectorRelation.stock_id)
        .filter(StockSectorRelation.sector_id.in_(watched_ids)).all()
    ) if watched_ids else []

    # 只算**真正会显示在页面上**的股票：先按跟 build_radar 同一套门槛筛出达标板块，
    # 再取这些板块的成员。不筛的话召回集会覆盖全部关注板块——实测 200 只/11秒，
    # 筛完只剩几十只/几秒，才对得起"快速刷新"这个定位。
    from .limit_up_radar_service import DEFAULT_MIN_LIMIT_UP, DEFAULT_MIN_BOARD_HEIGHT
    by_sector: Dict[int, list] = {}
    for sec_id, sid in rels:
        by_sector.setdefault(sec_id, []).append(sid)
    member_ids = set()
    for sec_id, sids in by_sector.items():
        hits = [s for s in sids if s in lu_ids]
        if (len(hits) >= DEFAULT_MIN_LIMIT_UP
                and max((board_by_sid.get(s, 0) for s in hits), default=0) >= DEFAULT_MIN_BOARD_HEIGHT):
            member_ids.update(sids)

    recall_codes = set(get_core_recall_details(db, trade_date))

    stocks = db.query(Stock).filter(Stock.id.in_(lu_ids | member_ids)).all() if (lu_ids | member_ids) else []
    targets = [s for s in stocks if s.id in lu_ids or s.code in recall_codes]
    if not targets:
        return 0
    targets = targets[:max_stocks]

    leader_ids = {
        r.stock_id for r in
        db.query(StockSectorRelation.stock_id)
        .filter(StockSectorRelation.is_leader == True).all()  # noqa: E712
    }

    infos = [
        StockBasicInfo(
            code=s.code, name=s.name, market=market_int(s.market, s.code),
            is_st=bool(s.is_st), pct_change=0.0, turnover_rate=0.0,
        )
        for s in targets
    ]
    klines = fetch_klines_batch(infos, days=65, max_workers=15, delay_between=0.0)

    payload = {}
    for s in targets:
        bars = klines.get(s.code) or []
        stats = compute_window_stats(
            code=s.code, name=s.name, is_st=bool(s.is_st), bars=bars,
            is_sector_leader=s.id in leader_ids,
        )
        if not stats:
            continue
        payload[s.code] = {
            "ls": round(stats.leader_score, 1),
            "rs": round(stats.risk_score, 1),
            "es": round(stats.emotion_score, 1),
            "bd": stats.today_bar_date.isoformat(),   # 这份分数算到哪一天，供页面判断新鲜度
        }
    if not payload:
        return 0

    key = scores_key(trade_date)
    row = db.query(AppConfig).filter(AppConfig.key == key).first()
    val = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if row:
        row.value = val
    else:
        db.add(AppConfig(key=key, value=val))
    db.commit()
    return len(payload)


def get_radar_scores(db: Session, trade_date: date) -> Dict[str, dict]:
    """读页面作用域的分数。只认当天那一份——分数是"现在什么样"，隔天的没有意义。"""
    row = db.query(AppConfig).filter(AppConfig.key == scores_key(trade_date)).first()
    if not row or not row.value:
        return {}
    try:
        raw = json.loads(row.value)
    except (ValueError, TypeError):
        return {}
    return {c: v for c, v in raw.items() if isinstance(v, dict)}
