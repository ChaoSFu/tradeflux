"""
backfill_daily_reviews.py
=========================
回填 DailyReview 中的历史赚钱效应字段：
  - strong_pool_avg_pct   : 当日强势股均涨幅 %
  - profit_effect_groups  : 四组（昨日涨停/震荡/走弱/破位龙头）的 avg_pct 及涨跌统计

数据来源：已存的 StockDailySnapshot 表（含 pct_change / is_limit_up / phase）。

用法：
  cd backend
  source .venv/bin/activate
  python -m scripts.backfill_daily_reviews           # 回填所有 NULL 行
  python -m scripts.backfill_daily_reviews --force   # 强制覆盖全部行
  python -m scripts.backfill_daily_reviews --date 2025-05-01  # 只回填指定日期
"""

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

# ── path bootstrap ────────────────────────────────────────────────────────────
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models.review import DailyReview
from app.models.stock import StockDailySnapshot


# ─────────────────────────────────────────────────────────────────────────────

GROUP_ORDER = ["limit_up", "oscillation", "weakening", "broken"]
GROUP_LABELS = {
    "limit_up":    "昨日涨停龙头",
    "oscillation": "昨日震荡龙头",
    "weakening":   "昨日走弱龙头",
    "broken":      "昨日破位龙头",
}


def _classify_p(p: float) -> str:
    """涨跌平分类（与 daily_update 保持一致）。"""
    return "up" if p > 0.5 else ("down" if p < -0.5 else "flat")


def backfill_date(db, target_date: date, prev_snaps: dict) -> Optional[dict]:
    """
    回填单日。
    prev_snaps: {stock_id: StockDailySnapshot} — 前一交易日的快照，用于分组。
    返回计算结果 dict，或 None（无数据）。
    """
    today_snaps = (
        db.query(StockDailySnapshot)
        .filter(StockDailySnapshot.date == target_date)
        .all()
    )

    # 只保留当天 phase 不为 null 的快照（代表该股当日在强势股池中）
    pool_snaps = [s for s in today_snaps if s.phase is not None]
    if not pool_snaps:
        return None

    # ── 整体均涨幅 ──────────────────────────────────────────────────────
    pcts = [s.pct_change for s in pool_snaps if s.pct_change is not None]
    strong_pool_avg_pct = round(sum(pcts) / len(pcts), 2) if pcts else None

    # ── 分组统计（依据前日快照分组）──────────────────────────────────────
    groups_data: dict[str, dict] = {
        k: {"pcts": [], "up": 0, "down": 0, "flat": 0} for k in GROUP_ORDER
    }

    for snap in pool_snaps:
        if snap.pct_change is None:
            continue
        p = snap.pct_change
        prev = prev_snaps.get(snap.stock_id)

        if prev and prev.is_limit_up:
            gk = "limit_up"
        elif prev and prev.phase == "broken":
            gk = "broken"
        elif prev and prev.phase == "weakening":
            gk = "weakening"
        else:
            # 前日无快照 或 phase = 'normal' → 震荡组
            gk = "oscillation"

        gd = groups_data[gk]
        gd["pcts"].append(p)
        cls = _classify_p(p)
        gd[cls] += 1

    profit_effect_groups = []
    for key in GROUP_ORDER:
        gd = groups_data[key]
        ps = gd["pcts"]
        profit_effect_groups.append({
            "key":         key,
            "label":       GROUP_LABELS[key],
            "stock_count": len(ps),
            "avg_pct":     round(sum(ps) / len(ps), 2) if ps else 0.0,
            "up_count":    gd["up"],
            "down_count":  gd["down"],
            "flat_count":  gd["flat"],
        })

    return {
        "strong_pool_avg_pct":  strong_pool_avg_pct,
        "profit_effect_groups": profit_effect_groups,
    }


def run(force: bool = False, only_date: Optional[date] = None):
    db = SessionLocal()
    try:
        # 确定要回填哪些 DailyReview 行
        q = db.query(DailyReview).order_by(DailyReview.date)
        if only_date:
            q = q.filter(DailyReview.date == only_date)
        elif not force:
            q = q.filter(DailyReview.strong_pool_avg_pct == None)  # noqa: E711

        reviews = q.all()
        if not reviews:
            print("没有需要回填的 DailyReview 行。")
            return

        print(f"共 {len(reviews)} 行需要回填...")

        # 预加载所有相关日期的 StockDailySnapshot，按日期分组
        min_date = reviews[0].date
        max_date = reviews[-1].date
        # 需要前一日快照，所以加载 [min_date-3days, max_date]
        load_from = min_date - timedelta(days=3)

        print(f"  加载 StockDailySnapshot [{load_from} → {max_date}]...")
        all_snaps = (
            db.query(StockDailySnapshot)
            .filter(
                StockDailySnapshot.date >= load_from,
                StockDailySnapshot.date <= max_date,
            )
            .all()
        )

        # 按日期索引
        snaps_by_date: dict[date, dict[int, StockDailySnapshot]] = defaultdict(dict)
        for s in all_snaps:
            snaps_by_date[s.date][s.stock_id] = s

        # 获取所有快照日期，排序后用于查找「前一交易日」
        all_snap_dates = sorted(snaps_by_date.keys())

        def prev_trading_day(d: date) -> Optional[date]:
            idx = all_snap_dates.index(d) if d in all_snap_dates else -1
            if idx > 0:
                return all_snap_dates[idx - 1]
            return None

        # 逐行回填
        updated = 0
        skipped = 0
        for review in reviews:
            d = review.date
            if d not in snaps_by_date:
                print(f"  [SKIP] {d} — StockDailySnapshot 无数据")
                skipped += 1
                continue

            prev_date = prev_trading_day(d)
            prev_snaps = snaps_by_date.get(prev_date, {}) if prev_date else {}

            result = backfill_date(db, d, prev_snaps)
            if result is None:
                print(f"  [SKIP] {d} — 强势股快照为空（phase 全 null）")
                skipped += 1
                continue

            review.strong_pool_avg_pct  = result["strong_pool_avg_pct"]
            review.profit_effect_groups = result["profit_effect_groups"]
            updated += 1

            groups_summary = ", ".join(
                f"{g['key']}:{g['avg_pct']:+.2f}%({g['stock_count']}只)"
                for g in result["profit_effect_groups"]
                if g["stock_count"] > 0
            )
            print(f"  [OK]   {d}  均涨幅={result['strong_pool_avg_pct']:+.2f}%  {groups_summary}")

        db.commit()
        print(f"\n完成：回填 {updated} 行，跳过 {skipped} 行。")

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填 DailyReview 历史赚钱效应字段")
    parser.add_argument("--force",  action="store_true", help="强制覆盖全部行（默认只回填 NULL 行）")
    parser.add_argument("--date",   type=str, default=None, help="只回填指定日期，格式 YYYY-MM-DD")
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else None
    run(force=args.force, only_date=target)
