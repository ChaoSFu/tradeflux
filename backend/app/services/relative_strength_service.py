"""
相对强度 RS —— 个股区间收益 减去 同制度基准指数的区间收益。

    RS(N) = 个股近N个交易日收益% − 基准指数近N个交易日收益%

正数 = 跑赢基准。单位是百分点，不是比值。

## 为什么基准要按板块配对

拿上证指数给创业板股票当基准是错的：两者涨跌幅限制（10% vs 20%）和波动量级都不同，
算出来的 RS 会系统性偏高。配对直接复用 `deviation_service.board_index_code()` ——
那是全仓已有的唯一一份「个股代码 → 基准指数」映射，**不再写第二套**
（本仓库已经因为同一事实存在两套函数栽过六次）。

北交所返回 None，`board_index_code` 原样不动：它是严重异动预测共用的，改它会波及
监管模块；而强势池本来就排除北交所（`_should_include_stock`），这里没有实际缺口。

## 为什么不能用现成的 pct_change_60d

`screening_service` 里那三个字段是 **`sum(b.pct_change)`——日涨幅简单相加**，不是
复合收益。docs 里记着实测案例：603580 近 60 日真实 +204.85%，相加只得 123.14%，
**差 80 个百分点**。而误差随涨幅放大，正好专坑大涨股——高标龙头全是这一类。
所以这里一律从收盘价序列算复合收益：`close[t] / close[t-N] - 1`。

## 日期必须对齐，对不齐就返回 None

个股快照有缺口（生产实测覆盖率 94.3%），"往前数 N 根 bar" 不等于 "往前 N 个交易日"。
如果拿一段跨了 70 个自然日的个股收益，去减一段正好 60 个交易日的指数收益，得到的
数没有意义，而且**不会报错**。

所以锚点日期一律以**指数的交易日历**为准（指数每天都有，是最可靠的交易日序列），
再去个股序列里取那一天的收盘价；取不到就返回 None —— 不用最近的一天近似顶替。
这跟破局雷达"窗口不满不给上沿"、连板数"缺失不当成1板"是同一条纪律。
"""
from datetime import date
from typing import Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..models.market_index import IndexDailySnapshot
from ..services.deviation_service import board_index_code

DEFAULT_WINDOWS: tuple[int, ...] = (10, 20, 60)


def _interval_return(closes: Dict[date, float], anchor: date, latest: date) -> Optional[float]:
    """区间复合收益 %。锚点或最新收盘缺失 → None（不知道，不近似）。"""
    a, b = closes.get(anchor), closes.get(latest)
    if not a or not b or a <= 0:
        return None
    return (b / a - 1) * 100


class MarketBenchmark:
    """
    一次性载入各基准指数的收盘序列与锚点日期，供整批股票复用。

    锚点由**指数**的交易日序列决定：`dates[-1-N]` 才是"往前 N 个交易日"。
    指数日线不会缺，用它当交易日历比用个股快照可靠得多。
    """

    def __init__(self, db: Session, as_of: Optional[date] = None,
                 windows: Sequence[int] = DEFAULT_WINDOWS):
        self.windows = tuple(windows)
        self._closes: Dict[str, Dict[date, float]] = {}
        self._anchor: Dict[str, Dict[int, date]] = {}
        self._latest: Dict[str, date] = {}
        self.returns: Dict[str, Dict[int, Optional[float]]] = {}

        q = db.query(IndexDailySnapshot.index_code, IndexDailySnapshot.date,
                     IndexDailySnapshot.close)
        if as_of:
            q = q.filter(IndexDailySnapshot.date <= as_of)
        rows: Dict[str, List[tuple]] = {}
        for code, d, close in q.all():
            if close and close > 0:
                rows.setdefault(code, []).append((d, close))

        for code, series in rows.items():
            series.sort(key=lambda r: r[0])
            dates = [d for d, _c in series]
            self._closes[code] = {d: c for d, c in series}
            self._latest[code] = dates[-1]
            self._anchor[code] = {}
            self.returns[code] = {}
            for w in self.windows:
                if len(dates) < w + 1:
                    # 指数历史都不够长，这个窗口谁也算不了。None 而不是跳过——
                    # 调用方要能看出"这个窗口没有基准"，而不是以为算过了
                    self.returns[code][w] = None
                    continue
                anchor = dates[-1 - w]
                self._anchor[code][w] = anchor
                self.returns[code][w] = _interval_return(
                    self._closes[code], anchor, dates[-1])

    def anchor(self, index_code: str, window: int) -> Optional[date]:
        return self._anchor.get(index_code, {}).get(window)

    def latest(self, index_code: str) -> Optional[date]:
        return self._latest.get(index_code)


def compute_rs_market(
    bench: MarketBenchmark, code: str, closes: Dict[date, float],
) -> Dict[int, Optional[float]]:
    """
    单只股票的 RS。`closes` 是该股 {日期: 收盘价}。

    返回 {窗口: RS百分点 或 None}。None 有三种成因，**都表示"不知道"**：
      · 该股没有对应基准（北交所）
      · 指数历史不够长
      · 个股在锚点日或最新日没有收盘价（停牌 / 快照缺口）
    绝不用邻近日期顶替——那会把两段不同区间的收益相减，而且不会报错。
    """
    idx = board_index_code(code)
    out: Dict[int, Optional[float]] = {w: None for w in bench.windows}
    if not idx or idx not in bench.returns:
        return out
    latest = bench.latest(idx)
    if not latest:
        return out
    for w in bench.windows:
        base = bench.returns[idx].get(w)
        anchor = bench.anchor(idx, w)
        if base is None or anchor is None:
            continue
        mine = _interval_return(closes, anchor, latest)
        if mine is None:
            continue
        out[w] = round(mine - base, 2)
    return out


# ─── 板块相对强度 ─────────────────────────────────────────────────────────────

def compute_rs_sector(
    sector_closes: Dict[date, float], stock_closes: Dict[date, float],
    windows: Sequence[int] = DEFAULT_WINDOWS,
) -> Dict[int, Optional[float]]:
    """
    个股相对**其主板块**的强度。跟 RS_market 同一套口径与纪律：

      · 复合收益，不是日涨幅相加
      · 锚点日期两边必须是同一天，对不齐就 None
      · 缺数据一律 None，不用邻近日期或别的板块顶替

    跟 RS_market 的一处关键差别：**锚点以板块指数的交易日为准**，而不是大盘指数。
    板块指数是从 push2his 回填来的，可能有缺口（限流打断过），拿大盘的交易日历去
    索引板块序列会取不到值——那样得到的 None 表达的是"我们索引错了"，不是
    "板块那天没数据"，两者混在一起就分不清故障和事实了。
    """
    out: Dict[int, Optional[float]] = {w: None for w in windows}
    if not sector_closes or not stock_closes:
        return out
    dates = sorted(sector_closes)
    latest = dates[-1]
    for w in windows:
        if len(dates) < w + 1:
            continue          # 板块历史不够长，这个窗口没有基准
        anchor = dates[-1 - w]
        base = _interval_return(sector_closes, anchor, latest)
        mine = _interval_return(stock_closes, anchor, latest)
        if base is None or mine is None:
            continue
        out[w] = round(mine - base, 2)
    return out
