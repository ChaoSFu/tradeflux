"""
每日盘面复盘接口。**全部要登录**，人工数据按用户名隔离——个人交易研究不能只靠前端
RequireLogin 挡着。市场事实（/context）也跟着要登录，是为了整个复盘流程一个口径。

静态路径（/dates /context /watch /leader-relations /responses）写在 /{trade_date} 前面，
不然会被当成日期去解析。
"""
from datetime import date, datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..database import get_db
from ..models.market_recap import MarketRecap, RecapLockedVersion
from ..services import market_recap_service as ctx_svc
from ..services import market_recap_store as store
from ..services.market_recap_store import RecapError

router = APIRouter(prefix="/market-recap", tags=["market-recap"])


def _now() -> datetime:
    from ..services.eastmoney_fetcher import SH_TZ
    return datetime.now(SH_TZ).replace(tzinfo=None)


def _cal(db: Session, need: Optional[date] = None) -> list:
    from ..services.trading_calendar import get_trading_days
    return get_trading_days(db, need_through=need) or []


def _bad(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


@router.get("/dates")
def recap_dates(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """能复盘的交易日（已收盘、有快照），以及每天我自己的草稿 / 锁定情况。"""
    days = ctx_svc.recap_dates(db)
    recaps = {r.trade_date: r.status for r in db.query(MarketRecap.trade_date, MarketRecap.status)
              .filter(MarketRecap.owner == user)}
    locks = {}
    for d, in db.query(RecapLockedVersion.recap_date).filter(RecapLockedVersion.owner == user):
        locks[d] = locks.get(d, 0) + 1
    return [{"date": d, "recap_status": recaps.get(d), "locked_versions": locks.get(d, 0)} for d in days]


@router.get("/context")
def recap_context(date_: date = Query(..., alias="date"), db: Session = Depends(get_db),
                  user: str = Depends(require_auth)):
    watched = [w.stock_code for w in store.watch_pool(db, user, date_)]
    return ctx_svc.build_context(db, date_, watched=watched, now=_now())


# ── 观察池 ──────────────────────────────────────────────────────────────────

class WatchIn(BaseModel):
    code: str
    date: date
    name: Optional[str] = None
    deep: Optional[bool] = None
    note: Optional[str] = None


@router.post("/watch")
def add_watch(body: WatchIn, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    try:
        w = store.add_watch(db, user, body.code, body.date, name=body.name, deep=body.deep, note=body.note)
    except RecapError as e:
        _bad(e)
    return {"ok": True, "id": w.id}


@router.delete("/watch/{code}")
def remove_watch(code: str, date_: date = Query(..., alias="date"), db: Session = Depends(get_db),
                 user: str = Depends(require_auth)):
    if not store.remove_watch(db, user, code, date_):
        raise HTTPException(status_code=404, detail="观察池里没有这只")
    return {"ok": True}


# ── 高标关联 ────────────────────────────────────────────────────────────────

@router.get("/leader-relations/{code}")
def leader_relations(code: str, date_: date = Query(..., alias="date"), name: Optional[str] = None,
                     db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """一只高标的关联知识（跨日继承）+ 关联股在这一天的事实和响应 + 关联建议。"""
    rels = store.relations_for(db, user, [code])
    facts = ctx_svc.stock_facts(db, [code] + [r.related_code for r in rels], date_)
    resp = store.responses_on(db, user, date_, [r.id for r in rels])
    leader = facts.get(code) or {}
    leader_name = name or leader.get("name") or next((r.leader_name for r in rels if r.leader_name), None)
    have = {(r.attribute_type, r.related_code) for r in rels}
    sugg = [s for s in (ctx_svc.name_suggestions(db, code, leader_name or "", date_)
                        + ctx_svc.concept_suggestions(db, code, date_))
            if (s["attribute_type"], s["related_code"]) not in have]
    return {"leader": {"code": code, "name": leader_name, "facts": leader},
            "relations": [{**store.relation_dict(r), "facts": facts.get(r.related_code),
                           "response": store.response_dict(resp[r.id]) if r.id in resp else None}
                          for r in rels],
            "suggestions": sugg}


class RelationIn(BaseModel):
    id: Optional[int] = None
    date: date
    leader_code: str
    leader_name: Optional[str] = None
    attribute_type: str
    attribute_value: str
    related_code: str
    related_name: Optional[str] = None
    evidence: Optional[str] = None
    source: Optional[str] = None
    confidence_status: Optional[str] = None
    note: Optional[str] = None


@router.put("/leader-relations")
def put_relation(body: RelationIn, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    try:
        r = store.upsert_relation(db, user, body.model_dump(), body.date)
    except RecapError as e:
        _bad(e)
    return store.relation_dict(r)


@router.delete("/leader-relations/{rid}")
def delete_relation(rid: int, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    if not store.remove_relation(db, user, rid):
        raise HTTPException(status_code=404, detail="没有这条关联")
    return {"ok": True}


class ResponseIn(BaseModel):
    date: date
    relation_id: int
    response_status: str
    note: Optional[str] = None


@router.put("/responses")
def put_response(body: ResponseIn, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    try:
        r = store.upsert_response(db, user, body.date, body.relation_id, body.response_status, body.note)
    except RecapError as e:
        _bad(e)
    return store.response_dict(r)


# ── 按日期 ──────────────────────────────────────────────────────────────────

@router.get("/{trade_date}")
def get_recap(trade_date: date, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    cal = _cal(db)
    pool = store.watch_pool(db, user, trade_date)
    facts = ctx_svc.stock_facts(db, [w.stock_code for w in pool], trade_date)
    rels = store.relations_for(db, user, [w.stock_code for w in pool])
    rel_count = {}
    for r in rels:
        rel_count[r.leader_code] = rel_count.get(r.leader_code, 0) + 1
    i = cal.index(trade_date) if trade_date in set(cal) else None
    prev = cal[i - 1] if i else None
    return {
        **store.get_recap(db, user, trade_date),
        "watch_pool": [{"code": w.stock_code, "name": w.stock_name or (facts.get(w.stock_code) or {}).get("name"),
                        "deep": w.deep, "note": w.note, "added_date": w.added_date,
                        "relations": rel_count.get(w.stock_code, 0), "facts": facts.get(w.stock_code)}
                       for w in pool],
        "summary": store.summary(db, user, trade_date),
        "locked_versions": [{k: v[k] for k in ("version", "locked_at", "pre_open", "target_trade_date",
                                               "target_is_provisional")}
                            for v in store.locked_versions(db, user, trade_date, cal)],
        # 上一交易日锁定的预案（盘前准备 / 今天验证昨天）
        "prev_date": prev,
        "prev_verification": store.verification(db, user, prev, cal, _now()) if prev else None,
    }


class RecapIn(BaseModel):
    market_posture: Optional[str] = None
    market_evidence: List[Any] = []
    evidence_note: Optional[str] = None
    invalidation: List[str] = []
    sector_picks: List[Any] = []
    scenarios: dict = {}
    flat_plan: bool = False
    discovery: Optional[str] = None
    mistake: Optional[str] = None
    mistake_type: Optional[str] = None
    hypothesis: Optional[str] = None
    deviations: List[Any] = []
    plans: List[Any] = []


@router.put("/{trade_date}")
def put_recap(trade_date: date, body: RecapIn, db: Session = Depends(get_db),
              user: str = Depends(require_auth)):
    if trade_date not in set(_cal(db, need=trade_date)):
        raise HTTPException(status_code=400, detail=f"{trade_date} 不是交易日")
    try:
        store.save_recap(db, user, trade_date, body.model_dump())
    except RecapError as e:
        _bad(e)
    return store.get_recap(db, user, trade_date)


@router.post("/{trade_date}/lock")
def lock_recap(trade_date: date, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """锁定当前草稿为新的一版（只插不改）。顺带记下锁定时各事实模块的数据状态。"""
    cal = _cal(db, need=trade_date)
    ctx = ctx_svc.build_context(db, trade_date, now=_now())
    try:
        v = store.lock(db, user, trade_date, cal, _now(), data_status=_jsonable(ctx.get("data_status")))
    except RecapError as e:
        _bad(e)
    return store.version_dict(v, cal)


@router.get("/{trade_date}/locked")
def locked(trade_date: date, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    return store.locked_versions(db, user, trade_date, _cal(db))


@router.get("/{trade_date}/verification")
def verify(trade_date: date, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    return store.verification(db, user, trade_date, _cal(db), _now())


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v
