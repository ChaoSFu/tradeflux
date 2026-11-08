"""
大市值日快照同步：东财 clist 翻完全市场 → 核对完整性 → 整批写入。拿不全时退到腾讯行情。

## 字段口径（2026-11-08 对 10 只股票逐个核过：茅台、宁德、工行、五粮液、中芯、比亚迪、
## 招行、东财、平安、美的，全部与东财前复权日 K / 个股详情一致）

    f20  总市值（元）= 总股本 × 最新价。A+H 公司的总股本含 H 股（工行 30080 亿）
    f21  流通市值（元）
    f2   最新价         f3  当日涨跌幅 %
    f6   成交额（元）    f8  换手率 %（= 日 K 的 f61）
    f10  量比（收盘后 = 当日量 ÷ 前 5 日均量）
    f109 / f160 / f110 / f24   近 5 / 10 / 20 / 60 个交易日涨跌幅 %（前复权收盘价算的）
    f100 东财二级行业名，跟 sectors 表里的行业板块同名（≥400 亿的 435 只全部对得上）
    f26  上市日（yyyymmdd）
    f103 概念（逗号分隔的板块名，跟 sectors 表的概念板块同名）。只有题材概念，不含 F10 里
         混着的权重股 / HS300_ / 融资融券这类风格、指数板块
    f297 这条行情的交易日（yyyymmdd）

**上市不满 N 天的「N 日涨跌幅」不是 N 日涨跌幅**：粤芯半导 10-13 上市、到 11-06 只有
19 根日 K，东财照样给 20 日 = 60 日 = 年初至今 = 128.14%（上市以来）。写库前按交易日历
数一下上市以来的交易日，不够 N+1 根就把 N 日涨跌幅置空（见 mask_young_returns）。

**注意 f8 / f10 别弄反**：f8 是换手率、f10 是量比。eastmoney_fetcher._fetch_from_eastmoney
曾把 f10 当换手率用，2026-11-08 已改成 f8。

## 什么叫「完整」

不是「拿到 800 只以上」。四个市场分组（沪主板、科创、深主板、创业板）每组都要：
翻完全部页、去重后的条数等于源头报的 total。外加有效市值占比 ≥ 90%、总数不比上一次
完整批次少 5% 以上。任何一条不满足，这批**一行都不写**——残缺名单会把没返回的公司
算成「跌出门槛」。

**东财单页最多 100 条**（2026-11-08 实测 pz=200/500 都只回 100），全市场 5563 只、58 页。

## 盘中数据不写

收盘前抓到的是现价，写进来会冒充当天的正式市值。V1 只收已收盘的数据；盘中抓到的批次
只记一行批次记录（status=intraday）。
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx
from sqlalchemy.orm import Session

from ..models.large_cap import LargeCapMarketDaily, LargeCapStockBoards, LargeCapSyncBatch
from .eastmoney_fetcher import (
    EM_MARKET_CONFIGS, HEADERS, SH_TZ, _F10_HEADERS, _warmed_client, bar_is_settled,
    json_or_explain, probe_market_now,
)

SOURCE_EM = "eastmoney_clist"
SOURCE_TENCENT = "tencent_quote"

EM_HOSTS = ("push2delay.eastmoney.com", "push2.eastmoney.com")
EM_FIELDS = "f12,f13,f14,f2,f3,f6,f8,f10,f20,f21,f24,f26,f100,f103,f109,f110,f160,f297"
PAGE_SIZE = 100
PAGE_TRIES = 4
_BACKOFF = (1.0, 3.0, 6.0, 10.0)

# 概念只给总市值 ≥ 这个数的存：全市场每只都存一串概念名，一年要多上百 MB，而门槛最低
# 预设也是 500 亿。门槛调到这以下时，下面那截公司没有概念（页面会报数）
CONCEPT_FLOOR_YI = 300.0

MIN_VALID_CAP_RATIO = 0.90
MIN_EXPECTED_RATIO = 0.95

MARKET_LABEL = {"m:1+t:2": "沪主板", "m:1+t:23": "科创板", "m:0+t:6": "深主板", "m:0+t:80": "创业板"}

TENCENT_URL = "https://qt.gtimg.cn/q="
TENCENT_BATCH = 60
_TENCENT_HEADERS = {"Referer": "https://finance.qq.com/", "User-Agent": HEADERS.get("User-Agent", "Mozilla/5.0")}


# ── 解析 ────────────────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    """可正可负可为 0 的数（涨跌幅、量比）。"-"、空、非数字 → None。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _pos(v) -> Optional[float]:
    """必须为正的数（市值、价格）。≤0 也当没给——0 亿市值不是一个市值。"""
    f = _num(v)
    return f if f is not None and f > 0 else None


def _yi(yuan) -> Optional[float]:
    """元 → 亿元。"""
    f = _num(yuan)
    return None if f is None else f / 1e8


def _is_st(name: Optional[str]) -> bool:
    return bool(name) and "ST" in name.upper()


def _parse_yyyymmdd(v) -> Optional[date]:
    s = str(v or "").strip()
    if len(s) < 8 or not s[:8].isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def parse_em_item(it: dict) -> Optional[dict]:
    """一条 clist 记录 → 一行快照（不含 trade_date 以外的批次信息）。"""
    code = str(it.get("f12") or "").strip()
    if not code:
        return None
    m = it.get("f13")
    name = it.get("f14")
    industry = it.get("f100")
    if not isinstance(industry, str) or industry.strip() in ("", "-"):
        industry = None
    raw_concepts = it.get("f103")
    concepts = None
    if isinstance(raw_concepts, str) and raw_concepts.strip() not in ("", "-"):
        concepts = list(dict.fromkeys(x.strip() for x in raw_concepts.split(",") if x.strip()))
    total = _pos(it.get("f20"))
    flt = _pos(it.get("f21"))
    amount = _num(it.get("f6"))
    return {
        "stock_code": code,
        "stock_name": name if isinstance(name, str) else None,
        "market": "SH" if m == 1 else "SZ" if m == 0 else None,
        "total_market_cap": total / 1e8 if total else None,
        "float_market_cap": flt / 1e8 if flt else None,
        "price": _pos(it.get("f2")),
        "pct_change": _num(it.get("f3")),
        "pct_change_5d": _num(it.get("f109")),
        "pct_change_10d": _num(it.get("f160")),
        "pct_change_20d": _num(it.get("f110")),
        "pct_change_60d": _num(it.get("f24")),
        "amount": amount / 1e8 if amount is not None else None,
        "turnover_rate": _num(it.get("f8")),
        "volume_ratio": _num(it.get("f10")),
        "industry": industry,
        "concepts": concepts,
        "is_st": _is_st(name if isinstance(name, str) else None),
        "listing_date": _parse_yyyymmdd(it.get("f26")),
        "trade_date": _parse_yyyymmdd(it.get("f297")),
    }


def parse_tencent_line(line: str) -> Optional[dict]:
    """
    `v_sh600519="1~贵州茅台~600519~1453.00~...";` → 一行快照。

    腾讯的单位：市值（f44 流通 / f45 总）是亿元，成交额 f37 是万元。2026-11-08 跟东财
    逐个对过 5 只，市值、换手、量比完全一致。**不给区间涨幅，也不给行业**。
    """
    if '="' not in line:
        return None
    head, _, body = line.partition('="')
    body = body.rstrip('";\n\r ')
    f = body.split("~")
    if len(f) < 50:
        return None
    prefix = head.strip().removeprefix("v_")[:2]
    code = f[2].strip()
    if not code or prefix not in ("sh", "sz"):
        return None
    amount_wan = _num(f[37])
    ts = f[30].strip()
    return {
        "stock_code": code,
        "stock_name": f[1].strip() or None,
        "market": prefix.upper(),
        "total_market_cap": _pos(f[45]),
        "float_market_cap": _pos(f[44]),
        "price": _pos(f[3]),
        "pct_change": _num(f[32]),
        "pct_change_5d": None, "pct_change_10d": None,
        "pct_change_20d": None, "pct_change_60d": None,
        "amount": amount_wan / 1e4 if amount_wan is not None else None,
        "turnover_rate": _num(f[38]),
        "volume_ratio": _num(f[49]),
        "industry": None,
        "concepts": None,
        "is_st": _is_st(f[1]),
        "listing_date": None,
        "trade_date": _parse_yyyymmdd(ts),
    }


RETURN_WINDOWS = (5, 10, 20, 60)


def mask_young_returns(rows: Iterable[dict], trade_date: date, calendar: Sequence[date]) -> int:
    """
    上市以来不满 N+1 个交易日的，N 日涨跌幅置空（东财这时给的是上市以来涨幅）。返回改了几只。

    日历只覆盖近一年：上市日早于日历起点的肯定够 60 天，不动。没有日历时保守处理：
    上市不到 N×2 个自然日的一律置空——宁可少一个数，也不让「上市以来」顶着「N 日」的名字。
    """
    cal = sorted(calendar or [])
    if cal and cal[-1] < trade_date:
        cal = []        # 缓存没刷到这天：数出来的交易日会偏少，按没有日历处理
    n = 0
    for r in rows:
        ld = r.get("listing_date")
        if ld is None or ld > trade_date:
            continue
        if cal and ld < cal[0]:
            continue
        sessions = sum(1 for d in cal if ld <= d <= trade_date) if cal else None
        touched = False
        for w in RETURN_WINDOWS:
            too_young = (sessions < w + 1) if sessions is not None else ((trade_date - ld).days < w * 2)
            if too_young and r.get(f"pct_change_{w}d") is not None:
                r[f"pct_change_{w}d"] = None
                touched = True
        n += touched
    return n


# ── 抓取 ────────────────────────────────────────────────────────────────────

class PageError(Exception):
    pass


@dataclass
class FetchResult:
    source: str
    rows: Dict[str, dict] = field(default_factory=dict)
    expected: int = 0
    pages_expected: int = 0
    pages_ok: int = 0
    fetch_ok: bool = False          # 每一页都拿到了
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


def em_page_getter(client: httpx.Client, sleep: Callable[[float], None] = time.sleep):
    """返回 get(fs, pn) -> data。每页最多试 PAGE_TRIES 次、两个主机轮着来。"""
    def get(fs: str, pn: int) -> dict:
        last = ""
        for attempt in range(PAGE_TRIES):
            host = EM_HOSTS[attempt % len(EM_HOSTS)]
            try:
                resp = client.get(f"https://{host}/api/qt/clist/get", params={
                    "pn": pn, "pz": PAGE_SIZE, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                    "fid": "f20", "fs": fs, "fields": EM_FIELDS,
                })
                data = json_or_explain(resp, "东财 clist ").get("data") or {}
                if data.get("diff"):
                    return data
                last = f"{host} 空页"
            except Exception as e:  # noqa: BLE001
                last = f"{host} {type(e).__name__}: {str(e)[:80]}"
            sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])
        raise PageError(f"{MARKET_LABEL.get(fs, fs)} 第 {pn} 页 {PAGE_TRIES} 次都没拿到（{last}）")
    return get


def collect_eastmoney(get_page: Callable[[str, int], dict],
                      markets: Sequence[Tuple[str, int]] = EM_MARKET_CONFIGS,
                      pause: Callable[[], None] = lambda: time.sleep(0.3)) -> FetchResult:
    """
    按市场分组逐页翻。**任何一页拿不到就整批作废**：后面的页没翻，名单就缺一截，
    缺掉的公司会被当成跌出门槛。
    """
    res = FetchResult(source=SOURCE_EM)
    for fs, _mid in markets:
        label = MARKET_LABEL.get(fs, fs)
        try:
            first = get_page(fs, 1)
        except PageError as e:
            res.error = str(e)
            return res
        total = int(first.get("total") or 0)
        n_pages = math.ceil(total / PAGE_SIZE) if total else 0
        res.pages_expected += n_pages
        res.expected += total
        seen: Dict[str, dict] = {}
        for pn in range(1, n_pages + 1):
            if pn == 1:
                data = first
            else:
                pause()
                try:
                    data = get_page(fs, pn)
                except PageError as e:
                    res.error = str(e)
                    return res
            res.pages_ok += 1
            for it in data.get("diff") or []:
                rec = parse_em_item(it)
                if rec is not None:
                    seen[rec["stock_code"]] = rec
        if len(seen) != total:
            res.warnings.append(f"{label}：源头报 {total} 只，去重后 {len(seen)} 只")
        res.rows.update(seen)
    res.fetch_ok = True
    return res


def fetch_eastmoney() -> FetchResult:
    with _warmed_client(headers=HEADERS, tag="large_cap") as client:
        return collect_eastmoney(em_page_getter(client))


def fetch_tencent(universe: Sequence[Tuple[str, str]], *,
                  get_text: Optional[Callable[[str], str]] = None,
                  pause: Callable[[], None] = lambda: time.sleep(0.2)) -> FetchResult:
    """universe = [(code, 'SH'|'SZ'), ...]。一批 60 只，一批失败重试两次，还不行整批作废。"""
    res = FetchResult(source=SOURCE_TENCENT, expected=len(universe))
    batches = [universe[i:i + TENCENT_BATCH] for i in range(0, len(universe), TENCENT_BATCH)]
    res.pages_expected = len(batches)

    def _default_get(q: str) -> str:
        r = httpx.get(TENCENT_URL + q, headers=_TENCENT_HEADERS, timeout=15)
        r.raise_for_status()
        return r.content.decode("gbk", errors="replace")

    get = get_text or _default_get
    for bi, batch in enumerate(batches):
        q = ",".join(f"{m.lower()}{c}" for c, m in batch)
        text, last = None, ""
        for attempt in range(3):
            try:
                text = get(q)
                break
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {str(e)[:80]}"
                time.sleep(_BACKOFF[attempt])
        if text is None:
            res.error = f"腾讯行情第 {bi + 1}/{len(batches)} 批失败（{last}）"
            return res
        res.pages_ok += 1
        for line in text.split(";"):
            rec = parse_tencent_line(line.strip())
            if rec is not None:
                res.rows[rec["stock_code"]] = rec
        if bi + 1 < len(batches):
            pause()
    if len(res.rows) != res.expected:
        res.warnings.append(f"腾讯行情：名单 {res.expected} 只，返回 {len(res.rows)} 只")
    res.fetch_ok = True
    return res


# ── 判完整 ──────────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    complete: bool
    trade_date: Optional[date]
    valid_caps: int
    reasons: List[str]


def judge(res: FetchResult, prev_expected: Optional[int] = None) -> Verdict:
    """这批能不能当权威名单。理由全写出来，不只给一个布尔。"""
    reasons: List[str] = []
    rows = list(res.rows.values())
    valid = sum(1 for r in rows if r["total_market_cap"] is not None)
    dates = Counter(r["trade_date"] for r in rows if r["trade_date"] is not None)
    trade_date = dates.most_common(1)[0][0] if dates else None

    if not res.fetch_ok:
        reasons.append(res.error or "抓取中断")
    if res.fetch_ok and res.pages_ok != res.pages_expected:
        reasons.append(f"页数不全：{res.pages_ok}/{res.pages_expected}")
    if len(rows) != res.expected:
        reasons.append(f"条数不符：源头 {res.expected}，去重后 {len(rows)}")
    if not rows:
        reasons.append("没有拿到任何行")
    elif valid / len(rows) < MIN_VALID_CAP_RATIO:
        reasons.append(f"有效总市值只有 {valid}/{len(rows)}，低于 {MIN_VALID_CAP_RATIO:.0%}")
    if prev_expected and res.expected < prev_expected * MIN_EXPECTED_RATIO:
        reasons.append(f"总数 {res.expected} 比上次完整批次 {prev_expected} 少了 5% 以上")
    if trade_date is None and rows:
        reasons.append("行情没带交易日")
    return Verdict(complete=not reasons, trade_date=trade_date, valid_caps=valid, reasons=reasons)


# ── 写库 ────────────────────────────────────────────────────────────────────

def _latest_applied_batch(db: Session, trade_date: date) -> Optional[LargeCapSyncBatch]:
    return (db.query(LargeCapSyncBatch)
            .filter(LargeCapSyncBatch.trade_date == trade_date, LargeCapSyncBatch.applied.is_(True))
            .order_by(LargeCapSyncBatch.id.desc()).first())


def _write_rows(db: Session, batch: LargeCapSyncBatch, rows: Iterable[dict], observed_at: datetime) -> int:
    """同一天整批替换：先删后插，同一个事务。重复同步是覆盖，不会叠出两份。"""
    db.query(LargeCapMarketDaily).filter(LargeCapMarketDaily.trade_date == batch.trade_date) \
        .delete(synchronize_session=False)
    payload = []
    for r in rows:
        rec = {k: v for k, v in r.items() if k != "trade_date"}
        cps = rec.get("concepts")
        keep = cps is not None and (rec.get("total_market_cap") or 0) >= CONCEPT_FLOOR_YI
        rec["concepts"] = json.dumps(cps, ensure_ascii=False) if keep else None
        rec.update(trade_date=batch.trade_date, source=batch.source,
                   batch_id=batch.id, observed_at=observed_at)
        payload.append(rec)
    db.bulk_insert_mappings(LargeCapMarketDaily, payload)
    return len(payload)


def _market_now(log) -> Tuple[datetime, Optional[str]]:
    now = probe_market_now()
    if now is not None:
        return now.replace(tzinfo=None), None
    local = datetime.now(SH_TZ).replace(tzinfo=None)
    msg = f"市场时间探测失败，按本机时钟 {local:%H:%M} 判断是否收盘"
    log(f"  ⚠️ {msg}")
    return local, msg


def _record(db: Session, res: FetchResult, verdict: Verdict, started: datetime,
            settled: Optional[bool], status: str, extra_warnings: List[str]) -> LargeCapSyncBatch:
    b = LargeCapSyncBatch(
        trade_date=verdict.trade_date, source=res.source, started_at=started,
        expected_count=res.expected, observed_count=len(res.rows),
        valid_market_cap_count=verdict.valid_caps,
        pages_expected=res.pages_expected, pages_ok=res.pages_ok,
        is_complete=verdict.complete, is_settled=settled, applied=False, status=status,
        warnings=json.dumps([*res.warnings, *extra_warnings], ensure_ascii=False) or None,
        error="；".join(verdict.reasons) or None,
    )
    db.add(b)
    db.flush()
    return b


def _prev_complete_expected(db: Session, source: str) -> Optional[int]:
    b = (db.query(LargeCapSyncBatch)
         .filter(LargeCapSyncBatch.source == source, LargeCapSyncBatch.is_complete.is_(True))
         .order_by(LargeCapSyncBatch.id.desc()).first())
    return b.expected_count if b else None


def _tencent_universe(db: Session, max_age_days: int = 20) -> Tuple[List[Tuple[str, str]], Dict[str, tuple], Optional[date]]:
    """沿用最近一次东财完整快照的名单（和那天的行业、概念）。太旧就不沿用。"""
    b = (db.query(LargeCapSyncBatch)
         .filter(LargeCapSyncBatch.source == SOURCE_EM, LargeCapSyncBatch.applied.is_(True))
         .order_by(LargeCapSyncBatch.trade_date.desc()).first())
    if b is None or b.trade_date is None or b.trade_date < date.today() - timedelta(days=max_age_days):
        return [], {}, None
    rows = (db.query(LargeCapMarketDaily.stock_code, LargeCapMarketDaily.market,
                     LargeCapMarketDaily.industry, LargeCapMarketDaily.concepts)
            .filter(LargeCapMarketDaily.trade_date == b.trade_date).all())
    return ([(c, m) for c, m, _, _ in rows if m in ("SH", "SZ")],
            {c: (ind, json.loads(cps) if cps else None) for c, _, ind, cps in rows}, b.trade_date)


def sync_snapshot(db: Session, *, apply: bool = True, source: str = "auto",
                  log: Callable[[str], None] = print,
                  fetch_em: Callable[[], FetchResult] = fetch_eastmoney,
                  fetch_tx: Callable[..., FetchResult] = fetch_tencent,
                  market_now: Optional[datetime] = None,
                  calendar: Optional[Sequence[date]] = None) -> dict:
    """
    跑一次同步。返回汇总 dict（status / trade_date / applied / reasons ...）。

    apply=False 是试跑：照样抓、照样判，不写快照、不写批次记录。
    """
    out: dict = {"status": "failed", "applied": False, "attempts": []}
    now, now_warn = (market_now, None) if market_now is not None else _market_now(log)
    extra = [now_warn] if now_warn else []

    tries: List[str] = []
    if source in ("auto", "eastmoney"):
        tries.append(SOURCE_EM)
    if source in ("auto", "tencent"):
        tries.append(SOURCE_TENCENT)

    for src in tries:
        started = datetime.now(SH_TZ).replace(tzinfo=None)
        warnings = list(extra)
        if src == SOURCE_EM:
            log("[大市值] 东财 clist 翻全市场...")
            res = fetch_em()
            carry_industry: Dict[str, tuple] = {}
        else:
            universe, carry_industry, uni_date = _tencent_universe(db)
            if not universe:
                msg = "腾讯备用：没有 20 天内的东财完整名单可沿用"
                log(f"  ❌ {msg}")
                out["attempts"].append({"source": src, "status": "failed", "reasons": [msg]})
                if apply:
                    v = Verdict(False, None, 0, [msg])
                    _record(db, FetchResult(source=src), v, started, None, "failed", warnings)
                    db.commit()
                continue
            log(f"[大市值] 腾讯行情备用，名单沿用 {uni_date} 东财快照（{len(universe)} 只）...")
            warnings.append(f"全市场名单与行业、概念沿用 {uni_date} 东财快照；之后新上市的不在内；"
                            "腾讯不提供 5/10/20/60 日涨跌幅")
            res = fetch_tx(universe)
            for code, rec in res.rows.items():
                rec["industry"], rec["concepts"] = carry_industry.get(code, (None, None))

        verdict = judge(res, _prev_complete_expected(db, src) if src == SOURCE_EM else None)
        if verdict.trade_date is not None:
            if calendar is None:
                calendar = _cached_calendar(db)
            masked = mask_young_returns(res.rows.values(), verdict.trade_date, calendar)
            if masked:
                log(f"  上市不满 N+1 个交易日的 {masked} 只：对应的 N 日涨跌幅置空")
        settled = bar_is_settled(verdict.trade_date, now) if verdict.trade_date else None
        status = ("ok" if verdict.complete and settled else
                  "intraday" if verdict.complete else
                  "failed" if not res.rows else "incomplete")
        n_big = sum(1 for r in res.rows.values() if (r["total_market_cap"] or 0) >= 1000)
        log(f"  {src}: 交易日 {verdict.trade_date} 源头 {res.expected} 拿到 {len(res.rows)} "
            f"有效市值 {verdict.valid_caps} ≥1000亿 {n_big} 页 {res.pages_ok}/{res.pages_expected} "
            f"{'已收盘' if settled else '未收盘' if settled is False else '收盘状态未知'}")
        for w in res.warnings:
            log(f"  ⚠️ {w}")
        for r in verdict.reasons:
            log(f"  ❌ {r}")
        attempt = {"source": src, "status": status, "trade_date": verdict.trade_date,
                   "observed": len(res.rows), "expected": res.expected,
                   "valid_caps": verdict.valid_caps, "reasons": verdict.reasons,
                   "warnings": [*res.warnings, *warnings]}
        out["attempts"].append(attempt)

        if not apply:
            if status in ("ok", "intraday"):
                out.update(status=f"dry_run_{status}", trade_date=verdict.trade_date,
                           rows=res.rows)
                return out
            continue

        batch = _record(db, res, verdict, started, settled, status, warnings)
        if status == "ok":
            existing = _latest_applied_batch(db, verdict.trade_date)
            if src == SOURCE_TENCENT and existing is not None and existing.source == SOURCE_EM:
                batch.status = "kept_existing"
                log(f"  {verdict.trade_date} 已有东财完整快照，备用源这批不覆盖")
            else:
                n = _write_rows(db, batch, res.rows.values(), started)
                batch.applied = True
                log(f"  ✅ 写入 {verdict.trade_date} 快照 {n} 行")
            batch.finished_at = datetime.now(SH_TZ).replace(tzinfo=None)
            db.commit()
            out.update(status=batch.status, applied=batch.applied, trade_date=verdict.trade_date,
                       batch_id=batch.id, codes_by_cap=_codes_by_cap(res.rows))
            return out
        batch.finished_at = datetime.now(SH_TZ).replace(tzinfo=None)
        db.commit()
        if status == "intraday":
            log("  盘中数据不写入正式快照（V1 只收收盘后的数据）")
            out.update(status="intraday", trade_date=verdict.trade_date)
            return out
    return out


def _cached_calendar(db: Session) -> List[date]:
    """只读缓存，不发请求（日更每天会刷新它）。"""
    from .trading_calendar import _read_cache, _with_index_days
    return _with_index_days(db, _read_cache(db)) or []


def _codes_by_cap(rows: Dict[str, dict]) -> List[Tuple[str, float]]:
    return sorted(((c, r["total_market_cap"]) for c, r in rows.items() if r["total_market_cap"]),
                  key=lambda x: -x[1])


# ── F10 分类缓存 ────────────────────────────────────────────────────────────

F10_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax"


def fetch_stock_boards(code: str, client: httpx.Client) -> List[list]:
    """[[BK 码, 板块名, 排位], ...]。HTTP / 解析失败抛异常；F10 确实没给就是空列表。"""
    mkt = "SH" if code.startswith(("6", "5", "9")) else "SZ"
    resp = client.get(F10_URL, params={"code": f"{mkt}{code}"}, headers=_F10_HEADERS, timeout=10)
    data = json_or_explain(resp, f"东财F10板块 {code} ")
    out = []
    for it in data.get("ssbk") or []:
        bc = str(it.get("BOARD_CODE") or "").strip()
        if not bc:
            continue
        rank = it.get("BOARD_RANK")
        out.append([f"BK{bc.zfill(4)}", str(it.get("BOARD_NAME") or "").strip(),
                    int(rank) if isinstance(rank, (int, float)) else None])
    return out


def refresh_classification(db: Session, codes_by_cap: Sequence[Tuple[str, float]], *,
                           floor_yi: float, limit: int = 150, stale_days: int = 30,
                           stale_limit: int = 30, log: Callable[[str], None] = print,
                           fetch: Optional[Callable[[str], List[list]]] = None,
                           pause: Callable[[], None] = lambda: time.sleep(0.25),
                           max_consecutive_bad: int = 5) -> dict:
    """
    给市值 ≥ floor_yi 的股票补 F10 板块。先补没有的（大的先），再轮换刷新旧的。

    **F10 拉空不覆盖旧值**：2026-11-02 板块同步就因为 F10 被掐拉空、把关联删光过。
    分类失败不影响行情快照，这一步只记数。

    **连续 max_consecutive_bad 只失败或拉空就停手**：2026-11-08 生产上 F10 每个请求都读超时，
    不停的话 435 只 × 10 秒要跑一个多小时，盘后那个线程和几次重试会一直挂着。
    A 股不存在一个板块都没有的股票，所以拉空也当失败数——那是被掐的样子。
    """
    targets = [c for c, cap in codes_by_cap if cap >= floor_yi]
    have = {r.stock_code: r for r in db.query(LargeCapStockBoards)
            .filter(LargeCapStockBoards.stock_code.in_(targets)).all()} if targets else {}
    now = datetime.now(SH_TZ).replace(tzinfo=None)
    all_missing = [c for c in targets if c not in have or have[c].status != "ok"]
    missing = all_missing[:limit]
    cutoff = now - timedelta(days=stale_days)
    stale = sorted((r for r in have.values() if r.status == "ok" and (r.fetched_at or now) < cutoff),
                   key=lambda r: r.fetched_at)[:stale_limit]
    todo = missing + [r.stock_code for r in stale]
    stats = {"targets": len(targets), "tried": 0, "ok": 0, "empty": 0, "error": 0,
             "remaining_missing": len(all_missing) - len(missing), "aborted": False}
    bad_streak = 0
    if not todo:
        return stats

    client = None
    if fetch is None:
        client = httpx.Client(follow_redirects=True, timeout=10)
        fetch = lambda code: fetch_stock_boards(code, client)  # noqa: E731
    try:
        for i, code in enumerate(todo):
            stats["tried"] += 1
            row = have.get(code)
            if row is None:
                row = LargeCapStockBoards(stock_code=code, status="error")
                db.add(row)
                have[code] = row
            row.attempted_at = now
            try:
                boards = fetch(code)
            except Exception as e:  # noqa: BLE001
                stats["error"] += 1
                bad_streak += 1
                if row.status != "ok":
                    row.status = "error"
                log(f"  F10 {code} 失败：{type(e).__name__}: {str(e)[:60]}")
            else:
                if boards:
                    row.boards = json.dumps(boards, ensure_ascii=False)
                    row.status, row.fetched_at = "ok", now
                    stats["ok"] += 1
                    bad_streak = 0
                else:
                    stats["empty"] += 1
                    bad_streak += 1
                    if row.status != "ok":
                        row.status = "empty"
            if bad_streak >= max_consecutive_bad:
                stats["aborted"] = True
                stats["remaining_missing"] += sum(1 for c in todo[i + 1:] if c in missing)
                log(f"  F10 连续 {bad_streak} 只失败或拉空，像是被掐了，本次停手（快照不受影响，下次同步接着补）")
                break
            if i + 1 < len(todo):
                pause()
            if (i + 1) % 50 == 0:
                db.commit()
                log(f"  F10 分类进度 {i + 1}/{len(todo)}")
        db.commit()
    finally:
        if client is not None:
            client.close()
    return stats
