"""
每日盘面复盘接口。**全部要登录**，人工数据按用户名隔离——个人交易研究不能只靠前端
RequireLogin 挡着。市场事实（/context）也跟着要登录，是为了整个复盘流程一个口径。

静态路径（/dates /context /live /watch /leader-relations /responses）写在 /{trade_date} 前面，
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


@router.get("/live")
def recap_live(db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """
    盘中跟踪：上一交易日锁定的预案、观察池、重点高标的关联股、人气核心池 × 实时行情。
    **只读**：不落库、不冻结、不改锁定版本；盘中事件全部标临时（provisional）。
    """
    from ..services import market_recap_live_service as live_svc
    return live_svc.build_live(db, user)


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


@router.post("/watch/{code}/restore")
def restore_watch(code: str, date_: date = Query(..., alias="date"), db: Session = Depends(get_db),
                  user: str = Depends(require_auth)):
    """撤销这一天的取消观察（加入日期、★、备注保持原样）。"""
    if not store.restore_watch(db, user, code, date_):
        raise HTTPException(status_code=404, detail="这一天没有取消过这只，或者它已经在观察池里")
    return {"ok": True}


# ── 高标关联 ────────────────────────────────────────────────────────────────

@router.get("/leader-relations/{code}")
def leader_relations(code: str, date_: date = Query(..., alias="date"), name: Optional[str] = None,
                     include_rejected: bool = False,
                     db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """
    一只高标的关联知识（跨日继承）+ 关联股在这一天的事实、系统响应分类、人工响应 + 关联建议。
    摘要按股票去重：一只票挂了三个属性，「今日涨停」只算一只。
    """
    from ..models.market_recap import LeaderActivationEvent
    from ..services.popular_core_service import RESPONSE_LABEL, classify_response
    rels = store.relations_for(db, user, [code], include_rejected=include_rejected)
    all_rels = store.relations_for(db, user, [code], include_rejected=True)
    facts = ctx_svc.stock_facts(db, [code] + [r.related_code for r in rels], date_)
    resp = store.responses_on(db, user, date_, [r.id for r in rels])
    leader = facts.get(code) or {}
    leader_name = name or leader.get("name") or next((r.leader_name for r in rels if r.leader_name), None)
    leader_events = [e for (e,) in db.query(LeaderActivationEvent.event_type)
                     .filter(LeaderActivationEvent.trade_date == date_, LeaderActivationEvent.leader_code == code)]
    have = {(r.attribute_type, r.related_code) for r in all_rels}
    # 同板块的票已经在「板块关联」里按板块列出来了，这里只给名称联想建议
    sugg = [s for s in ctx_svc.name_suggestions(db, code, leader_name or "", date_)
            if (s["attribute_type"], s["related_code"]) not in have]
    rows = [{**store.relation_dict(r), "facts": facts.get(r.related_code),
             "system_response": classify_response(facts.get(r.related_code), leader, leader_events),
             "response": store.response_dict(resp[r.id]) if r.id in resp else None}
            for r in rels]
    active = [x for x in rows if x["confidence_status"] != "rejected"]
    by_type, by_resp = {}, {}
    for x in active:
        by_type.setdefault(x["attribute_type"], set()).add(x["related_code"])
    for c, sr in {x["related_code"]: x["system_response"] for x in active}.items():
        by_resp[sr] = by_resp.get(sr, 0) + 1
    return {"leader": {"code": code, "name": leader_name, "facts": leader, "events": leader_events},
            "relations": rows, "suggestions": sugg,
            "summary": {"stocks": len({x["related_code"] for x in active}),
                        "by_type": {k: len(v) for k, v in by_type.items()},
                        "by_response": by_resp, "response_labels": RESPONSE_LABEL,
                        "manual_pending": sum(1 for x in active
                                              if (x["response"] or {}).get("response_status") == "pending")}}


@router.get("/leader-sectors/{code}")
def leader_sectors(code: str, date_: date = Query(..., alias="date"), db: Session = Depends(get_db),
                   user: str = Depends(require_auth)):
    """板块关联（自动）：高标所在的每个板块里，当天的活跃股；每只带系统响应分类。"""
    from ..models.market_recap import LeaderActivationEvent
    from ..services.popular_core_service import RESPONSE_LABEL, classify_response
    res = ctx_svc.sector_peers(db, code, date_)
    events = [e for (e,) in db.query(LeaderActivationEvent.event_type)
              .filter(LeaderActivationEvent.trade_date == date_, LeaderActivationEvent.leader_code == code)]
    for g in res["groups"]:
        for st in g["stocks"]:
            st["system_response"] = classify_response(st["facts"], res.get("leader"), events)
    return {**res, "response_labels": RESPONSE_LABEL}


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


# ── 人气核心池 / 活跃事件 ──────────────────────────────────────────────────

def _latest_snapshot_date(db: Session) -> Optional[date]:
    from sqlalchemy import func
    from ..models.stock import StockDailySnapshot
    return db.query(func.max(StockDailySnapshot.date)).scalar()


def _cores(db: Session, d: date) -> dict:
    from ..services import popular_core_service as pc
    cal = _cal(db, need=d)
    if d not in set(cal):
        raise HTTPException(status_code=400, detail=f"{d} 不在交易日历里")
    from ..services.eastmoney_fetcher import bar_is_settled
    if not bar_is_settled(d, _now()):
        raise HTTPException(status_code=400, detail=f"{d} 还没收盘：人气核心和活跃事件只用收盘后的数据")
    return pc.load(db, d, cal, _latest_snapshot_date(db))


@router.get("/popular-cores")
def popular_cores(date_: date = Query(..., alias="date"), db: Session = Depends(get_db),
                  user: str = Depends(require_auth)):
    """
    指定交易日的人气核心池。冻结过读冻结的；没冻结现算（frozen=false），全部用那一天的数据。
    叠加我自己的：观察池（「我的重点」，不受自动榜单淘汰）、每只的关联股数。
    """
    from ..services import popular_core_service as pc
    res = _cores(db, date_)
    watched = {w.stock_code: w for w in store.watch_pool(db, user, date_)}
    items = list(res["items"])
    have = {i["stock_code"] for i in items}
    extra = [c for c in watched if c not in have]
    if extra:
        f = ctx_svc.stock_facts(db, extra, date_)
        for c in extra:
            items.append({"stock_code": c, "stock_name": watched[c].stock_name or (f.get(c) or {}).get("name"),
                          "board": pc.board_of(c), "selection_sources": [{"source": "manual", "rank": None,
                                                                           "value": None, "scope": "我的观察池"}],
                          "pct_change_5d": (f.get(c) or {}).get("pct_5d"), "pct_change_20d": (f.get(c) or {}).get("pct_20d"),
                          "data_status": {"missing": ["不在当天的人气核心池里，只有基础行情；活跃事件只对池内成员计算"]}})
    rel_n: dict = {}
    for r in store.relations_for(db, user, [i["stock_code"] for i in items]):
        rel_n.setdefault(r.leader_code, set()).add(r.related_code)
    ev_codes = {e["leader_code"] for e in res["events"]}
    events_by: dict = {}
    for e in res["events"]:
        events_by.setdefault(e["leader_code"], []).append(e["event_type"])
    for i in items:
        i["watched"] = i["stock_code"] in watched
        i["deep"] = bool(watched.get(i["stock_code"]) and watched[i["stock_code"]].deep)
        i["relations"] = len(rel_n.get(i["stock_code"], ()))
        i["events"] = events_by.get(i["stock_code"], [])
        i["views"] = pc.view_of(i, ev_codes, set(watched))
    return {"date": date_, "rule_version": res["rule_version"], "rules": res["rules"], "frozen": res["frozen"],
            "built_at": res["built_at"], "sources": res["sources"], "source_labels": pc.SOURCE_LABEL,
            "premium_is_current": res["premium_is_current"], "items": items}


@router.get("/leader-activations")
def leader_activations(date_: date = Query(..., alias="date"), event_type: Optional[str] = None,
                       db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """当天人气核心活跃事件。只是提醒去看关联池，不是买入信号。"""
    from ..services import popular_core_service as pc
    res = _cores(db, date_)
    events = [e for e in res["events"] if not event_type or e["event_type"] == event_type]
    return {"date": date_, "frozen": res["frozen"], "labels": pc.EVENT_LABEL, "events": events,
            "not_supported": ["关键价格突破：要先有你写下的价格触发条件，V2 还没接"]}


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
    key_points: List[str] = []
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
    from ..services import popular_core_service as pc
    cal = _cal(db, need=trade_date)
    ctx = ctx_svc.build_context(db, trade_date, now=_now())
    try:
        cores = pc.load(db, trade_date, cal, _latest_snapshot_date(db)) if trade_date in set(cal) else None
    except Exception:  # noqa: BLE001 —— 核心池挂了不挡锁定，锁定快照里如实记成 None
        db.rollback()
        cores = None
    core_snap = None if cores is None else _jsonable({
        "frozen": cores["frozen"], "rule_version": cores["rule_version"], "sources": cores["sources"],
        "items": [{k: i.get(k) for k in ("stock_code", "stock_name", "selection_sources", "lifecycle_state")}
                  for i in cores["items"]],
        "events": [{k: (str(v) if k == "event_time" and v else v) for k, v in e.items()} for e in cores["events"]]})
    try:
        v = store.lock(db, user, trade_date, cal, _now(), data_status=_jsonable(ctx.get("data_status")),
                       cores=core_snap)
    except RecapError as e:
        _bad(e)
    return store.version_dict(v, cal)


@router.get("/{trade_date}/locked")
def locked(trade_date: date, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    return store.locked_versions(db, user, trade_date, _cal(db))


@router.get("/{trade_date}/prepare")
def prepare(trade_date: date, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """次日准备：这一天锁定的预案（最后一份盘前版本），一屏看完。"""
    return _jsonable(store.prepare(db, user, trade_date, _cal(db)))


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
