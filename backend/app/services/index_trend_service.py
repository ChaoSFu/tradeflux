"""
大盘指数趋势分析引擎

方法论（经典趋势跟踪技术分析，均为业界通用口径）：
  1. 均线多空排列（Dow 趋势理论）：
       多头排列 close > MA5 > MA10 > MA20 > MA60 → 各周期资金成本递增，趋势向上
       空头排列反之；均线纠缠 → 震荡市
  2. 关键均线位置：
       MA20（月线）≈ 中期趋势线；MA60（季线）≈ 中长期牛熊分界（生命线）
       收盘价站上/跌破这两条线是中期趋势转换的主信号
  3. 均线斜率：MA20 五日斜率、MA60 十日斜率 > 0 表示趋势仍在向上推进；
       价格在均线上方但均线走平/向下 → 反弹而非反转
  4. 金叉/死叉：MA5 上穿/下穿 MA20（短期动能对中期趋势的确认/背离）
  5. 乖离率 BIAS20 = (close - MA20)/MA20：指数口径 |BIAS| > 5% 视为
       短期超买/超跌，趋势内回踩/反抽概率加大（均值回归）

趋势强度分（0-100，构成透明可解释）：
  位置分 40：收盘价高于 MA5/10/20/60 各 +10
  排列分 20：MA5>MA10 +7、MA10>MA20 +7、MA20>MA60 +6
  斜率分 20：MA20 五日斜率>0 +10、MA60 十日斜率>0 +10
  动能分 20：近5日累计涨幅>0 +10、近3日出现金叉 +10
  ≥75 强势 / 55-74 偏强 / 40-54 震荡 / 20-39 偏弱 / <20 弱势
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import List, Optional

from .eastmoney_fetcher import fetch_index_kline
from ..schemas.market_index import (
    IndexTrendPoint, IndexSignal, IndexTrendAnalysis, MarketTrendResponse,
)

# 核心指数（secid：东财市场前缀.代码）
INDICES = [
    {"code": "000001", "secid": "1.000001", "name": "上证指数"},
    {"code": "399001", "secid": "0.399001", "name": "深证成指"},
    {"code": "399006", "secid": "0.399006", "name": "创业板指"},
    {"code": "000688", "secid": "1.000688", "name": "科创50"},
    {"code": "899050", "secid": "0.899050", "name": "北证50"},
]

BIAS_THRESHOLD = 5.0     # 指数乖离率超买/超跌阈值 %
SIGNAL_LOOKBACK = 10     # 近 N 个交易日内的信号才展示
CHART_DAYS = 120         # 前端图表返回的交易日数

_CACHE_TTL = 600         # 秒；东财公开接口，避免每次页面访问都全量拉取
_cache: dict = {"ts": None, "resp": None}
_lock = threading.Lock()


def _ma(closes: List[float], i: int, n: int) -> Optional[float]:
    """closes[i] 往前 n 日均值；数据不足返回 None"""
    if i + 1 < n:
        return None
    return sum(closes[i - n + 1: i + 1]) / n


def _analyze_index(meta: dict, bars: List[dict]) -> IndexTrendAnalysis:
    """bars: fetch_index_kline 返回的 [{'date','close','pct_change'}, ...]（升序）"""
    closes = [b["close"] for b in bars]
    n = len(bars)
    i = n - 1  # 最新一根

    ma = {p: [_ma(closes, k, p) for k in range(n)] for p in (5, 10, 20, 60, 120, 250)}

    close = closes[i]
    ma5, ma10, ma20, ma60 = ma[5][i], ma[10][i], ma[20][i], ma[60][i]

    # ── 位置与排列 ────────────────────────────────────────────────────────
    above = {
        "ma5":  bool(ma5 and close > ma5),
        "ma10": bool(ma10 and close > ma10),
        "ma20": bool(ma20 and close > ma20),
        "ma60": bool(ma60 and close > ma60),
    }
    ordered_bull = [
        bool(ma5 and ma10 and ma5 > ma10),
        bool(ma10 and ma20 and ma10 > ma20),
        bool(ma20 and ma60 and ma20 > ma60),
    ]
    ordered_bear = [
        bool(ma5 and ma10 and ma5 < ma10),
        bool(ma10 and ma20 and ma10 < ma20),
        bool(ma20 and ma60 and ma20 < ma60),
    ]
    if all(ordered_bull) and all(above.values()):
        alignment = "bull"
    elif all(ordered_bear) and not any(above.values()):
        alignment = "bear"
    else:
        alignment = "mixed"

    # ── 斜率（趋势方向确认）───────────────────────────────────────────────
    def slope(period: int, back: int) -> float:
        cur, prev = ma[period][i], ma[period][i - back] if i - back >= 0 else None
        if not cur or not prev:
            return 0.0
        return round((cur / prev - 1) * 100, 2)

    ma20_slope = slope(20, 5)
    ma60_slope = slope(60, 10)

    # ── 乖离率与区间涨幅 ─────────────────────────────────────────────────
    bias20 = round((close - ma20) / ma20 * 100, 2) if ma20 else 0.0
    pct_5d = round((close / closes[i - 5] - 1) * 100, 2) if i >= 5 else 0.0
    pct_20d = round((close / closes[i - 20] - 1) * 100, 2) if i >= 20 else 0.0

    # ── 信号扫描（近 SIGNAL_LOOKBACK 日）─────────────────────────────────
    signals: List[IndexSignal] = []
    start = max(1, n - SIGNAL_LOOKBACK)
    golden_recent_3d = False
    for k in range(start, n):
        d = str(bars[k]['date'])
        m5p, m5c = ma[5][k - 1], ma[5][k]
        m20p, m20c = ma[20][k - 1], ma[20][k]
        m60p, m60c = ma[60][k - 1], ma[60][k]
        cp, cc = closes[k - 1], closes[k]
        if m5p and m20p and m5c and m20c:
            if m5p <= m20p and m5c > m20c:
                signals.append(IndexSignal(date=d, kind="golden_cross", label="MA5 金叉 MA20，短期动能转强", side="bull"))
                if k >= n - 3:
                    golden_recent_3d = True
            elif m5p >= m20p and m5c < m20c:
                signals.append(IndexSignal(date=d, kind="death_cross", label="MA5 死叉 MA20，短期动能转弱", side="bear"))
        if m20p and m20c:
            if cp <= m20p and cc > m20c:
                signals.append(IndexSignal(date=d, kind="break_above_ma20", label="站上 MA20 月线，中期转强信号", side="bull"))
            elif cp >= m20p and cc < m20c:
                signals.append(IndexSignal(date=d, kind="break_below_ma20", label="跌破 MA20 月线，中期转弱信号", side="bear"))
        if m60p and m60c:
            if cp <= m60p and cc > m60c:
                signals.append(IndexSignal(date=d, kind="break_above_ma60", label="站上 MA60 季线（牛熊线），中长期转强", side="bull"))
            elif cp >= m60p and cc < m60c:
                signals.append(IndexSignal(date=d, kind="break_below_ma60", label="跌破 MA60 季线（牛熊线），中长期转弱", side="bear"))
    if bias20 >= BIAS_THRESHOLD:
        signals.append(IndexSignal(date=str(bars[i]['date']), kind="overbought",
                                   label=f"乖离率 +{bias20:.1f}%，短期超买，谨防回踩均线", side="warn"))
    elif bias20 <= -BIAS_THRESHOLD:
        signals.append(IndexSignal(date=str(bars[i]['date']), kind="oversold",
                                   label=f"乖离率 {bias20:.1f}%，短期超跌，存在均值回归反抽需求", side="warn"))
    signals.sort(key=lambda s: s.date, reverse=True)

    # ── 趋势强度分 ────────────────────────────────────────────────────────
    score = 0
    score += sum(10 for v in above.values() if v)                       # 位置 40
    score += (7 if ordered_bull[0] else 0) + (7 if ordered_bull[1] else 0) + (6 if ordered_bull[2] else 0)  # 排列 20
    score += (10 if ma20_slope > 0 else 0) + (10 if ma60_slope > 0 else 0)  # 斜率 20
    score += (10 if pct_5d > 0 else 0) + (10 if golden_recent_3d else 0)    # 动能 20

    if score >= 75:
        state, state_label = "strong", "强势"
    elif score >= 55:
        state, state_label = "bullish", "偏强"
    elif score >= 40:
        state, state_label = "range", "震荡"
    elif score >= 20:
        state, state_label = "bearish", "偏弱"
    else:
        state, state_label = "weak", "弱势"

    # ── 图表序列（近 CHART_DAYS 日）──────────────────────────────────────
    r2 = lambda v: round(v, 2) if v is not None else None  # noqa: E731
    series = [
        IndexTrendPoint(
            date=str(bars[k]['date']), close=r2(closes[k]),
            ma5=r2(ma[5][k]), ma10=r2(ma[10][k]), ma20=r2(ma[20][k]),
            ma60=r2(ma[60][k]), ma120=r2(ma[120][k]), ma250=r2(ma[250][k]),
        )
        for k in range(max(0, n - CHART_DAYS), n)
    ]

    return IndexTrendAnalysis(
        code=meta["code"], name=meta["name"],
        close=round(close, 2), pct_change=round(bars[i]['pct_change'], 2),
        pct_5d=pct_5d, pct_20d=pct_20d,
        score=score, state=state, state_label=state_label, alignment=alignment,
        above_ma5=above["ma5"], above_ma10=above["ma10"],
        above_ma20=above["ma20"], above_ma60=above["ma60"],
        ma20_slope_pct=ma20_slope, ma60_slope_pct=ma60_slope, bias20=bias20,
        signals=signals[:8], series=series,
    )


def get_market_trend(force_refresh: bool = False) -> MarketTrendResponse:
    """全部核心指数的趋势分析（进程内缓存 10 分钟）"""
    with _lock:
        if (
            not force_refresh
            and _cache["resp"] is not None
            and (datetime.now() - _cache["ts"]).total_seconds() < _CACHE_TTL
        ):
            return _cache["resp"]

        indices: List[IndexTrendAnalysis] = []
        errors: List[str] = []
        for meta in INDICES:
            try:
                # 320 根：MA250 年线留冗余；返回 dict {'date','close','pct_change'}
                bars = fetch_index_kline(meta["secid"], days=320)
                if len(bars) < 61:
                    raise ValueError(f"K线不足61根（{len(bars)}）")
                indices.append(_analyze_index(meta, bars))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{meta['name']}: {e}")

        resp = MarketTrendResponse(
            updated_at=datetime.now().isoformat(timespec="seconds"),
            indices=indices,
            errors=errors,
        )
        # 全部失败时不缓存，便于下次重试
        if indices:
            _cache["ts"] = datetime.now()
            _cache["resp"] = resp
        return resp
