"""
换手率推算 —— 从流通市值反推流通股本，再用成交量算换手率。**零新增请求。**

    流通股本 = 流通市值 / 收盘价          （来自涨停池/炸板池的 ltsz 字段）
    换手率%  = 成交量(股) / 流通股本 × 100

## 为什么之前一直没有

腾讯/新浪的 K 线接口都不提供换手率，fuyao dump 的文档也明写"没有换手率"，
所以全市场的 turnover_rate 长期是 NULL。这不是 bug，是数据源的事实。

## 为什么这一版**不接进情绪分和龙头分**

`kline_bar_from_quote` 有段 2026-08-25 的刻意决定：那条路的行情其实带真实换手率，
但代码一律置 None，理由是——

  「如果只有『K线拉取失败、走了行情兜底』的那少数几只带上真实换手率，它们就会
    凭空多拿到情绪分里的 turnover*0.8（高换手股可达+16）和龙头分里的
    turnover_bonus(+5)，等于『数据源恰好走了哪条路』变成了打分优势。」

本模块的流通股本来自 `LimitUpDailyDetail`，**只有进过涨停池/炸板池的股票才有**。
直接接进打分，就变成「**曾经涨停过**」成为打分优势——跟当时避开的是同一个坑，
只是换了个入口。而情绪分/龙头分是拿来做横向排序和板块均值的，来源相关的系统性
偏差比"少一个因子"有害得多。

所以这一版只**算和存**，不改任何打分公式。等覆盖率接近全市场了再单独讨论接入。
生命周期引擎可以用它——那里只看强势池（按 PeakBoard60D>=4 定义，成员几乎都反复
涨停过），覆盖率高，而且它不参与跨股票排序。

## 流通股本会跳变

除权、解禁都会改变流通股本，而且是台阶式的。所以 `float_shares_date` 必须一起存，
用旧观测值算出来的换手率要能看出它有多旧。每次该股再次出现在涨停池，就刷新一次。
"""
from datetime import date
from typing import Dict, Optional

from sqlalchemy.orm import Session

from ..models.limit_up_detail import BrokenBoardDailyDetail, LimitUpDailyDetail
from ..models.stock import Stock

# 观测超过这么多个自然日就不再用来算换手率。解禁/除权是台阶式跳变，
# 用三个月前的流通股本算今天的换手率可能差 20% 以上，而这种错不会报警。
MAX_FLOAT_SHARES_AGE_DAYS = 45


def refresh_float_shares(db: Session, trade_date: date,
                         market_caps: Optional[Dict[str, tuple]] = None) -> dict:
    """
    刷新 Stock.float_shares（流通股本 = 流通市值 ÷ 价格）。**零新增请求**。

    只在算得出且比库里更新时才写——旧观测不覆盖新观测。

    ## 两个来源，覆盖面差一个数量级

    涨停池 + 炸板池明细的 ltsz 字段（两张都读，2026-09-04 补上炸板池——同一个
    事实的两个来源，只读一张纯属白白少一半覆盖）。但它们 08-25 才建表、历史浅，
    且只含当天涨停/炸板的股票，**实测强势池 61 只只覆盖到 38 只**。

    `market_caps` 是 {code: (流通市值, 最新价)}，来自 `fetch_main_board_stocks`
    的全市场 clist——那个调用每天本来就要扫 25 页，多要两个字段是免费的，
    换来的是全市场覆盖。调用方不传就退回只用明细表，行为跟以前一样。

    两个来源冲突时**以明细表为准**并计数：明细的 ltsz 是收盘后归档的确定值，
    clist 的 f21 可能是盘中快照。分歧率异常高就说明字段口径理解错了，要能看见。
    """
    rows = list(
        db.query(LimitUpDailyDetail.stock_id, LimitUpDailyDetail.float_market_cap,
                 LimitUpDailyDetail.price)
        .filter(LimitUpDailyDetail.trade_date == trade_date,
                LimitUpDailyDetail.float_market_cap.isnot(None),
                LimitUpDailyDetail.price.isnot(None))
        .all()
    ) + list(
        db.query(BrokenBoardDailyDetail.stock_id, BrokenBoardDailyDetail.float_market_cap,
                 BrokenBoardDailyDetail.price)
        .filter(BrokenBoardDailyDetail.trade_date == trade_date,
                BrokenBoardDailyDetail.float_market_cap.isnot(None),
                BrokenBoardDailyDetail.price.isnot(None))
        .all()
    )
    by_id = {s.id: s for s in db.query(Stock).filter(
        Stock.id.in_([r[0] for r in rows])).all()} if rows else {}

    def _shares(fmc, price) -> Optional[float]:
        if not fmc or not price or price <= 0:
            return None
        sh = fmc / price
        return sh if sh > 0 else None

    # {code: 流通股本}，明细表优先
    detail: Dict[str, float] = {}
    for sid, fmc, price in rows:
        st = by_id.get(sid)
        sh = _shares(fmc, price)
        if st is not None and sh is not None:
            detail[st.code] = sh

    from_clist: Dict[str, float] = {}
    disagree = 0
    for code, (fmc, price) in (market_caps or {}).items():
        sh = _shares(fmc, price)
        if sh is None:
            continue
        if code in detail:
            # 两个来源都有 → 对一下。差 5% 以上记一笔：分歧率异常高就说明
            # 字段口径理解错了（比如 f21 根本不是流通市值），必须能看见
            if abs(sh - detail[code]) / detail[code] > 0.05:
                disagree += 1
            continue
        from_clist[code] = sh

    merged = {**from_clist, **detail}          # 明细表覆盖 clist
    if not merged:
        return {"updated": 0, "seen": 0, "from_detail": 0, "from_clist": 0,
                "disagree": 0}

    updated = 0
    stocks = db.query(Stock).filter(Stock.code.in_(list(merged))).all()
    for st in stocks:
        sh = merged.get(st.code)
        if sh is None:
            continue
        # 旧观测不覆盖新的
        if st.float_shares_date and st.float_shares_date > trade_date:
            continue
        st.float_shares = round(sh, 2)
        st.float_shares_date = trade_date
        updated += 1
    db.commit()
    return {"updated": updated, "seen": len(merged), "from_detail": len(detail),
            "from_clist": len(from_clist), "disagree": disagree}


def compute_turnover_rate(
    volume: Optional[float], float_shares: Optional[float],
    float_shares_date: Optional[date], as_of: date,
) -> Optional[float]:
    """
    换手率 %。任一环节缺失或观测过旧 → None（不知道），**绝不返回 0**。

    0 和 None 在本仓库是两件被反复混淆的事：turnover_rate 那一版用 0.0 顶替
    "数据源没给"，结果全市场换手率长期恒为 0，情绪分里的因子事实上死掉很久，
    却因为"0是个合法数字"完全没有报错、没人发现。
    """
    if volume is None or volume <= 0:
        return None
    if not float_shares or float_shares <= 0 or float_shares_date is None:
        return None
    age = (as_of - float_shares_date).days
    if age < 0 or age > MAX_FLOAT_SHARES_AGE_DAYS:
        return None          # 观测太旧，算出来的数不可信 —— 宁可没有
    return round(volume / float_shares * 100, 4)
