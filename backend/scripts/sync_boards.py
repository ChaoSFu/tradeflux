"""
东方财富板块数据全量同步脚本。

流程：
    1. 拉取全量板块元数据（名称/涨幅/市值等）→ 更新 sectors 表
    2. 对 DB 中有快照的活跃股票，并发调 F10 接口获取其所属板块 BK 码
       → 增量更新 stock_sector_relations（只改变有变化的记录）

设计原则：
    - 板块 → 个股（反向遍历930个板块拉成分股）已废弃，成本过高
    - 改为个股 → 板块（每只股票查自己属于哪些板块），只维护实际跟踪的股票
    - 只关联 is_watched=True 的板块（前端可见），其余忽略
    - 关联未变化时跳过写入，避免无谓 DB 操作

用法：
    cd backend && .venv/bin/python scripts/sync_boards.py
    cd backend && .venv/bin/python scripts/sync_boards.py --stocks-only  # 只更新关联，跳过板块元数据
    cd backend && .venv/bin/python scripts/sync_boards.py --meta-only    # 只更新板块元数据
"""
import os
import sys
import time
import argparse
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models.sector import Sector, StockSectorRelation
from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import fetch_stock_bk_codes

BASE_URL = "https://push2delay.eastmoney.com/api/qt"

_HTTP_HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# 各类板块的 fs 参数及对应的 sector_type
BOARD_TYPES = [
    ("m:90+e:3", "concept",  "概念板块"),
    ("b:MK0881", "industry", "行业板块"),
    ("m:90+e:1", "region",   "地区板块"),
]

# 动态/噪声板块黑名单（跳过不同步）
DYNAMIC_BOARD_KEYWORDS = {
    "最近多板", "昨日连板", "昨日涨停", "昨日跌停",
    "今日连板", "今日涨停", "今日跌停",
    "东方财富热股", "东方财富概念", "近期强势", "近期涨停",
    "连续上涨", "连续下跌", "持续缩量", "持续放量",
}


# ---------------------------------------------------------------------------
# 第1步：板块元数据同步（httpx，替换原 curl subprocess）
# ---------------------------------------------------------------------------

def _fetch_boards_by_fs(fs_code: str, label: str) -> list[dict]:
    """拉取指定 fs 类型的全量板块列表。"""
    all_boards: list[dict] = []
    page = 1
    total = None

    while True:
        params = {
            "pn": str(page), "pz": "100", "po": "1", "np": "1",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": fs_code,
            "fields": "f12,f14,f3,f8,f20,f6,f109,f110,f160,f165",
        }
        # 最多重试 3 次，每次间隔递增
        data = None
        for attempt in range(3):
            try:
                resp = httpx.get(
                    f"{BASE_URL}/clist/get",
                    params=params,
                    headers=_HTTP_HEADERS,
                    timeout=30,
                )
                data = resp.json()
                break
            except Exception as e:
                wait = (attempt + 1) * 3
                print(f"  [{label}] 第{page}页请求失败（第{attempt+1}次）: {e}，{wait}s 后重试...")
                time.sleep(wait)
        if data is None:
            print(f"  [{label}] 第{page}页重试3次均失败，跳过此页")
            break

        diff = (data.get("data") or {}).get("diff") or []
        if total is None:
            total = (data.get("data") or {}).get("total", 0)

        all_boards.extend(diff)
        print(f"  [{label}] 第{page}页: {len(diff)}条 (累计 {len(all_boards)}/{total})")

        if not diff or len(all_boards) >= (total or 0):
            break

        page += 1
        time.sleep(0.3)

    return all_boards


def _fetch_board_stock_count(bk_code: str) -> int:
    """
    调用东财 clist API 获取指定板块的成份股总数。
    fs=b:{bk_code} 时 data.total 即为该板块成份股数量。
    只取第1页1条数据（pz=1），仅用 total 字段，轻量快速。
    失败返回 -1（调用方据此决定是否跳过更新）。
    """
    try:
        resp = httpx.get(
            f"{BASE_URL}/clist/get",
            params={
                "pn": "1", "pz": "1", "np": "1",
                "fltt": "2", "invt": "2", "fid": "f3",
                "fs": f"b:{bk_code}",
                "fields": "f12",
            },
            headers=_HTTP_HEADERS,
            timeout=10,
        )
        total = (resp.json().get("data") or {}).get("total")
        return int(total) if total is not None else -1
    except Exception:
        return -1


def _upsert_board(db, board: dict, sector_type: str) -> tuple["Sector | None", bool]:
    """更新或创建板块元数据记录。name 为空时跳过（API 偶发返回无名称的板块）。"""
    bk_code = board["f12"]
    name = board.get("f14") or None
    if not name:
        return None, False  # 无名称，跳过

    sector = db.query(Sector).filter(Sector.code == bk_code).first()
    is_new = sector is None
    if is_new:
        # 创建时即带上 name/sector_type，避免 flush 时触发 NOT NULL 约束
        sector = Sector(code=bk_code, name=name, sector_type=sector_type, is_watched=False)
        db.add(sector)
        db.flush()  # 立即写入 session，避免同一 code 重复出现时查询不到

    sector.name = name
    sector.sector_type = sector_type
    sector.total_market_cap = round((board.get("f20") or 0) / 1e8, 2)
    sector.turnover_rate    = round(board.get("f8") or 0, 4)
    sector.amount           = round((board.get("f6") or 0) / 1e8, 2)
    sector.pct_change_30d   = round(board.get("f3") or 0, 2)
    sector.pct_change_5d    = round(board.get("f109") or 0, 2)
    sector.pct_change_10d   = round(board.get("f110") or 0, 2)
    sector.pct_change_20d   = round(board.get("f160") or 0, 2)
    sector.pct_change_60d   = round(board.get("f165") or 0, 2)

    return sector, is_new


def sync_board_metadata(db, update_stock_count: bool = True) -> tuple[int, int]:
    """
    第1步：同步全量板块元数据。
    update_stock_count=True：额外并发拉取各板块成份股数量（全量同步时使用，约+15s）。
    update_stock_count=False：仅更新涨跌幅/换手/市值等，跳过成份股数量（meta_only 模式）。
    返回 (新增数, 更新数)
    """
    print("\n[第1步] 拉取东财全量板块元数据...")
    all_boards: list[tuple[dict, str]] = []

    for fs_code, sector_type, label in BOARD_TYPES:
        boards = _fetch_boards_by_fs(fs_code, label)
        print(f"  {label}共 {len(boards)} 个")
        for b in boards:
            all_boards.append((b, sector_type))
        time.sleep(0.5)

    new_count = updated_count = 0
    for board, sector_type in all_boards:
        name = board.get("f14") or ""
        if not name or any(kw in name for kw in DYNAMIC_BOARD_KEYWORDS):
            continue
        sector, is_new = _upsert_board(db, board, sector_type)
        if sector is None:
            continue  # _upsert_board 内部已过滤无名称板块
        if is_new:
            new_count += 1
        else:
            updated_count += 1

    db.commit()
    print(f"  板块元数据同步完成: 新增 {new_count} 个，更新 {updated_count} 个")

    if not update_stock_count:
        return new_count, updated_count

    # ── 并发拉取各板块成份股数量，更新 stock_count ──────────────────────────
    print("\n[第1步-补充] 并发拉取各板块成份股数量...")
    t0 = time.time()
    all_sectors = db.query(Sector).filter(Sector.code.isnot(None)).all()
    code_to_sector = {s.code: s for s in all_sectors}

    ok_count = skip_count = 0

    def _fetch_one(code: str) -> tuple[str, int]:
        return code, _fetch_board_stock_count(code)

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(_fetch_one, code): code for code in code_to_sector}
        for i, future in enumerate(as_completed(futures), 1):
            code, total = future.result()
            if total >= 0:  # -1 表示失败，保持原值
                code_to_sector[code].stock_count = total
                ok_count += 1
            else:
                skip_count += 1
            if i % 100 == 0:
                print(f"    进度: {i}/{len(futures)}")

    db.commit()
    elapsed = time.time() - t0
    print(f"  成份股数量更新完成: 成功 {ok_count} 个，失败保持原值 {skip_count} 个，耗时 {elapsed:.1f}s")

    return new_count, updated_count


# ---------------------------------------------------------------------------
# 第2步：个股→板块关联同步（F10 接口，增量更新）
# ---------------------------------------------------------------------------

def sync_stock_sector_relations(db, max_workers: int = 10) -> dict:
    """
    第2步：以个股为中心，并发查询 F10 接口，增量更新 stock_sector_relations。

    只处理 DB 中有快照记录的活跃股票（candidates 池），
    只关联 is_watched=True 的板块。
    有变化才写入，无变化跳过。
    """
    from sqlalchemy import func as sqlfunc, or_

    # ── 目标股票：当日涨跌停 + 当日强势股池 ──────────────────────────────
    latest_date = db.query(sqlfunc.max(StockDailySnapshot.date)).scalar()
    if not latest_date:
        print("\n[第2步] 无快照数据，跳过")
        return {"stocks_processed": 0, "skipped": 0, "relations_added": 0,
                "relations_removed": 0, "fetch_failed": 0}

    target_stock_ids = {
        row[0]
        for row in db.query(StockDailySnapshot.stock_id)
        .filter(
            StockDailySnapshot.date == latest_date,
            or_(
                StockDailySnapshot.is_limit_up == True,    # noqa
                StockDailySnapshot.is_limit_down == True,  # noqa
            ),
        )
        .all()
    }
    # 加上强势股池
    target_stock_ids |= {
        row[0]
        for row in db.query(Stock.id).filter(Stock.in_strong_pool == True).all()  # noqa
    }

    stocks = db.query(Stock).filter(Stock.id.in_(target_stock_ids)).all()
    print(f"\n[第2步] 同步个股→板块关联")
    print(f"  最新日期: {latest_date}")
    print(f"  目标股票: {len(stocks)} 只（当日涨跌停 + 强势股池）")

    # ── watched 板块的 code→Sector 映射 ──────────────────────────────────
    sector_map: dict[str, Sector] = {
        s.code: s
        for s in db.query(Sector).filter(Sector.is_watched == True).all()  # noqa
    }
    print(f"  关联范围: is_watched 板块共 {len(sector_map)} 个")

    # ── 读取当前 DB 里的关联（{stock_id: set(sector_id)}）──────────────
    existing_rels = (
        db.query(StockSectorRelation)
        .filter(StockSectorRelation.stock_id.in_(target_stock_ids))
        .all()
    )
    current_map: dict[int, set[int]] = defaultdict(set)
    for rel in existing_rels:
        current_map[rel.stock_id].add(rel.sector_id)

    # ── 并发拉取 F10 ──────────────────────────────────────────────────────
    print(f"  并发拉取 F10（{max_workers} workers）...")
    t0 = time.time()

    fetch_results: list[tuple[Stock, list[str]]] = []
    failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_stock_bk_codes, s.code): s
            for s in stocks
        }
        for future in as_completed(futures):
            stock = futures[future]
            try:
                bk_codes = future.result()
                fetch_results.append((stock, bk_codes))
            except Exception:
                failed += 1
                fetch_results.append((stock, []))

    elapsed = time.time() - t0
    print(f"  F10 拉取完成: {len(fetch_results)-failed}/{len(stocks)} 只，耗时 {elapsed:.1f}s")

    # ── 增量对比 & 写入 ───────────────────────────────────────────────────
    added = removed = skipped = 0

    for stock, bk_codes in fetch_results:
        # 新的 sector_id 集合（只保留 watched 板块）
        new_ids: set[int] = {
            sector_map[code].id
            for code in bk_codes
            if code in sector_map
        }
        old_ids = current_map.get(stock.id, set())

        if new_ids == old_ids:
            skipped += 1
            continue

        # 只删增量变化部分
        to_add = new_ids - old_ids
        to_del = old_ids - new_ids

        if to_del:
            db.query(StockSectorRelation).filter(
                StockSectorRelation.stock_id == stock.id,
                StockSectorRelation.sector_id.in_(to_del),
            ).delete(synchronize_session=False)
            removed += len(to_del)

        for sid in to_add:
            db.add(StockSectorRelation(stock_id=stock.id, sector_id=sid))
            added += 1

    db.commit()

    # 注意：stock_count 由 sync_board_metadata 通过东财 API(fs=b:{code}) 获取真实成份股数
    # 不在此处用 stock_sector_relations 覆盖，避免用部分关联数（仅强势股+涨跌停）污染全量数据

    return {
        "stocks_processed": len(stocks),
        "skipped": skipped,
        "relations_added": added,
        "relations_removed": removed,
        "fetch_failed": failed,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_sync_boards(
    meta_only: bool = False,
    stocks_only: bool = False,
    write_log: bool = True,   # 命令行直接运行时自己写日志；admin 调用时由 admin 写
) -> None:
    from datetime import datetime
    today = date.today().isoformat()

    mode_label = "板块行情同步（meta_only）" if meta_only else "板块全量同步（full）"
    print(f"\n{'='*60}")
    print(f"  TradeFlux {mode_label}  [{today}]  启动于 {datetime.now().strftime('%H:%M:%S')}")
    if meta_only:
        print(f"  模式：仅更新涨跌幅/换手率/市值等行情数据，跳过成份股数量和个股关联")
    else:
        print(f"  模式：全量同步，含成份股数量（并发拉取）+ 个股→板块关联（F10）")
    print(f"{'='*60}")

    db = SessionLocal()
    try:
        init_db()
        t_start = time.time()
        steps = []
        output_lines: list[str] = []

        if not stocks_only:
            t0 = time.time()
            print(f"\n[步骤1] 板块元数据同步（update_stock_count={'否' if meta_only else '是'}）...")
            sync_board_metadata(db, update_stock_count=not meta_only)
            elapsed = time.time() - t0
            print(f"  ✅ 板块元数据完成，耗时 {elapsed:.1f}s")
            steps.append(("板块元数据", elapsed))

        if not meta_only:
            t0 = time.time()
            print(f"\n[步骤2] 个股→板块关联同步（F10）...")
            stats = sync_stock_sector_relations(db)
            t1 = time.time() - t0
            print(f"  ✅ 关联同步完成，耗时 {t1:.1f}s")
            steps.append(("个股关联同步", t1))
            print(f"\n  关联同步结果:")
            print(f"    处理股票:   {stats['stocks_processed']} 只")
            print(f"    无变化跳过: {stats['skipped']} 只")
            print(f"    新增关联:   {stats['relations_added']} 条")
            print(f"    移除关联:   {stats['relations_removed']} 条")
            if stats['fetch_failed']:
                print(f"    F10 失败:   {stats['fetch_failed']} 只")

        total = time.time() - t_start
        print(f"\n{'─'*60}")
        print(f"  {'步骤':<16} {'耗时':>8}")
        print(f"  {'─'*26}")
        for name, t in steps:
            print(f"  {name:<16} {t:>7.1f}s")
        print(f"  {'─'*26}")
        print(f"  {'总耗时':<16} {total:>7.1f}s  完成于 {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'─'*60}")
        print("\n✅ 板块同步完成\n")

    except Exception as e:
        db.rollback()
        print(f"\n❌ 同步失败: {e}")
        import traceback; traceback.print_exc()
        raise
    finally:
        db.close()
        from app.database import engine
        engine.dispose()


if __name__ == "__main__":
    import io, sys as _sys

    parser = argparse.ArgumentParser(description="TradeFlux 板块同步")
    parser.add_argument("--meta-only",   action="store_true", help="只同步板块元数据，跳过个股关联")
    parser.add_argument("--stocks-only", action="store_true", help="只同步个股关联，跳过板块元数据")
    args = parser.parse_args()

    # 命令行运行时：同时写日志文件
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"sync_boards_{date.today().isoformat()}.log")

    class _Tee:
        def __init__(self, *files): self.files = files
        def write(self, s): [f.write(s) for f in self.files]
        def flush(self): [f.flush() for f in self.files]

    with open(log_path, "a", encoding="utf-8") as lf:
        orig = _sys.stdout
        _sys.stdout = _Tee(orig, lf)
        try:
            run_sync_boards(meta_only=args.meta_only, stocks_only=args.stocks_only)
        finally:
            _sys.stdout = orig
    print(f"[日志已写入 {log_path}]")
