"""
东方财富板块数据全量同步脚本。
数据源来自 WAP 版行情页（https://wap.eastmoney.com/quote/bankuailist.html）。

板块分类（与 WAP 页面标签一一对应）：
    fs=m:90+e:3  概念板块（~399个）    sector_type="concept"   WAP"概念"标签
    fs=b:MK0881  行业板块（~457个）    sector_type="industry"  WAP"行业"标签全量
    fs=m:90+e:1  地区板块（~31个）     sector_type="region"    WAP"地区"标签

注意：
    - b:MK0881 是 WAP 行业全量（一/二/三级混合），比 m:90+s:4 (128) 更完整
    - m:90+e:4 风格板块（昨日连板等）为动态/噪声板块，跳过不同步
    - 新板块 is_watched=False（由用户在管理页手动开启）
    - 已有板块保留原有 is_watched 配置

用法：
    cd backend && .venv/bin/python scripts/sync_boards.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models.sector import Sector, StockSectorRelation
from app.models.stock import Stock


BASE_URL = "https://push2delay.eastmoney.com/api/qt"
HEADERS = [
    "-H", "Referer: https://quote.eastmoney.com/",
    "-H", (
        "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
]

# 各类板块的 fs 参数及对应的 sector_type
# b:MK0881 = WAP"行业"标签的全量来源，涵盖一/二/三级行业共457个
# m:90+e:3  = WAP"概念"标签（399个纯概念，不含风格噪声）
# m:90+e:1  = WAP"地区"标签（31个省级板块）
BOARD_TYPES = [
    ("m:90+e:3", "concept",  "概念板块"),
    ("b:MK0881", "industry", "行业板块"),
    ("m:90+e:1", "region",   "地区板块"),
]


# ---------------------------------------------------------------------------
# HTTP 工具（curl subprocess，规避 TLS 指纹）
# ---------------------------------------------------------------------------

def _curl_get(url: str, params: dict | None = None, retries: int = 2) -> dict:
    """用 curl 发起 GET 请求，返回 JSON dict。失败时重试。"""
    if params:
        qs = urllib.parse.urlencode(params)
        url = f"{url}?{qs}"

    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "20"] + HEADERS + [url],
                capture_output=True, text=True, timeout=25,
            )
            if result.stdout.strip():
                return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            pass
        if attempt < retries:
            time.sleep(1.0)

    return {}


# ---------------------------------------------------------------------------
# 数据拉取
# ---------------------------------------------------------------------------

def _fetch_boards_by_fs(fs_code: str, label: str) -> list[dict]:
    """
    拉取指定 fs 类型的全量板块列表。
    返回 list of {f12: BK码, f14: 名称, f3: 今日涨跌幅%,
                  f8: 换手率%, f20: 总市值(元), f6: 今日成交额(元),
                  f109: 近5日涨幅%, f110: 近10日涨幅%,
                  f160: 近20日涨幅%, f165: 近60日涨幅%}
    """
    all_boards: list[dict] = []
    page = 1
    total = None

    while True:
        data = _curl_get(f"{BASE_URL}/clist/get", {
            "pn": str(page), "pz": "100", "po": "1", "np": "1",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": fs_code,
            "fields": "f12,f14,f3,f8,f20,f6,f109,f110,f160,f165",
        })

        diff = (data.get("data") or {}).get("diff") or []
        if total is None:
            total = (data.get("data") or {}).get("total", 0)

        all_boards.extend(diff)
        print(f"  [{label}] 第{page}页: {len(diff)}条 (累计 {len(all_boards)}/{total})")

        if not diff or len(all_boards) >= (total or 0):
            break

        page += 1
        time.sleep(0.4)

    return all_boards


def _fetch_constituent_stocks(bk_code: str) -> list[tuple[str, int]]:
    """
    拉取板块成份股完整列表（分页，每页200）。
    返回 [(code_6位, market), ...] — market: 0=SZ, 1=SH
    """
    stocks: list[tuple[str, int]] = []
    page = 1
    total = None

    while True:
        data = _curl_get(f"{BASE_URL}/clist/get", {
            "pn": str(page), "pz": "200", "po": "1", "np": "1",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": f"b:{bk_code}",
            "fields": "f12,f13",
        })

        diff = (data.get("data") or {}).get("diff") or []
        if total is None:
            total = (data.get("data") or {}).get("total", 0)

        for item in diff:
            code = str(item.get("f12", "")).zfill(6)
            market = int(item.get("f13", 0))
            stocks.append((code, market))

        if not diff or len(stocks) >= (total or 0):
            break

        page += 1
        time.sleep(0.2)

    return stocks


# ---------------------------------------------------------------------------
# 数据库写入
# ---------------------------------------------------------------------------

def _upsert_board(db, board: dict, sector_type: str, stock_count: int) -> tuple["Sector", bool]:
    """
    更新或创建板块记录。
    返回 (sector, is_new)。

    is_watched 规则：
    - 新板块：False（需人工在管理页开启）
    - 已有板块：保留原有值（尊重人工配置）
    """
    bk_code = board["f12"]
    name = board["f14"]

    sector = db.query(Sector).filter(Sector.code == bk_code).first()
    is_new = sector is None
    if is_new:
        sector = Sector(code=bk_code)
        db.add(sector)

    sector.name = name
    sector.sector_type = sector_type
    sector.stock_count = stock_count
    sector.total_market_cap = round((board.get("f20") or 0) / 1e8, 2)   # 亿元
    sector.turnover_rate = round(board.get("f8") or 0, 4)               # %
    sector.amount = round((board.get("f6") or 0) / 1e8, 2)              # 亿元
    sector.pct_change_30d = round(board.get("f3") or 0, 2)              # 今日涨幅 %
    sector.pct_change_5d  = round(board.get("f109") or 0, 2)             # 近5日涨幅 %
    sector.pct_change_10d = round(board.get("f110") or 0, 2)            # 近10日涨幅 %
    sector.pct_change_20d = round(board.get("f160") or 0, 2)            # 近20日涨幅 %
    sector.pct_change_60d = round(board.get("f165") or 0, 2)            # 近60日涨幅 %
    if is_new:
        sector.is_watched = False  # 新板块默认不展示

    return sector, is_new


def _sync_board_relations(db, sector: "Sector",
                          constituents: list[tuple[str, int]]) -> int:
    """
    重建某板块的成份股关联。只关联已在 stocks 表中的股票。
    先删旧关联，再写新关联。返回实际写入的关联数。
    """
    db.query(StockSectorRelation).filter(
        StockSectorRelation.sector_id == sector.id
    ).delete()

    if not constituents:
        return 0

    codes = [c for c, _ in constituents]
    stocks_in_db = db.query(Stock).filter(Stock.code.in_(codes)).all()
    stock_map = {s.code: s for s in stocks_in_db}

    count = 0
    for code, _ in constituents:
        stock = stock_map.get(code)
        if not stock:
            continue
        rel = StockSectorRelation(
            stock_id=stock.id,
            sector_id=sector.id,
        )
        db.add(rel)
        count += 1

    return count


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_sync_boards() -> None:
    today = date.today().isoformat()
    print(f"\n{'='*60}")
    print(f"  TradeFlux 东财板块全量同步  [{today}]")
    print(f"{'='*60}")
    print("  数据源：WAP 版行情页分类体系")
    print("  概念(e:3) + 行业二级(s:4) + 行业三级(s:8) + 地区(e:1)")
    print("  新板块 is_watched=False，已有板块保留原有配置")

    db = SessionLocal()
    try:
        init_db()

        # ── 1. 拉取各类板块列表 ──────────────────────────────────────
        print("\n[第1步] 拉取东财全量板块列表...")
        all_boards: list[tuple[dict, str, str]] = []  # (board, sector_type, label)

        for fs_code, sector_type, label in BOARD_TYPES:
            print(f"  拉取 {label}（fs={fs_code}）...")
            boards = _fetch_boards_by_fs(fs_code, label)
            print(f"  {label}共 {len(boards)} 个")
            for b in boards:
                all_boards.append((b, sector_type, label))
            time.sleep(0.8)

        print(f"\n  合计 {len(all_boards)} 个板块待同步")
        type_counts = {}
        for _, st, _ in all_boards:
            type_counts[st] = type_counts.get(st, 0) + 1
        for st, cnt in type_counts.items():
            print(f"    {st}: {cnt} 个")

        # ── 2. 写入 sectors 表 & 同步成份股关联 ─────────────────────
        print(f"\n[第2步] 逐板块写入数据库并同步成份股关联...")
        mins_est = len(all_boards) * 0.4 / 60
        print(f"  （共 {len(all_boards)} 个板块，预计约 {mins_est:.0f} 分钟）")

        total_new = 0
        total_updated = 0
        total_relations = 0

        for i, (board, sector_type, label) in enumerate(all_boards):
            bk_code = board["f12"]
            name = board["f14"]

            # 拉取成份股
            constituents = _fetch_constituent_stocks(bk_code)
            stock_count = len(constituents)

            # upsert Sector
            sector, is_new = _upsert_board(db, board, sector_type, stock_count)
            db.flush()

            if is_new:
                total_new += 1
            else:
                total_updated += 1

            # 重建成份股关联
            n = _sync_board_relations(db, sector, constituents)
            total_relations += n

            watched_mark = "★" if sector.is_watched else " "
            print(f"  [{i+1:4d}/{len(all_boards)}] {watched_mark}{sector_type[:3]:3s} "
                  f"{bk_code} {name[:16]:16s} 成份={stock_count:4d} 关联={n:4d} "
                  f"{'[新]' if is_new else ''}")

            # 每50个板块提交一次
            if (i + 1) % 50 == 0:
                db.commit()
                print(f"  --- 中间提交 ({i+1}/{len(all_boards)}) ---")

            time.sleep(0.3)

        db.commit()

        # ── 3. 统计摘要 ────────────────────────────────────────────
        print(f"\n{'─'*60}")
        print(f"  新增板块: {total_new} 个，更新板块: {total_updated} 个")
        for fs_code, sector_type, label in BOARD_TYPES:
            total_cnt = db.query(Sector).filter(Sector.sector_type == sector_type).count()
            watched_cnt = (
                db.query(Sector)
                .filter(Sector.sector_type == sector_type, Sector.is_watched == True)  # noqa: E712
                .count()
            )
            rels = (
                db.query(StockSectorRelation)
                .join(Sector, StockSectorRelation.sector_id == Sector.id)
                .filter(Sector.sector_type == sector_type)
                .count()
            )
            print(f"  {label}({sector_type}): {total_cnt}个 展示{watched_cnt}个 关联{rels}条")
        print(f"{'─'*60}")
        print("\n✅ 东财板块全量同步完成\n")

    except Exception as e:
        db.rollback()
        print(f"\n❌ 同步失败: {e}")
        raise
    finally:
        db.close()
        from app.database import engine
        engine.dispose()


if __name__ == "__main__":
    run_sync_boards()
