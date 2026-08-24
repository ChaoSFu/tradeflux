"""
弱转强雷达 API：候选列表/详情（含Checklist）、独立快速刷新（自己的锁文件，
不碰 daily_update 那把锁）、状态变化事件日志、配置读写。

候选发现（两路 Prompt → weak_to_strong_candidates）不在这里触发，由
scripts/daily_update.py 的既有每日流程调用 w2s_candidate_service.discover_candidates，
本 router 的 /refresh 只做"已发现候选"的快速状态刷新——职责边界见方案
"调度与锁"一节。
"""
import fcntl
import os
import threading
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..database import SessionLocal, get_db
from ..models.weak_to_strong_radar import (
    WeakToStrongCandidate, WeakToStrongEvent, WeakToStrongSnapshot, WeakToStrongDiscoveryRun,
)
from ..schemas.weak_to_strong_radar import (
    CandidateResponse, CandidateDetailResponse, ChecklistGroup,
    RefreshResultResponse, RefreshStatusResponse, EventResponse, SnapshotResponse, DiscoveryRunResponse,
    W2SConfigResponse, W2SConfigUpdateRequest, MarketGateResponse, MainlineSector, MainlinesResponse,
)
from ..services import w2s_config_service as cfg
from ..services import w2s_market_gate_service as market_gate
from ..services import w2s_sector_gate_service as sector_gate
from ..services import w2s_state_machine as sm
from ..services.w2s_refresh_service import run_refresh
from ..services.eastmoney_fetcher import _w2s_log
from .admin import record_job_duration

router = APIRouter(prefix="/weak-to-strong-radar", tags=["weak-to-strong-radar"])

LOCK_FILE = "/tmp/tradeflux_w2s_radar.lock"

_lock = threading.Lock()
_job: dict = {"running": False, "last_result": None, "last_error": None}


def _run_refresh_job() -> None:
    lock_fd = None
    db = SessionLocal()
    _w2s_log("MANUAL", "用户点击「刷新数据并重新评估」，开始执行")
    try:
        lock_fd = open(LOCK_FILE, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _w2s_log("MANUAL", "❌ 锁已被占用（可能是09:26定时任务正在跑），本次跳过")
            with _lock:
                _job["running"] = False
                _job["last_error"] = "已有雷达刷新任务在运行中，请稍后再试"
            return

        started = datetime.now().isoformat(timespec="seconds")
        stats = run_refresh(db)
        finished = datetime.now().isoformat(timespec="seconds")
        record_job_duration("w2s_refresh", started, finished)
        _w2s_log("MANUAL", f"✅ 完成: {stats}")
        with _lock:
            _job["running"] = False
            _job["last_error"] = None
            _job["last_result"] = {**stats, "triggered_at": datetime.now()}
    except Exception as exc:  # noqa: BLE001
        import traceback
        _w2s_log("MANUAL", f"❌ 刷新异常: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        with _lock:
            _job["running"] = False
            _job["last_error"] = str(exc)
    finally:
        db.close()
        if lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


@router.post("/refresh")
def trigger_refresh(_: str = Depends(require_auth)):
    with _lock:
        if _job["running"]:
            return {"ok": False, "message": "已有雷达刷新任务在运行中，请稍后"}
        _job["running"] = True
    t = threading.Thread(target=_run_refresh_job, daemon=True)
    t.start()
    return {"ok": True, "message": "雷达刷新已启动"}


@router.get("/refresh/status", response_model=RefreshStatusResponse)
def get_refresh_status():
    with _lock:
        return dict(_job)


@router.get("/market-gate", response_model=MarketGateResponse)
def get_market_gate(db: Session = Depends(get_db)):
    return market_gate.get_market_gate(db)


@router.get("/mainlines", response_model=MainlinesResponse)
def get_mainlines(db: Session = Depends(get_db)):
    """
    "今日主线"摘要（2026-08-24新增）：全市场 is_watched 板块里，Mainline Top N
    是哪几个，板块视角、不依赖任何W2S候选是否命中。纯本地DB计算（跟 run_refresh()
    共用同一份 get_current_mainlines()，不会两边算出不一样的结果），不触发任何
    外部行情请求，随时可调用。0~3个是上限不是配额，没有就返回空列表，前端不
    应该为了凑数硬显示。
    """
    today = date.today()
    result = sector_gate.get_current_mainlines(db, today)
    ordered = sorted(
        result["mainline_sector_ids"],
        key=lambda sid: (
            -(result["sector_score_cache"][sid].get("sector_strength_score") or 0.0), sid,
        ),
    )
    mainlines = [
        MainlineSector(
            sector_id=sid,
            sector_code=result["sectors_by_id"][sid].code,
            sector_name=result["sectors_by_id"][sid].name,
            rank=i + 1,
            sector_category=result["sector_score_cache"][sid]["sector_category"],
            sector_strength_score=result["sector_score_cache"][sid]["sector_strength_score"],
            sector_momentum_score=result["sector_score_cache"][sid]["sector_momentum_score"],
            sector_divergence_health=result["sector_score_cache"][sid]["sector_divergence_health"],
        )
        for i, sid in enumerate(ordered)
    ]
    return MainlinesResponse(
        mainlines=mainlines,
        data_as_of=str(result["data_as_of"]) if result["data_as_of"] else None,
    )


@router.get("/candidates", response_model=list[CandidateResponse])
def list_candidates(active_only: bool = True, db: Session = Depends(get_db)):
    q = db.query(WeakToStrongCandidate)
    if active_only:
        q = q.filter(WeakToStrongCandidate.is_active == True)  # noqa: E712
    return q.order_by(WeakToStrongCandidate.last_refreshed_at.desc().nullslast()).all()


def _build_checklist(
    cand: WeakToStrongCandidate, gate: dict, market_gate_blocked: set[str], space_min_room_pct: float,
) -> list[ChecklistGroup]:
    market_state = gate["market_state"]
    return [
        ChecklistGroup(
            group="MARKET",
            status="fail" if market_state in market_gate_blocked else "pass",
            detail=f"{market_state}（趋势分{gate['trend_score'] if gate['trend_score'] is not None else '-'}"
                   f" / 风险偏好分{gate['risk_score'] if gate['risk_score'] is not None else '-'}"
                   f" / 市场负反馈{gate.get('market_negative_feedback', 'UNKNOWN')}"
                   + (f"[亏钱效应{gate['market_effect_loss_strength']:.0f}]" if gate.get('market_effect_loss_strength') is not None else "")
                   + "）",
        ),
        ChecklistGroup(
            group="SECTOR",
            status="pass" if cand.sector_category in ("NEW_START", "EXPANDING", "MAIN_UPTREND", "HEALTHY_DIVERGENCE") else "fail",
            detail=f"{cand.sector_name or '未知板块'} · {cand.sector_category or '无'}"
                   f"（强度{cand.sector_strength_score if cand.sector_strength_score is not None else '-'}"
                   f" / 动量{cand.sector_momentum_score if cand.sector_momentum_score is not None else '-'}"
                   + (f" / 分歧健康度{cand.sector_divergence_health:.0f}" if cand.sector_divergence_health is not None else "")
                   + (" / 属于当前主升前列" if cand.is_mainline_sector else " / 不在当前主升前列，结构确认封顶CONFIRMING")
                   + "）",
        ),
        ChecklistGroup(
            group="LEADER",
            # 真实闸门逻辑（w2s_state_machine.compute_gate_blocks/derive_display_state）
            # 只有 non_leader 是硬拦截；undetermined 是软上限（结构确认封顶CONFIRMING，
            # 不放行BUYABLE，但不是"打回"）；backup 完全不受任何限制，跟 core 走同样
            # 的放行路径。此前 core 以外一律显示"fail"，会出现"Checklist标LEADER
            # 失败，候选却已经是BUYABLE"的自相矛盾（backup可以到BUYABLE）——round4
            # review 指出的真实bug，这里改成跟真实闸门语义对齐（2026-08-23修复）。
            status="pass" if cand.leader_type in ("core", "backup") else (
                "advisory" if cand.leader_type == "undetermined" else "fail"
            ),
            detail=f"{cand.leader_type or '未知'}"
                   + (f" · 第{cand.leader_rank}名" if cand.leader_rank else "")
                   + (f" · Core Leader Score {cand.leader_score}" if cand.leader_score is not None else "")
                   + ("（结构确认后暂封顶CONFIRMING，不放行BUYABLE）" if cand.leader_type == "undetermined" else ""),
        ),
        ChecklistGroup(
            group="DIVERGENCE",
            status="pass" if cand.sector_category != "HIGH_LEVEL_WARNING" else "fail",
            detail="板块分歧健康度已纳入 Sector Gate 分类判断" if cand.sector_category else "暂无数据",
        ),
        ChecklistGroup(
            group="SETUP",
            status="pass" if cand.structural_state in (
                sm.STRUCT_READY, sm.STRUCT_REPAIRING, sm.STRUCT_PULLBACK, sm.STRUCT_CONFIRMED,
            ) else "fail",
            detail=(
                f"展示态 {cand.current_state}"
                + (f"（底层结构事实已到 {cand.structural_state}，当前被闸门/软上限临时覆盖展示）"
                   if cand.structural_state != cand.current_state else "")
                + "："
                + (cand.trigger_reasons or cand.block_reasons or "暂无变化")
            ),
        ),
        ChecklistGroup(
            group="SPACE",
            status="pass" if (cand.limit_room is not None and cand.limit_room >= space_min_room_pct) else "fail",
            detail=(
                f"涨停空间 {cand.limit_room:.1f}%（阈值 {space_min_room_pct:.1f}%）"
                if cand.limit_room is not None else "涨停空间数据缺失"
            ),
        ),
        ChecklistGroup(group="CHIPS", status="phase2", detail="日内获利盘估算需分钟级数据，本仓库目前无该数据源，Phase 3 视情况补充"),
        ChecklistGroup(
            group="RISK",
            # Stress R/R 在真实闸门逻辑里从来不是 Hard Blocker、也不是 Soft Cap 输入
            # （w2s_state_machine.py 全文不引用 stress_rr，见 w2s_risk_service.py 模块
            # 头注释"只回答值不值得担这个风险，不是完整期望收益模型"），只是展示给
            # 用户参考。此前低于1.0标"fail"，会出现"Checklist标RISK失败，候选却已经
            # 是BUYABLE"的自相矛盾——round4 review 指出的真实bug，这里统一改成
            # advisory，永不参与pass/fail判断（2026-08-23修复）。
            status="advisory",
            detail=(
                f"Stress R/R {cand.stress_rr:.2f}"
                f"（技术止损{cand.technical_stop if cand.technical_stop is not None else '-'} / "
                f"标准止损{cand.standard_stop if cand.standard_stop is not None else '-'} / "
                f"压力止损{cand.stress_stop if cand.stress_stop is not None else '-'}）"
                if cand.stress_rr is not None else "风险回报数据缺失"
            ),
        ),
    ]


@router.get("/candidates/{code}", response_model=CandidateDetailResponse)
def get_candidate_detail(code: str, db: Session = Depends(get_db)):
    cand = db.query(WeakToStrongCandidate).filter(WeakToStrongCandidate.stock_code == code).first()
    if cand is None:
        raise HTTPException(status_code=404, detail="候选不存在")
    base = CandidateResponse.model_validate(cand, from_attributes=True)
    gate = market_gate.get_market_gate(db)
    blocked = cfg.get_market_gate_blocked(db)
    space_min_room_pct = cfg.get_numeric(db, cfg.KEY_SPACE_MIN_ROOM_PCT)
    return CandidateDetailResponse(
        **base.model_dump(), checklist=_build_checklist(cand, gate, blocked, space_min_room_pct),
    )


@router.get("/events", response_model=list[EventResponse])
def list_events(stock_code: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    q = db.query(WeakToStrongEvent)
    if stock_code:
        q = q.filter(WeakToStrongEvent.stock_code == stock_code)
    return q.order_by(WeakToStrongEvent.timestamp.desc()).limit(min(limit, 500)).all()


@router.get("/snapshots", response_model=list[SnapshotResponse])
def list_snapshots(stock_code: str, trade_date: date | None = None, limit: int = 500, db: Session = Depends(get_db)):
    """
    候选专属日内快照，按 stock_code 必填过滤（这张表是稠密采样，不像 events
    那样天然稀疏，不加股票过滤条件容易一次查出全候选池的量）。默认只看今天，
    传 trade_date 可以查历史某一天的采样序列。
    """
    q = db.query(WeakToStrongSnapshot).filter(WeakToStrongSnapshot.stock_code == stock_code)
    q = q.filter(WeakToStrongSnapshot.trade_date == (trade_date or date.today()))
    return q.order_by(WeakToStrongSnapshot.timestamp.asc()).limit(min(limit, 2000)).all()


@router.get("/discovery-runs", response_model=list[DiscoveryRunResponse])
def list_discovery_runs(limit: int = 30, db: Session = Depends(get_db)):
    """候选池发现的Prompt解析监控日志，最近在前——用于人工核查召回数量是否异常。"""
    return (
        db.query(WeakToStrongDiscoveryRun)
        .order_by(WeakToStrongDiscoveryRun.timestamp.desc())
        .limit(min(limit, 200))
        .all()
    )


@router.get("/config", response_model=W2SConfigResponse)
def get_config(db: Session = Depends(get_db)):
    return cfg.get_all_config(db)


@router.put("/config", response_model=W2SConfigResponse)
def update_config(payload: W2SConfigUpdateRequest, db: Session = Depends(get_db), _: str = Depends(require_auth)):
    cfg.set_w2s_config(db, payload.key, payload.value)
    return cfg.get_all_config(db)
