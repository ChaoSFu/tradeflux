"""
可编辑的选股 API prompt（强势池 / 涨跌停池）配置读写。
存于 app_config 表；未设置时回退代码内默认常量。
"""
from typing import Optional

from sqlalchemy.orm import Session

from ..models.app_config import AppConfig
from ..services.eastmoney_fetcher import STRONG_POOL_KEYWORD, LIMIT_MOVE_KEYWORD

KEY_STRONG = "strong_pool_keyword"
KEY_LIMIT = "limit_move_keyword"

DEFAULTS = {
    KEY_STRONG: STRONG_POOL_KEYWORD,
    KEY_LIMIT: LIMIT_MOVE_KEYWORD,
}


def _get(db: Session, key: str) -> Optional[str]:
    row = db.query(AppConfig).filter(AppConfig.key == key).first()
    v = (row.value or "").strip() if row else ""
    return v or None


def get_pool_keywords(db: Session) -> dict:
    """返回当前生效 prompt + 默认值（供 daily_update / 界面用）。"""
    strong = _get(db, KEY_STRONG) or DEFAULTS[KEY_STRONG]
    limit = _get(db, KEY_LIMIT) or DEFAULTS[KEY_LIMIT]
    return {
        "strong_pool_keyword": strong,
        "limit_move_keyword": limit,
        "is_strong_custom": _get(db, KEY_STRONG) is not None,
        "is_limit_custom": _get(db, KEY_LIMIT) is not None,
        "default_strong_pool_keyword": DEFAULTS[KEY_STRONG],
        "default_limit_move_keyword": DEFAULTS[KEY_LIMIT],
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
