"""
每日盘面复盘的人工数据读写：草稿、重点观察池、高标关联、每日响应、锁定、次日验证。

三条硬规矩（违反就是在骗自己）：

1. **锁定版本只插不改。** 锁定后再改草稿、再锁，是新的一版。
2. **盘前 / 事后要分得清。** 锁定时间晚于目标交易日 09:30 的版本标「事后记录」，
   验证时优先用最后一份盘前版本。目标交易日按交易日历在验证时重新确定。
3. **没有分时证据不判领先 / 跟随。** 领先、同步、滞后只能在两边当天都有首封时间时记。
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..models.market_recap import (
    LeaderRelation, LeaderResponseDaily, MarketRecap, NextDayPlan, RecapLockedVersion,
    RecapWatchStock,
)

POSTURES = ("attack", "probe", "defend", "unclear")
MISTAKE_TYPES = ("judgment", "execution", "strategy", "none")
SCENARIOS = ("A", "B", "C", "N")
SECTOR_TIERS = ("core", "secondary", "drop")
PLAN_STATUSES = ("waiting", "triggered", "not_triggered", "invalid", "unverified")
MAX_PLANS = 5
ATTRIBUTE_TYPES = ("industry", "concept", "event", "name", "structure", "history")
# 关联依据的性质由属性类型决定，不让人手填：名称联想永远是 C
BASIS_BY_TYPE = {"industry": "A", "event": "A", "concept": "B", "history": "B",
                 "structure": "B", "name": "C"}
CONFIDENCE = ("unverified", "verified", "rejected")
RESPONSES = ("leading", "sync", "lagging", "no_response", "unknown", "pending")
TIMED_RESPONSES = ("leading", "sync", "lagging")
MARKET_OPEN = dtime(9, 30)

# 情景的字段：三个情景各存各的，不是一段笼统评论
# V2 只加字段不改旧字段名——已经存下的草稿不能因为改版丢内容
SCENARIO_FIELDS = {
    "A": ("trigger", "attributes", "sector_confirm", "watch_stocks", "weak_resonance", "overheat"),
    "B": ("reseal", "strong_peers", "successor", "keep_watching"),
    "C": ("logic_invalid", "pause", "drop_stocks", "independent"),
    "N": ("new_core", "same_mainline", "regroup", "evidence", "verify_by"),
}
MAX_LEADER_SCENARIOS = 8
MAX_KEY_POINTS = 3


class RecapError(ValueError):
    pass


def _clean(v: Any, n: int = 4000) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s[:n] or None


# ── 观察池 ──────────────────────────────────────────────────────────────────

def watch_pool(db: Session, owner: str, d: date) -> List[RecapWatchStock]:
    return (db.query(RecapWatchStock)
            .filter(RecapWatchStock.owner == owner, RecapWatchStock.added_date <= d,
                    (RecapWatchStock.removed_date.is_(None)) | (RecapWatchStock.removed_date > d))
            .order_by(RecapWatchStock.deep.desc(), RecapWatchStock.added_date, RecapWatchStock.id).all())


def add_watch(db: Session, owner: str, code: str, d: date, name: Optional[str] = None,
              deep: Optional[bool] = None, note: Optional[str] = None) -> RecapWatchStock:
    code = (code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise RecapError("股票代码要 6 位数字")
    row = next((r for r in watch_pool(db, owner, d) if r.stock_code == code), None)
    if row is None:
        row = RecapWatchStock(owner=owner, stock_code=code, stock_name=_clean(name, 50),
                              added_date=d, deep=bool(deep), note=_clean(note))
        db.add(row)
    else:
        if deep is not None:
            row.deep = bool(deep)
        if note is not None:
            row.note = _clean(note)
        if name and not row.stock_name:
            row.stock_name = _clean(name, 50)
    db.commit()
    return row


def remove_watch(db: Session, owner: str, code: str, d: date) -> bool:
    """只写 removed_date，不删行：之前那些天的观察池照样查得到。"""
    row = next((r for r in watch_pool(db, owner, d) if r.stock_code == code), None)
    if row is None:
        return False
    row.removed_date = d
    db.commit()
    return True


# ── 草稿 ────────────────────────────────────────────────────────────────────

def _plans(db: Session, owner: str, d: date) -> List[NextDayPlan]:
    return (db.query(NextDayPlan).filter(NextDayPlan.owner == owner, NextDayPlan.recap_date == d)
            .order_by(NextDayPlan.sort, NextDayPlan.id).all())


def plan_dict(p: NextDayPlan) -> dict:
    return {k: getattr(p, k) for k in (
        "id", "stock_code", "stock_name", "leader_code", "sector_name", "scenario", "reason",
        "relation_note", "watch_trigger", "entry_trigger", "invalidation", "planned_stop",
        "risk_budget", "position_limit", "observe_until", "status")}


def recap_dict(r: Optional[MarketRecap]) -> Optional[dict]:
    if r is None:
        return None
    return {k: getattr(r, k) for k in (
        "trade_date", "market_posture", "market_evidence", "evidence_note", "invalidation",
        "sector_picks", "scenarios", "flat_plan", "discovery", "mistake", "mistake_type",
        "hypothesis", "deviations", "key_points", "status", "updated_at")}


def _scenario_set(raw: Any) -> dict:
    out = {}
    for k, fields in SCENARIO_FIELDS.items():
        src = (raw or {}).get(k) or {}
        out[k] = {f: _clean(src.get(f), 2000) for f in fields}
    return out


def _scenarios(raw: Any) -> dict:
    """全局一套 + 按重点高标各一套（by_leader）。四个情景各存各的，不是一段笼统评论。"""
    out = _scenario_set(raw)
    by = {}
    for code, sc in list(((raw or {}).get("by_leader") or {}).items())[:MAX_LEADER_SCENARIOS]:
        code = str(code).strip()
        if code.isdigit() and len(code) == 6:
            by[code] = _scenario_set(sc)
    out["by_leader"] = by
    return out


def _plan_rows(raw: Iterable[dict], owner: str, d: date) -> List[NextDayPlan]:
    raw = list(raw or [])
    if len(raw) > MAX_PLANS:
        raise RecapError(f"次日预案最多 {MAX_PLANS} 只，现在 {len(raw)} 只")
    rows, seen = [], set()
    for i, p in enumerate(raw):
        code = (p.get("stock_code") or "").strip()
        if not code.isdigit() or len(code) != 6:
            raise RecapError(f"第 {i + 1} 只的股票代码要 6 位数字")
        if code in seen:
            raise RecapError(f"{code} 重复了")
        seen.add(code)
        entry, inval = _clean(p.get("entry_trigger")), _clean(p.get("invalidation"))
        # 「感觉强就买」不是条件：入场触发和失效条件都得写
        if not entry or not inval:
            raise RecapError(f"{code}：入场触发和失效条件都要写，不能只写选择理由")
        sc = p.get("scenario")
        if sc is not None and sc not in SCENARIOS:
            raise RecapError(f"{code}：情景只能是 {SCENARIOS}")
        status = p.get("status") or "waiting"
        if status not in PLAN_STATUSES:
            raise RecapError(f"{code}：状态只能是 {PLAN_STATUSES}")

        def num(k):
            v = p.get(k)
            if v in (None, ""):
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise RecapError(f"{code}：{k} 要是数字")
            if f < 0:
                raise RecapError(f"{code}：{k} 不能是负数")
            return f
        obs = p.get("observe_until")
        rows.append(NextDayPlan(
            owner=owner, recap_date=d, sort=i, stock_code=code, stock_name=_clean(p.get("stock_name"), 50),
            leader_code=_clean(p.get("leader_code"), 10), sector_name=_clean(p.get("sector_name"), 100),
            scenario=sc, reason=_clean(p.get("reason")), relation_note=_clean(p.get("relation_note")),
            watch_trigger=_clean(p.get("watch_trigger")), entry_trigger=entry, invalidation=inval,
            planned_stop=num("planned_stop"), risk_budget=num("risk_budget"),
            position_limit=num("position_limit"),
            observe_until=date.fromisoformat(obs) if isinstance(obs, str) and obs else None,
            status=status))
    return rows


def save_recap(db: Session, owner: str, d: date, payload: dict) -> MarketRecap:
    posture = payload.get("market_posture")
    if posture is not None and posture not in POSTURES:
        raise RecapError(f"市场姿态只能是 {POSTURES}")
    mt = payload.get("mistake_type")
    if mt is not None and mt not in MISTAKE_TYPES:
        raise RecapError(f"偏差类型只能是 {MISTAKE_TYPES}")
    picks = []
    for p in payload.get("sector_picks") or []:
        if p.get("tier") not in SECTOR_TIERS:
            raise RecapError(f"板块归类只能是 {SECTOR_TIERS}")
        picks.append({"code": _clean(p.get("code"), 20), "name": _clean(p.get("name"), 100),
                      "tier": p["tier"], "state_label": _clean(p.get("state_label"), 20)})
    plans = _plan_rows(payload.get("plans"), owner, d)
    flat = bool(payload.get("flat_plan"))
    if flat and plans:
        raise RecapError("选了空仓预案就不能再有候选股")

    r = db.query(MarketRecap).filter(MarketRecap.owner == owner, MarketRecap.trade_date == d).first()
    if r is None:
        r = MarketRecap(owner=owner, trade_date=d, status="draft")
        db.add(r)
    r.market_posture = posture
    r.market_evidence = [{"key": _clean(e.get("key"), 60), "label": _clean(e.get("label"), 60),
                          "value": _clean(e.get("value"), 120)}
                         for e in (payload.get("market_evidence") or []) if isinstance(e, dict)]
    r.evidence_note = _clean(payload.get("evidence_note"))
    r.invalidation = [x for x in (_clean(v, 500) for v in payload.get("invalidation") or []) if x]
    r.sector_picks = picks
    r.scenarios = _scenarios(payload.get("scenarios"))
    r.flat_plan = flat
    r.discovery = _clean(payload.get("discovery"))
    r.mistake = _clean(payload.get("mistake"))
    r.mistake_type = mt
    r.hypothesis = _clean(payload.get("hypothesis"))
    kp = [x for x in (_clean(v, 300) for v in payload.get("key_points") or []) if x]
    if len(kp) > MAX_KEY_POINTS:
        raise RecapError(f"最重要的观察点最多 {MAX_KEY_POINTS} 条")
    r.key_points = kp
    r.deviations = [{k: _clean(x.get(k), 1000) for k in
                     ("subject", "original", "actual", "kind", "cause", "next_step")}
                    | {"keep_verifying": bool(x.get("keep_verifying"))}
                    for x in (payload.get("deviations") or []) if isinstance(x, dict)]
    db.query(NextDayPlan).filter(NextDayPlan.owner == owner, NextDayPlan.recap_date == d) \
        .delete(synchronize_session=False)
    for p in plans:
        db.add(p)
    db.commit()
    return r


def get_recap(db: Session, owner: str, d: date) -> dict:
    r = db.query(MarketRecap).filter(MarketRecap.owner == owner, MarketRecap.trade_date == d).first()
    return {"recap": recap_dict(r), "plans": [plan_dict(p) for p in _plans(db, owner, d)]}


# ── 高标关联 ────────────────────────────────────────────────────────────────

def relation_dict(r: LeaderRelation) -> dict:
    return {k: getattr(r, k) for k in (
        "id", "leader_code", "leader_name", "attribute_type", "attribute_value", "related_code",
        "related_name", "relation_basis", "evidence", "source", "confidence_status",
        "first_recorded", "last_verified", "note")}


def relations_for(db: Session, owner: str, leader_codes: Iterable[str],
                  include_rejected: bool = False) -> List[LeaderRelation]:
    """
    「不成立」（rejected）默认不返回：批量忽略关联建议就是记成不成立——留着这条记录，
    建议列表里就不会再冒出来，也查得到当初为什么否掉。
    """
    codes = list(leader_codes)
    if not codes:
        return []
    q = (db.query(LeaderRelation)
         .filter(LeaderRelation.owner == owner, LeaderRelation.leader_code.in_(codes),
                 LeaderRelation.removed_at.is_(None)))
    if not include_rejected:
        q = q.filter(LeaderRelation.confidence_status != "rejected")
    return q.order_by(LeaderRelation.leader_code, LeaderRelation.relation_basis,
                      LeaderRelation.attribute_type, LeaderRelation.id).all()


def upsert_relation(db: Session, owner: str, payload: dict, d: date) -> LeaderRelation:
    t = payload.get("attribute_type")
    if t not in ATTRIBUTE_TYPES:
        raise RecapError(f"属性类型只能是 {ATTRIBUTE_TYPES}")
    leader, related = (payload.get("leader_code") or "").strip(), (payload.get("related_code") or "").strip()
    for c in (leader, related):
        if not c.isdigit() or len(c) != 6:
            raise RecapError("股票代码要 6 位数字")
    if leader == related:
        raise RecapError("关联股不能是高标自己")
    value = _clean(payload.get("attribute_value"), 100)
    if not value:
        raise RecapError("属性名不能为空（比如概念名、事件、名称里相同的字）")
    status = payload.get("confidence_status") or "unverified"
    if status not in CONFIDENCE:
        raise RecapError(f"核验状态只能是 {CONFIDENCE}")
    # 名字先查好：下面一旦 db.add 了新行，再查库会触发 autoflush，把还没填完的行写下去
    names = {c: _stock_name(db, c) for c in (leader, related)}
    rid = payload.get("id")
    if rid:
        r = db.query(LeaderRelation).filter(LeaderRelation.id == rid, LeaderRelation.owner == owner).first()
        if r is None:
            raise RecapError("没有这条关联")
    else:
        r = (db.query(LeaderRelation)
             .filter(LeaderRelation.owner == owner, LeaderRelation.leader_code == leader,
                     LeaderRelation.attribute_type == t, LeaderRelation.attribute_value == value,
                     LeaderRelation.related_code == related).first())
        if r is None:
            r = LeaderRelation(owner=owner, first_recorded=d)
            db.add(r)
        r.removed_at = None
    r.leader_code, r.related_code = leader, related
    r.leader_name = _clean(payload.get("leader_name"), 50) or r.leader_name or names[leader]
    r.related_name = _clean(payload.get("related_name"), 50) or r.related_name or names[related]
    r.attribute_type, r.attribute_value = t, value
    r.relation_basis = BASIS_BY_TYPE[t]
    r.evidence = _clean(payload.get("evidence"))
    r.source = _clean(payload.get("source"), 20) or "manual"
    if status != r.confidence_status and status == "verified":
        r.last_verified = d
    r.confidence_status = status
    r.note = _clean(payload.get("note"))
    db.commit()
    return r


def _stock_name(db: Session, code: str) -> Optional[str]:
    """只输了代码时补上名字：股票表 → 最近一份全市场快照。"""
    from ..models.large_cap import LargeCapMarketDaily
    from ..models.stock import Stock
    n = db.query(Stock.name).filter(Stock.code == code).scalar()
    if n:
        return n
    return (db.query(LargeCapMarketDaily.stock_name).filter(LargeCapMarketDaily.stock_code == code)
            .order_by(LargeCapMarketDaily.trade_date.desc()).limit(1).scalar())


def remove_relation(db: Session, owner: str, rid: int) -> bool:
    r = db.query(LeaderRelation).filter(LeaderRelation.id == rid, LeaderRelation.owner == owner).first()
    if r is None:
        return False
    r.removed_at = datetime.now()
    db.commit()
    return True


def responses_on(db: Session, owner: str, d: date, relation_ids: Iterable[int]) -> Dict[int, LeaderResponseDaily]:
    ids = list(relation_ids)
    if not ids:
        return {}
    return {r.relation_id: r for r in db.query(LeaderResponseDaily)
            .filter(LeaderResponseDaily.owner == owner, LeaderResponseDaily.trade_date == d,
                    LeaderResponseDaily.relation_id.in_(ids))}


def response_dict(r: LeaderResponseDaily) -> dict:
    return {"relation_id": r.relation_id, "trade_date": r.trade_date, "response_status": r.response_status,
            "price_change": r.price_change, "limit_up": r.limit_up,
            "leader_trigger_time": r.leader_trigger_time.strftime("%H:%M:%S") if r.leader_trigger_time else None,
            "first_trigger_time": r.first_trigger_time.strftime("%H:%M:%S") if r.first_trigger_time else None,
            "evidence_source": r.evidence_source, "note": r.note}


def upsert_response(db: Session, owner: str, d: date, relation_id: int, status: str,
                    note: Optional[str] = None) -> LeaderResponseDaily:
    from .market_recap_service import first_limit_time, stock_facts
    if status not in RESPONSES:
        raise RecapError(f"响应状态只能是 {RESPONSES}")
    rel = (db.query(LeaderRelation)
           .filter(LeaderRelation.id == relation_id, LeaderRelation.owner == owner).first())
    if rel is None:
        raise RecapError("没有这条关联")
    lt, rt = first_limit_time(db, rel.leader_code, d), first_limit_time(db, rel.related_code, d)
    if status in TIMED_RESPONSES and (lt is None or rt is None):
        raise RecapError("领先 / 同步 / 滞后要两边当天都有首封时间才能判断；没有分时证据，"
                         "只能记「未见明显响应」「无法判断」或「待验证」")
    f = stock_facts(db, [rel.related_code], d).get(rel.related_code) or {}
    r = (db.query(LeaderResponseDaily)
         .filter(LeaderResponseDaily.owner == owner, LeaderResponseDaily.trade_date == d,
                 LeaderResponseDaily.relation_id == relation_id).first())
    if r is None:
        r = LeaderResponseDaily(owner=owner, trade_date=d, relation_id=relation_id)
        db.add(r)
    r.leader_code, r.related_code = rel.leader_code, rel.related_code
    r.price_change, r.limit_up = f.get("pct"), f.get("is_limit_up")
    r.leader_trigger_time, r.first_trigger_time = lt, rt
    r.response_status = status
    r.evidence_source = "首封时间（涨停明细）" if status in TIMED_RESPONSES else "日线收盘事实"
    r.note = _clean(note)
    db.commit()
    return r


# ── 汇总（四项最终结果）────────────────────────────────────────────────────

def summary(db: Session, owner: str, d: date) -> dict:
    g = get_recap(db, owner, d)
    r, plans = g["recap"] or {}, g["plans"]
    pool = watch_pool(db, owner, d)
    rels = relations_for(db, owner, [w.stock_code for w in pool])
    resp = responses_on(db, owner, d, [x.id for x in rels])
    judged = sum(1 for x in resp.values() if x.response_status not in ("pending", "unknown"))
    picks = r.get("sector_picks") or []
    return {
        "key_points": r.get("key_points") or [],
        "market": {"posture": r.get("market_posture"), "evidence": r.get("market_evidence") or [],
                   "evidence_note": r.get("evidence_note"), "invalidation": r.get("invalidation") or []},
        "core": {"core_sectors": [p for p in picks if p["tier"] == "core"],
                 "secondary_sectors": [p for p in picks if p["tier"] == "secondary"],
                 "watch_count": len(pool), "deep_count": sum(1 for w in pool if w.deep),
                 "relations": len(rels), "responses_judged": judged,
                 "responses_pending": len(rels) - judged},
        "plan": {"flat": bool(r.get("flat_plan")), "count": len(plans),
                 "complete": sum(1 for p in plans if p["entry_trigger"] and p["invalidation"]
                                 and (p["risk_budget"] is not None or p["planned_stop"] is not None))},
        "learning": {"discovery": bool(r.get("discovery")), "mistake": bool(r.get("mistake")),
                     "mistake_type": r.get("mistake_type"), "hypothesis": bool(r.get("hypothesis"))},
    }


# ── 锁定 ────────────────────────────────────────────────────────────────────

def next_trading_day(cal: Sequence[date], d: date) -> tuple:
    """(目标交易日, 是否推测)。日历只到今天，明天还不在里面时按下一个工作日推测。"""
    later = [x for x in cal if x > d]
    if later:
        return later[0], False
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd, True


def lock(db: Session, owner: str, d: date, cal: Sequence[date], now: datetime,
         data_status: Optional[dict] = None, cores: Optional[dict] = None) -> RecapLockedVersion:
    from .market_recap_service import stock_facts
    g = get_recap(db, owner, d)
    if g["recap"] is None:
        raise RecapError("先保存草稿再锁定")
    rec, plans = g["recap"], g["plans"]
    if not plans and not rec["flat_plan"]:
        raise RecapError("锁定的是次日预案：至少要有一只候选股，或者明确选「空仓预案」")
    pool = watch_pool(db, owner, d)
    rels = relations_for(db, owner, [w.stock_code for w in pool])
    resp = responses_on(db, owner, d, [x.id for x in rels])
    target, provisional = next_trading_day(cal, d)
    last = (db.query(RecapLockedVersion.version)
            .filter(RecapLockedVersion.owner == owner, RecapLockedVersion.recap_date == d)
            .order_by(RecapLockedVersion.version.desc()).first())
    payload = {
        "recap": {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in rec.items()},
        "plans": [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items()} for p in plans],
        "watch_pool": [{"code": w.stock_code, "name": w.stock_name, "deep": w.deep, "note": w.note,
                        "added_date": w.added_date.isoformat()} for w in pool],
        "relations": [{**{k: (v.isoformat() if isinstance(v, date) else v)
                          for k, v in relation_dict(x).items()},
                       "response": ({k: (v.isoformat() if isinstance(v, date) else v)
                                     for k, v in response_dict(resp[x.id]).items()} if x.id in resp else None)}
                      for x in rels],
        # 锁定那一刻候选股的价格事实——验证时跟次日对照，不靠事后重查
        "plan_facts": {c: {k: v for k, v in f.items() if k in ("pct", "close", "is_limit_up", "board_count")}
                       for c, f in stock_facts(db, [p["stock_code"] for p in plans], d).items()},
        # 当时的人气核心池（成员 + 选入理由）和活跃事件。之后新冒出来的票不会补进这一版
        "cores": cores,
    }
    v = RecapLockedVersion(owner=owner, recap_date=d, version=(last[0] if last else 0) + 1,
                           locked_at=now, target_trade_date=target, target_is_provisional=provisional,
                           payload=payload, data_status=data_status)
    db.add(v)
    r = db.query(MarketRecap).filter(MarketRecap.owner == owner, MarketRecap.trade_date == d).first()
    r.status = "locked"
    db.commit()
    return v


def version_dict(v: RecapLockedVersion, cal: Sequence[date]) -> dict:
    target, provisional = next_trading_day(cal, v.recap_date)
    cutoff = datetime.combine(target, MARKET_OPEN)
    return {"id": v.id, "version": v.version, "recap_date": v.recap_date, "locked_at": v.locked_at,
            "target_trade_date": target, "target_is_provisional": provisional,
            # 锁定晚于目标交易日开盘 = 行情已经开始了，只能算事后记录，不能冒充盘前判断
            "pre_open": v.locked_at < cutoff, "payload": v.payload, "data_status": v.data_status}


def locked_versions(db: Session, owner: str, d: date, cal: Sequence[date]) -> List[dict]:
    rows = (db.query(RecapLockedVersion)
            .filter(RecapLockedVersion.owner == owner, RecapLockedVersion.recap_date == d)
            .order_by(RecapLockedVersion.version).all())
    return [version_dict(v, cal) for v in rows]


def chosen_version(db: Session, owner: str, d: date, cal: Sequence[date]) -> Optional[dict]:
    """拿来准备 / 验证的那一版：最后一份盘前锁定；一份都没有就最新一版（标事后）。"""
    versions = locked_versions(db, owner, d, cal)
    if not versions:
        return None
    pre = [v for v in versions if v["pre_open"]]
    return {**(pre[-1] if pre else versions[-1]), "backfilled": not pre,
            "versions": [{k: v[k] for k in ("version", "locked_at", "pre_open")} for v in versions]}


def prepare(db: Session, owner: str, d: date, cal: Sequence[date]) -> dict:
    """次日准备：d 这天锁定的预案，一屏看完（姿态、重点高标、关联、事件、触发与放弃条件）。"""
    v = chosen_version(db, owner, d, cal)
    if v is None:
        return {"status": "no_lock", "message": f"{d} 没有锁定的预案"}
    p = v["payload"]
    rec = p.get("recap") or {}
    deep = [w for w in p.get("watch_pool") or [] if w.get("deep")]
    rel_by_leader: Dict[str, list] = {}
    for x in p.get("relations") or []:
        rel_by_leader.setdefault(x["leader_code"], []).append(
            {k: x.get(k) for k in ("related_code", "related_name", "attribute_type", "attribute_value",
                                   "relation_basis")} | {"response": (x.get("response") or {}).get("response_status")})
    cores = p.get("cores") or {}
    focus_codes = {w["code"] for w in deep} | {x.get("leader_code") for x in p.get("plans") or [] if x.get("leader_code")}
    return {"status": "ok", "recap_date": d,
            **{k: v[k] for k in ("version", "locked_at", "pre_open", "target_trade_date", "backfilled", "versions")},
            "posture": rec.get("market_posture"), "invalidation": rec.get("invalidation") or [],
            "key_points": rec.get("key_points") or [], "flat_plan": bool(rec.get("flat_plan")),
            "focus_leaders": deep, "relations": {c: rel_by_leader.get(c, []) for c in focus_codes},
            "events": [e for e in cores.get("events") or [] if e.get("leader_code") in focus_codes]
            or (cores.get("events") or [])[:20],
            "scenarios": rec.get("scenarios") or {}, "plans": p.get("plans") or []}


# ── 次日验证 ────────────────────────────────────────────────────────────────

def verification(db: Session, owner: str, d: date, cal: Sequence[date], now: datetime) -> dict:
    """
    拿 d 这天锁定的预案，对照下一个交易日实际发生的事实。用的是**最后一份盘前锁定版本**；
    一份盘前版本都没有，就用最新一版并标明是事后记录。草稿不参与——事后改过的内容不能替代原判断。
    """
    from .eastmoney_fetcher import bar_is_settled
    from .market_recap_service import index_module, stock_facts
    versions = locked_versions(db, owner, d, cal)
    if not versions:
        return {"status": "no_lock", "message": f"{d} 没有锁定的预案"}
    pre = [v for v in versions if v["pre_open"]]
    chosen = pre[-1] if pre else versions[-1]
    target, provisional = chosen["target_trade_date"], chosen["target_is_provisional"]
    base = {"version": {k: chosen[k] for k in ("version", "locked_at", "pre_open")},
            "backfilled": not chosen["pre_open"], "target_trade_date": target,
            "versions": [{k: v[k] for k in ("version", "locked_at", "pre_open")} for v in versions]}
    if provisional or target not in set(cal):
        return {**base, "status": "pending",
                "message": f"交易日历里还没有 {d} 之后的交易日（{target} 是按工作日推测的），等它出现并收盘后再验证"}
    if not bar_is_settled(target, now):
        return {**base, "status": "pending", "message": f"{target} 还没收盘，等收盘后再验证"}
    p = chosen["payload"]
    plans = p.get("plans") or []
    facts = stock_facts(db, [x["stock_code"] for x in plans], target)
    at_lock = p.get("plan_facts") or {}
    rows = []
    for x in plans:
        f = facts.get(x["stock_code"]) or {}
        rows.append({"stock_code": x["stock_code"], "stock_name": x.get("stock_name") or f.get("name"),
                     "scenario": x.get("scenario"), "entry_trigger": x["entry_trigger"],
                     "invalidation": x["invalidation"], "planned_stop": x.get("planned_stop"),
                     "at_lock": at_lock.get(x["stock_code"]),
                     "actual": {k: f.get(k) for k in ("status", "pct", "close", "is_limit_up", "board_count",
                                                      "first_limit_time")},
                     # 跌破计划止损价只在有收盘价时才能说
                     "below_stop": (f["close"] < x["planned_stop"]
                                    if f.get("close") is not None and x.get("planned_stop") else None)})
    idx = index_module(db, target)
    return {**base, "status": "ok",
            "market": {"posture": (p.get("recap") or {}).get("market_posture"),
                       "invalidation": (p.get("recap") or {}).get("invalidation") or [],
                       "indices": [{k: i.get(k) for k in ("code", "name", "status", "pct", "above_ma20",
                                                          "ma20_dir")} for i in idx["items"]]},
            "flat_plan": bool((p.get("recap") or {}).get("flat_plan")), "plans": rows}
