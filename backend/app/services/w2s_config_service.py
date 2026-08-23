"""
弱转强雷达（Weak-to-Strong Radar）可编辑配置：两路候选 Prompt + 数值型阈值。
存于 app_config 表（跟 pool_config_service.py 完全同一套机制：字符串 KV，
数值型读时转换，未设置时回退代码内默认常量）——不新建配置表。
"""
from typing import Optional

from sqlalchemy.orm import Session

from ..models.app_config import AppConfig

# ── 两路候选 Prompt（东财智能选股 keyword）──────────────────────────────────
KEY_PROMPT1 = "w2s_premarket_prompt1"
KEY_PROMPT2 = "w2s_premarket_prompt2"

PROMPT1_DEFAULT = (
    "非ST；非退市；非新股非次新；"
    "近20个交易日有涨停或者近20日涨幅前20%；"
    "昨日下跌；昨日成交额大于3亿元。"
)
PROMPT2_DEFAULT = (
    "非ST；非退市；非新股非次新；"
    "近20日涨幅前20%；"
    "昨日收盘价跌破5日均线；昨日收盘价高于20日均线；"
    "昨日成交额大于3亿元。"
)

# ── 数值型阈值 ────────────────────────────────────────────────────────────
KEY_MIN_YDAY_AMOUNT = "w2s_min_yesterday_amount"          # 元，默认3亿
KEY_LEADER_GAP_THRESHOLD = "w2s_leader_gap_threshold"      # Core Leader Score 分差阈值
KEY_OBSERVATION_WINDOW_DAYS = "w2s_observation_window_days"  # 候选连续miss多少天后移出
KEY_DIVERGENCE_HEALTH_THRESHOLD = "w2s_divergence_health_threshold"  # phase=4 细分阈值
KEY_AUCTION_GAP_MIN = "w2s_auction_gap_min"                # 竞价Gap超预期阈值（%）
KEY_SPACE_MIN_ROOM_PCT = "w2s_space_min_room_pct"          # 涨停空间不足阈值（%），低于此值降级
KEY_PULLBACK_MIN_PCT = "w2s_pullback_min_pct"              # 有效回踩最小幅度（%），低于此值视为噪音不冻结H1
KEY_MAINLINE_SECTOR_TOP_N = "w2s_mainline_sector_top_n"    # 主升板块软上限：MAIN_UPTREND里按强度分取前N个才允许放行到BUYABLE
KEY_SECTOR_GATE_ALLOWED = "w2s_sector_gate_allowed"        # 逗号分隔的允许分类列表（NEW_START虽在列表里但被软上限限制到READY，见状态机）
KEY_REGULATORY_RISK_CAP = "w2s_regulatory_risk_cap"        # 达到此级别即BLOCK，逗号分隔
KEY_MARKET_GATE_BLOCKED = "w2s_market_gate_blocked"        # 大盘闸门达到此颜色即BLOCK，逗号分隔
KEY_FORMULA_VERSION = "w2s_formula_version"

NUMERIC_DEFAULTS: dict[str, float] = {
    KEY_MIN_YDAY_AMOUNT: 3.0e8,
    KEY_LEADER_GAP_THRESHOLD: 8.0,
    KEY_OBSERVATION_WINDOW_DAYS: 7,
    KEY_DIVERGENCE_HEALTH_THRESHOLD: 50.0,
    KEY_AUCTION_GAP_MIN: 3.0,
    KEY_SPACE_MIN_ROOM_PCT: 2.0,
    KEY_PULLBACK_MIN_PCT: 1.5,
    KEY_MAINLINE_SECTOR_TOP_N: 3,
}
STRING_DEFAULTS: dict[str, str] = {
    KEY_SECTOR_GATE_ALLOWED: "NEW_START,EXPANDING,MAIN_UPTREND,HEALTHY_DIVERGENCE",
    KEY_REGULATORY_RISK_CAP: "HIGH,EXTREME",
    KEY_MARKET_GATE_BLOCKED: "RED",
    KEY_FORMULA_VERSION: "w2s_radar_v0.6.0",
}
PROMPT_DEFAULTS = {KEY_PROMPT1: PROMPT1_DEFAULT, KEY_PROMPT2: PROMPT2_DEFAULT}

ALL_KEYS = list(PROMPT_DEFAULTS) + list(NUMERIC_DEFAULTS) + list(STRING_DEFAULTS)


def _get(db: Session, key: str) -> Optional[str]:
    row = db.query(AppConfig).filter(AppConfig.key == key).first()
    v = (row.value or "").strip() if row else ""
    return v or None


def set_w2s_config(db: Session, key: str, value: Optional[str]) -> None:
    """value 为 None/空 → 删除（回退默认）；否则 upsert。"""
    if key not in ALL_KEYS:
        raise ValueError(f"unknown w2s config key: {key}")
    row = db.query(AppConfig).filter(AppConfig.key == key).first()
    val = (value or "").strip()
    if not val:
        if row:
            db.delete(row)
    elif row:
        row.value = val
    else:
        db.add(AppConfig(key=key, value=val))
    db.commit()


def get_prompts(db: Session) -> dict:
    return {
        "prompt1": _get(db, KEY_PROMPT1) or PROMPT1_DEFAULT,
        "prompt2": _get(db, KEY_PROMPT2) or PROMPT2_DEFAULT,
        "is_prompt1_custom": _get(db, KEY_PROMPT1) is not None,
        "is_prompt2_custom": _get(db, KEY_PROMPT2) is not None,
        "default_prompt1": PROMPT1_DEFAULT,
        "default_prompt2": PROMPT2_DEFAULT,
    }


def get_numeric(db: Session, key: str) -> float:
    raw = _get(db, key)
    if raw is None:
        return NUMERIC_DEFAULTS[key]
    try:
        return float(raw)
    except ValueError:
        return NUMERIC_DEFAULTS[key]


def get_string(db: Session, key: str) -> str:
    return _get(db, key) or STRING_DEFAULTS[key]


def get_sector_gate_allowed(db: Session) -> set[str]:
    return {s.strip() for s in get_string(db, KEY_SECTOR_GATE_ALLOWED).split(",") if s.strip()}


def get_regulatory_risk_cap(db: Session) -> set[str]:
    return {s.strip() for s in get_string(db, KEY_REGULATORY_RISK_CAP).split(",") if s.strip()}


def get_market_gate_blocked(db: Session) -> set[str]:
    return {s.strip() for s in get_string(db, KEY_MARKET_GATE_BLOCKED).split(",") if s.strip()}


def get_all_config(db: Session) -> dict:
    """完整配置快照，供 GET /weak-to-strong-radar/config 用。"""
    out = get_prompts(db)
    for key in NUMERIC_DEFAULTS:
        out[key] = get_numeric(db, key)
    for key in STRING_DEFAULTS:
        out[key] = get_string(db, key)
    return out
