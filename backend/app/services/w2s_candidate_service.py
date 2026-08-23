"""
弱转强雷达候选池发现：跑两路盘前 Prompt（复用 eastmoney_fetcher 的智能选股
search-code 接口），本地用 StockDailySnapshot 二次校验排名/均线这类东财从不
返回中间解析结果、容易算错或跟本地口径不一致的条件，upsert 进
weak_to_strong_candidates。

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

from sqlalchemy.orm import Session

from ..models.stock import Stock, StockDailySnapshot
from ..models.weak_to_strong_radar import WeakToStrongCandidate, WeakToStrongDiscoveryRun
from .eastmoney_fetcher import fetch_strong_pool_codes
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


def verify_prompt1(
    *,
    limit_up_days_20d: int,
    pct20_percentile: float,
    yesterday_pct_change: Optional[float],
) -> bool:
    """
    纯函数：本地复核 Prompt1 里的排名/方向类条件（近20日有涨停 或 近20日涨幅
    前20%；昨日下跌）。成交额条件不在本地复核范围，见模块头注释。
    """
    if yesterday_pct_change is None or yesterday_pct_change >= 0:
        return False
    return limit_up_days_20d > 0 or pct20_percentile >= 0.8


def verify_prompt2(
    *,
    pct20_percentile: float,
    yesterday_close: Optional[float],
    ma5: Optional[float],
    ma20: Optional[float],
) -> bool:
    """
    纯函数：本地复核 Prompt2 里的排名/均线类条件（近20日涨幅前20%；昨收跌破
    MA5但仍高于MA20）。成交额条件不在本地复核范围，见模块头注释。
    MA5/MA20 数据不足（新股/次新）时保守判 False，不猜测。
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


def discover_candidates(db: Session, as_of: date_cls) -> dict:
    """
    跑两路 Prompt + 本地二次校验，upsert 候选池。返回统计信息
    {"prompt1_raw": n, "prompt2_raw": n, "verified": n, "new": n, "renewed": n, "expired": n}。
    任一路 Prompt 请求失败（返回空集）不阻断另一路，但整体候选数异常偏少时
    调用方（refresh_service/scheduler日志）应能看到 raw vs verified 的差异用于排查。
    """
    prompts = cfg.get_prompts(db)
    window_days = int(cfg.get_numeric(db, cfg.KEY_OBSERVATION_WINDOW_DAYS))

    raw1 = fetch_strong_pool_codes(keyword=prompts["prompt1"], with_names=True)
    raw2 = fetch_strong_pool_codes(keyword=prompts["prompt2"], with_names=True)
    all_codes = set(raw1) | set(raw2)

    universe_pct20 = [v for (v,) in db.query(Stock.pct_change_20d).all()]

    verified_by_code: dict[str, str] = {}  # code -> "prompt1" / "prompt2" / "both"
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

            hit1 = stock.code in raw1 and verify_prompt1(
                limit_up_days_20d=stock.limit_up_days_20d,
                pct20_percentile=pct20_pctl,
                yesterday_pct_change=(yday.pct_change if yday else None),
            )
            hit2 = stock.code in raw2 and verify_prompt2(
                pct20_percentile=pct20_pctl,
                yesterday_close=(yday.close_price if yday else None),
                ma5=ma5, ma20=ma20,
            )
            if hit1 and hit2:
                verified_by_code[stock.code] = "both"
            elif hit1:
                verified_by_code[stock.code] = "prompt1"
            elif hit2:
                verified_by_code[stock.code] = "prompt2"

    stats = {
        "prompt1_raw": len(raw1), "prompt2_raw": len(raw2),
        "verified": len(verified_by_code), "new": 0, "renewed": 0, "expired": 0,
    }
    # 召回异常检测放在这个早退分支之前——0候选/召回骤降正是Prompt Parser Monitor
    # 最需要抓到的情况，不能被"没有可处理的候选"这条早退路径悄悄跳过。
    _log_discovery_run(db, as_of, prompts, raw1_count=len(raw1), raw2_count=len(raw2), verified_count=stats["verified"])
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
