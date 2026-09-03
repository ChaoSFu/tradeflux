"""
可编辑的选股 API prompt（强势池 / 涨跌停池）配置读写。
存于 app_config 表；未设置时回退代码内默认常量。
"""
from typing import Optional

from sqlalchemy.orm import Session

from ..models.app_config import AppConfig
from ..services.eastmoney_fetcher import (
    STRONG_POOL_KEYWORD, LIMIT_MOVE_KEYWORD, TURNOVER_POOL_KEYWORD,
    LEGACY_STRONG_POOL_KEYWORDS,
)

KEY_STRONG = "strong_pool_keyword"
KEY_LIMIT = "limit_move_keyword"
KEY_TURNOVER = "turnover_pool_keyword"

DEFAULTS = {
    KEY_STRONG: STRONG_POOL_KEYWORD,
    KEY_LIMIT: LIMIT_MOVE_KEYWORD,
    KEY_TURNOVER: TURNOVER_POOL_KEYWORD,
}


def _get(db: Session, key: str) -> Optional[str]:
    row = db.query(AppConfig).filter(AppConfig.key == key).first()
    v = (row.value or "").strip() if row else ""
    return v or None


def get_pool_keywords(db: Session) -> dict:
    """返回当前生效 prompt + 默认值（供 daily_update / 界面用）。"""
    strong = _get(db, KEY_STRONG) or DEFAULTS[KEY_STRONG]
    limit = _get(db, KEY_LIMIT) or DEFAULTS[KEY_LIMIT]
    turnover = _get(db, KEY_TURNOVER) or DEFAULTS[KEY_TURNOVER]
    return {
        "strong_pool_keyword": strong,
        "limit_move_keyword": limit,
        "turnover_pool_keyword": turnover,
        "is_strong_custom": _get(db, KEY_STRONG) is not None,
        "is_limit_custom": _get(db, KEY_LIMIT) is not None,
        "is_turnover_custom": _get(db, KEY_TURNOVER) is not None,
        "default_strong_pool_keyword": DEFAULTS[KEY_STRONG],
        "default_limit_move_keyword": DEFAULTS[KEY_LIMIT],
        "default_turnover_pool_keyword": DEFAULTS[KEY_TURNOVER],
    }


def set_pool_keyword(db: Session, key: str, value: Optional[str]) -> None:
    """value 为 None/空 → 删除（回退默认）；否则 upsert。"""
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


# ─── 强势池 prompt 收窄迁移（2026-09-03）────────────────────────────────────

KEY_STRONG_MIGRATED_FROM = "strong_pool_keyword_migrated_from"


def _norm(v: str) -> str:
    """比对用的规范化：去空白、统一中英文分号，避免因排版差异误判成自定义。"""
    return (v or "").replace(" ", "").replace("\n", "").replace("；", ";").strip()


def migrate_strong_pool_keyword(db: Session) -> dict:
    """
    把旧的强势池 prompt 迁移成收窄后的新默认值。**幂等，可重复执行。**

    ## 为什么需要显式迁移

    本模块的存储约定是「**没自定义时 app_config 里根本没有行**」，所以改代码常量
    对未自定义的用户自动生效、对自定义用户永不覆盖——安全迁移本来是架构天然满足的，
    不需要任何代码。

    但生产上这份 prompt 是**自定义**的（值等于旧默认值去掉"主板"两字），照上面的
    规则它永远不会被更新，收窄就完全不生效。所以要一次显式的、可审计的覆盖。

    ## 只覆盖认得出的旧值

    比对 LEGACY_STRONG_POOL_KEYWORDS 里那几个已知旧默认变体（规范化后比对，
    避免因空格/中英文分号排版差异把它误判成自定义）：

      · 命中   → 覆盖成新默认，并把原值写进 strong_pool_keyword_migrated_from
      · 不命中 → **不动**，返回 skipped 让调用方如实报出「检测到未知自定义 prompt」

    留原值是为了可回滚。直接覆盖一个用户配置而不留痕，出了问题没法还原。

    返回 {'action': 'migrated'|'already'|'skipped'|'noop', 'old': 原值或None}
    """
    cur = _get(db, KEY_STRONG)
    if cur is None:
        # 没有自定义行 → 直接吃代码里的新默认值，无需任何操作
        return {"action": "noop", "old": None}
    if _norm(cur) == _norm(STRONG_POOL_KEYWORD):
        return {"action": "already", "old": cur}
    if _norm(cur) not in {_norm(k) for k in LEGACY_STRONG_POOL_KEYWORDS}:
        return {"action": "skipped", "old": cur}

    set_pool_keyword(db, KEY_STRONG, STRONG_POOL_KEYWORD)
    row = db.query(AppConfig).filter(AppConfig.key == KEY_STRONG_MIGRATED_FROM).first()
    if row is None:
        db.add(AppConfig(key=KEY_STRONG_MIGRATED_FROM, value=cur))
    else:
        row.value = cur          # 重复执行时保留最近一次被覆盖的原值
    db.commit()
    return {"action": "migrated", "old": cur}
