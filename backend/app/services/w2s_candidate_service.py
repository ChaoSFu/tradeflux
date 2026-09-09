"""
弱转强雷达候选池发现。

## 来源（2026-09-09 改）

**不再自己跑两路盘前 Prompt**，改成读本地已有的三个股池：

    强势股    Stock.in_strong_pool
    涨停股    StockDailySnapshot.is_limit_up（当日）
    成交额池  TurnoverPoolDaily（当日）

前两个就是「活跃股池」那一页的两个 tab，第三个是「成交额概览」。这三份都由
daily_update 每天写好，读库即可——于是这一步**不再发任何外部请求**，也不再有
「Prompt 解析出错但没人发现」这条故障路径（原来的召回异常检测因此从"监控
Prompt"变成"监控这三个池子是不是突然空了"，保留）。

改这个来源的理由：候选池本来就该跟页面上看得见的股池是同一批票。原来两路
Prompt 是独立的第四套口径，用户在活跃股池里看到的票不一定在雷达里，反过来
也是——**同一个"哪些票值得看"的问题有两套答案，对不上时没人说得清哪个对。**

## 筛选条件没变

来源换了，但"什么叫弱转强的起点"这组结构条件原样保留（见 verify_setup_*）：
近20日强 + 昨日下跌，或 近20日强 + 昨收跌破MA5但仍在MA20上方。这两组条件才是
这个雷达的定义，不是来源。

昨日成交额条件不在本地复核范围（2026-08-22 定案，此前短暂加过又撤回）：
`StockDailySnapshot` 没有持久化逐日成交额，本来想用批量实时报价的 amount
顶替，但那个值在盘前/盘中调用时代表的是"当前累计成交额"，跟 Prompt 要校验的
"昨日全天成交额"根本是两个不同的变量，不是近似而是语义错误——尤其 09:27
那次运行，此时今天才刚竞价结束，用它当"昨日成交额"校验会产生系统性偏差，
误伤本该入选的候选。宁可完全信任东财自己的数值过滤（成交额这种无歧义标量
比较，东财自己算错的概率本来就低），也不用一个语义不对的替代变量冒充校验
结果。真要本地核验，需要先把逐日成交额补进 StockDailySnapshot（意味着要
扩展 K 线重建管线），是独立的、更大的改动，不是这里能顺手做的。

命中续期：连续多天没有再次命中任一 Prompt 的候选，超过观察窗口天数后
is_active 置 False（不物理删除，保留历史）。
"""
from __future__ import annotations

from datetime import date as date_cls
from typing import Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from ..models.stock import Stock, StockDailySnapshot
from ..models.weak_to_strong_radar import WeakToStrongCandidate, WeakToStrongDiscoveryRun
from ..models.turnover_pool import TurnoverPoolDaily
from . import w2s_config_service as cfg


def compute_ma(closes: list[float], window: int) -> Optional[float]:
    """纯函数：最近 window 个收盘价的简单均线，不足 window 个返回 None（不用不完整数据冒充均线）。"""
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def compute_pct20_percentile(pct_change_20d: float, universe: list[float]) -> float:
    """纯函数：pct_change_20d 在 universe（全市场 Stock.pct_change_20d）里的百分位，0-1，越大越靠前。"""
    if not universe:
        return 0.0
    below = sum(1 for v in universe if v < pct_change_20d)
    return below / len(universe)


def detect_recall_anomaly(
    current_total_raw: int, historical_totals: list[int],
    min_history: int = 3, low_ratio: float = 0.3, high_ratio: float = 3.0,
) -> Optional[str]:
    """
    纯函数（Prompt Parser Monitor，2026-08-23新增）：候选召回数量异常检测。
    历史样本不足（<min_history次）时不判断——没有基准，强行判断只会制造假
    警报。当前值相对历史均值过低（可能是东财把 Prompt 解析逻辑改坏、召回
    大幅萎缩）或过高（可能是条件被错误放宽，混进了不该出现的股票）都标记，
    只返回一行人类可读原因，不做任何自动纠正——发现异常应该是人工去核实
    东财这次到底解析成了什么，而不是系统自己重试或悄悄换一套逻辑掩盖过去。
    """
    if len(historical_totals) < min_history:
        return None
    avg = sum(historical_totals) / len(historical_totals)
    if avg <= 0:
        return None
    ratio = current_total_raw / avg
    if ratio < low_ratio:
        return f"候选召回数量({current_total_raw})显著低于近{len(historical_totals)}次均值({avg:.0f})，疑似Prompt解析异常或接口降级"
    if ratio > high_ratio:
        return f"候选召回数量({current_total_raw})显著高于近{len(historical_totals)}次均值({avg:.0f})，疑似Prompt条件被错误放宽"
    return None


def verify_setup_pullback(
    *,
    limit_up_days_20d: int,
    pct20_percentile: float,
    yesterday_pct_change: Optional[float],
) -> bool:
    """
    形态一：**近20日强过、昨天回落**。
    （近20日有涨停 或 近20日涨幅前20%）且 昨日下跌。

    这是"弱转强"里"弱"的那一半——没有昨天的回落就没有今天的转强可言。
    """
    if yesterday_pct_change is None or yesterday_pct_change >= 0:
        return False
    return limit_up_days_20d > 0 or pct20_percentile >= 0.8


def verify_setup_ma_squeeze(
    *,
    pct20_percentile: float,
    yesterday_close: Optional[float],
    ma5: Optional[float],
    ma20: Optional[float],
) -> bool:
    """
    形态二：**近20日涨幅前20%，昨收跌破 MA5 但仍站在 MA20 上方**。
    趋势没破、只是短期回踩到位。

    MA5/MA20 数据不足（新股/次新）时保守判 False，**不猜测**。
    """
    if pct20_percentile < 0.8:
        return False
    if yesterday_close is None or ma5 is None or ma20 is None:
        return False
    return yesterday_close < ma5 and yesterday_close > ma20


def _recent_closes(db: Session, stock_id: int, as_of: date_cls, limit: int = 20) -> list[float]:
    """最近 limit 个交易日的收盘价，按日期升序（用于算 MA），排除缺 close_price 的记录。"""
    rows = (
        db.query(StockDailySnapshot)
        .filter(
            StockDailySnapshot.stock_id == stock_id,
            StockDailySnapshot.date <= as_of,
            StockDailySnapshot.close_price.isnot(None),
        )
        .order_by(StockDailySnapshot.date.desc())
        .limit(limit)
        .all()
    )
    return [r.close_price for r in reversed(rows)]


def collect_source_pools(db: Session, as_of: date_cls) -> dict[str, set[str]]:
    """
    候选来源：**页面上看得见的那三个股池**，全部读库，零外部请求。

        strong    强势股（活跃股池「强势股」tab）
        limit_up  当日涨停股（活跃股池「涨跌停股」tab）
        turnover  成交额池（成交额概览）

    涨停股和成交额池都按「≤ as_of 的最近一个有数据的交易日」取，不硬钉 as_of：
    盘前跑的时候今天那批还没写出来，硬钉会得到空集，而**空集跟"今天真没有涨停"
    看起来一模一样**。
    """
    strong = {c for (c,) in db.query(Stock.code)
              .filter(Stock.in_strong_pool.is_(True)).all()}

    lu_date = (db.query(sqlfunc.max(StockDailySnapshot.date))
               .filter(StockDailySnapshot.date <= as_of,
                       StockDailySnapshot.is_limit_up.is_(True)).scalar())
    limit_up = {c for (c,) in db.query(Stock.code)
                .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
                .filter(StockDailySnapshot.date == lu_date,
                        StockDailySnapshot.is_limit_up.is_(True)).all()} if lu_date else set()

    to_date = (db.query(sqlfunc.max(TurnoverPoolDaily.date))
               .filter(TurnoverPoolDaily.date <= as_of).scalar())
    turnover = {c for (c,) in db.query(TurnoverPoolDaily.stock_code)
                .filter(TurnoverPoolDaily.date == to_date).all()} if to_date else set()

    return {"strong": strong, "limit_up": limit_up, "turnover": turnover}


def discover_candidates(db: Session, as_of: date_cls) -> dict:
    """
    从三个本地股池取来源 + 两组形态条件筛选，upsert 候选池。返回
    {"strong_raw", "limit_up_raw", "turnover_raw", "source_raw", "verified",
     "new", "renewed", "expired"}。

    raw 和 verified 都要报出来：**分不清"池子空了"和"池子里没有符合形态的票"，
    就等于没有监控**。
    """
    prompts = cfg.get_prompts(db)
    window_days = int(cfg.get_numeric(db, cfg.KEY_OBSERVATION_WINDOW_DAYS))

    pools = collect_source_pools(db, as_of)
    all_codes = set().union(*pools.values()) if pools else set()

    universe_pct20 = [v for (v,) in db.query(Stock.pct_change_20d).all()]

    # code -> 它来自哪几个池子（不是"命中哪条 Prompt"）。同一只票可能三个池子
    # 都在，逗号连起来存，让人一眼看出这只票是从哪儿进来的
    verified_by_code: dict[str, str] = {}
    if all_codes:
        stocks = db.query(Stock).filter(Stock.code.in_(all_codes)).all()
        for stock in stocks:
            closes = _recent_closes(db, stock.id, as_of, limit=20)
            ma5 = compute_ma(closes, 5)
            ma20 = compute_ma(closes, 20)
            yday = (
                db.query(StockDailySnapshot)
                .filter(StockDailySnapshot.stock_id == stock.id, StockDailySnapshot.date <= as_of)
                .order_by(StockDailySnapshot.date.desc())
                .first()
            )
            pct20_pctl = compute_pct20_percentile(stock.pct_change_20d, universe_pct20)

            # 来源池不再决定用哪条判据——三个池子里的票都拿同样两组形态条件筛，
            # 命中任一即算候选。来源只记录"它从哪个池子进来的"
            hit = verify_setup_pullback(
                limit_up_days_20d=stock.limit_up_days_20d,
                pct20_percentile=pct20_pctl,
                yesterday_pct_change=(yday.pct_change if yday else None),
            ) or verify_setup_ma_squeeze(
                pct20_percentile=pct20_pctl,
                yesterday_close=(yday.close_price if yday else None),
                ma5=ma5, ma20=ma20,
            )
            if hit:
                src = [k for k, codes in pools.items() if stock.code in codes]
                verified_by_code[stock.code] = ",".join(src)[:20] or "unknown"

    stats = {
        **{f"{k}_raw": len(v) for k, v in pools.items()},
        "source_raw": len(all_codes),
        "verified": len(verified_by_code), "new": 0, "renewed": 0, "expired": 0,
    }
    # 召回异常检测放在这个早退分支之前——0候选/召回骤降正是Prompt Parser Monitor
    # 最需要抓到的情况，不能被"没有可处理的候选"这条早退路径悄悄跳过。
    _log_discovery_run(db, as_of, prompts, raw1_count=len(pools.get("strong", ())),
                       raw2_count=len(pools.get("limit_up", ())),
                       verified_count=stats["verified"])
    if not verified_by_code:
        db.commit()
        return stats

    stock_by_code = {
        s.code: s for s in db.query(Stock).filter(Stock.code.in_(verified_by_code)).all()
    }
    existing = {
        c.stock_code: c
        for c in db.query(WeakToStrongCandidate)
        .filter(WeakToStrongCandidate.stock_code.in_(verified_by_code))
        .all()
    }

    for code, source in verified_by_code.items():
        stock = stock_by_code.get(code)
        if stock is None:
            continue
        cand = existing.get(code)
        if cand is None:
            cand = WeakToStrongCandidate(
                stock_id=stock.id, stock_code=code, stock_name=stock.name,
                first_seen_date=as_of, last_seen_date=as_of,
                consecutive_miss_days=0, candidate_source=source, is_active=True,
            )
            db.add(cand)
            stats["new"] += 1
        else:
            cand.last_seen_date = as_of
            cand.consecutive_miss_days = 0
            cand.candidate_source = source
            if not cand.is_active:
                cand.is_active = True
                stats["renewed"] += 1
        cand.stock_name = stock.name

    # 本次未命中、之前是 active 的候选：miss 天数 +1，超窗口才失活
    missed = (
        db.query(WeakToStrongCandidate)
        .filter(WeakToStrongCandidate.is_active == True, ~WeakToStrongCandidate.stock_code.in_(verified_by_code))  # noqa: E712
        .all()
    )
    for cand in missed:
        cand.consecutive_miss_days = (cand.consecutive_miss_days or 0) + 1
        if cand.consecutive_miss_days > window_days:
            cand.is_active = False
            stats["expired"] += 1

    db.commit()
    return stats


def _log_discovery_run(
    db: Session, as_of: date_cls, prompts: dict, *, raw1_count: int, raw2_count: int, verified_count: int,
) -> None:
    """
    薄封装：写一条 WeakToStrongDiscoveryRun + 跟最近10次历史总召回量比较，
    异常时标记 is_anomaly/anomaly_reason（只记录，不自动处理，见 detect_recall_anomaly）。
    """
    recent = (
        db.query(WeakToStrongDiscoveryRun)
        .order_by(WeakToStrongDiscoveryRun.timestamp.desc())
        .limit(10)
        .all()
    )
    historical_totals = [r.prompt1_raw_count + r.prompt2_raw_count for r in recent]
    current_total = raw1_count + raw2_count
    anomaly_reason = detect_recall_anomaly(current_total, historical_totals)
    db.add(WeakToStrongDiscoveryRun(
        run_date=as_of, prompt1_text=prompts["prompt1"], prompt2_text=prompts["prompt2"],
        prompt1_raw_count=raw1_count, prompt2_raw_count=raw2_count, verified_count=verified_count,
        is_anomaly=anomaly_reason is not None, anomaly_reason=anomaly_reason,
    ))
