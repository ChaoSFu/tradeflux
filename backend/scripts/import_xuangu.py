"""
从东方财富选股结果导入股票到强势池。

用法：
    cd backend && .venv/bin/python scripts/import_xuangu.py --xcid xc119f19616e010050c8

流程：
    1. 用 Playwright 拦截 xuangu API，获取选股结果（支持分页）
    2. 对每只股票：
       - 已在 stocks 表 → 直接设 in_strong_pool=True
       - 不在表中 → 先从新浪补全基础信息，再入库
    3. 补全 stock_sector_relations（已有板块映射时）
    4. 输出统计摘要
"""
import sys, os, json, time, random, string, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
from datetime import date
import httpx
from playwright.sync_api import sync_playwright

from app.database import SessionLocal, init_db
from app.models.stock import Stock
from app.models.sector import Sector, StockSectorRelation


# ---------------------------------------------------------------------------
# Playwright: 拦截 API 并获取完整股票列表
# ---------------------------------------------------------------------------

def fetch_xuangu_stocks(xc_id: str) -> list[dict]:
    """
    打开东方财富选股页面，拦截 search-code API，
    返回全部分页股票记录 [{code, name, industry, ...}, ...]
    """
    URL = f"https://xuangu.eastmoney.com/Result?id={xc_id}&a=edit_way"
    captured = {}

    def on_request(request):
        if "smart-tag/stock/v3/pw/search-code" in request.url and not captured:
            captured["url"] = request.url
            captured["headers"] = dict(request.headers)
            captured["body"] = request.post_data
            captured["cookies_ctx"] = None  # will set after

    print(f"  打开页面: {URL}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = ctx.new_page()
        page.on("request", on_request)
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)
        cookies = ctx.cookies()
        browser.close()

    if not captured:
        raise RuntimeError("未能捕获到选股 API 请求，请确认 xcId 正确")

    cookies_dict = {c["name"]: c["value"] for c in cookies}
    base_body = json.loads(captured["body"])
    headers = {
        "referer": "https://xuangu.eastmoney.com/",
        "content-type": "application/json",
        "accept": "application/json, text/plain, */*",
        "user-agent": captured["headers"].get("user-agent", ""),
        "actionmode": "edit_way",
        "curpage": "stockResult",
        "jumpsource": "edit_way",
        "origin": "https://xuangu.eastmoney.com",
    }

    def fetch_page(page_no):
        ts = int(time.time() * 1_000_000)
        rid = "".join(random.choices(string.ascii_letters, k=32)) + str(int(time.time() * 1000))
        body = {**base_body, "pageNo": page_no, "pageSize": 50,
                "timestamp": str(ts), "requestId": rid}
        r = httpx.post(
            "https://np-tjxg-g.eastmoney.com/api/smart-tag/stock/v3/pw/search-code",
            headers=headers, cookies=cookies_dict, json=body, timeout=15,
        )
        return r.json()

    all_stocks = []
    page_no = 1
    total = None

    while True:
        print(f"  拉取第 {page_no} 页...")
        resp = fetch_page(page_no)
        result = resp.get("data", {}).get("result", {})
        data_list = result.get("dataList", [])

        if total is None:
            total = result.get("total", len(data_list))
            print(f"  API 总计: {total} 只")

        for item in data_list:
            code = item.get("SECURITY_CODE", "").strip()
            if code:
                all_stocks.append({
                    "code": code,
                    "name": item.get("SECURITY_NAME_ABBR", "") or item.get("SECURITY_NAME", ""),
                    "industry": item.get("INDUSTRY", ""),
                })

        if not data_list or len(all_stocks) >= total:
            break
        page_no += 1
        time.sleep(0.3)

    return all_stocks


# ---------------------------------------------------------------------------
# 新浪 API：补全股票名称（对 Playwright 拿到的空名称）
# ---------------------------------------------------------------------------

def _fill_names_from_sina(stocks: list[dict]) -> list[dict]:
    """对 name 为空的股票，批量从新浪补全名称"""
    missing = [s for s in stocks if not s["name"]]
    if not missing:
        return stocks

    # 按市场判断前缀
    def prefix(code):
        return "sh" if code.startswith(("6",)) else "sz"

    batch = [f"{prefix(s['code'])}{s['code']}" for s in missing]
    url = "https://hq.sinajs.cn/list=" + ",".join(batch)
    headers = {"Referer": "https://finance.sina.com.cn/",
               "User-Agent": "Mozilla/5.0 Chrome/124.0"}
    try:
        r = httpx.get(url, headers=headers, timeout=10)
        name_map = {}
        for line in r.text.splitlines():
            m = re.match(r'var hq_str_[a-z]{2}(\d{6})="([^,]+)', line)
            if m:
                name_map[m.group(1)] = m.group(2)
        for s in missing:
            s["name"] = name_map.get(s["code"], s["code"])
    except Exception as e:
        print(f"  [warn] 新浪补全名称失败: {e}")

    return stocks


# ---------------------------------------------------------------------------
# DB 写入
# ---------------------------------------------------------------------------

def import_to_strong_pool(stocks: list[dict], db) -> tuple[int, int]:
    """
    批量导入到强势池。
    返回 (新增数, 更新数)
    """
    added = updated = 0
    for item in stocks:
        code = item["code"]
        market_str = "SH" if code.startswith("6") else "SZ"
        is_st = "ST" in item.get("name", "")

        stock = db.query(Stock).filter(Stock.code == code).first()
        if not stock:
            stock = Stock(
                code=code,
                name=item["name"] or code,
                market=market_str,
                is_st=is_st,
                in_strong_pool=True,
            )
            db.add(stock)
            added += 1
        else:
            if item["name"]:
                stock.name = item["name"]
            stock.in_strong_pool = True
            updated += 1

    db.flush()

    # 补全 sector relations（利用已有映射）
    for item in stocks:
        code = item["code"]
        stock = db.query(Stock).filter(Stock.code == code).first()
        if not stock:
            continue
        existing_rel = db.query(StockSectorRelation).filter(
            StockSectorRelation.stock_id == stock.id
        ).first()
        if not existing_rel:
            pass  # 无板块关联，暂不处理

    return added, updated


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="从东方财富选股导入强势池")
    parser.add_argument("--xcid", default="xc119f19616e010050c8", help="东方财富选股 ID")
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print(f"  导入东方财富选股结果 → 强势池")
    print(f"  xcId: {args.xcid}")
    print(f"{'='*55}")

    db = SessionLocal()
    try:
        init_db()

        # 1. 抓取选股结果
        print("\n[第1步] 通过 Playwright 拦截选股 API...")
        stocks = fetch_xuangu_stocks(args.xcid)
        print(f"  原始获取: {len(stocks)} 只")

        # 2. 补全名称
        print("\n[第2步] 补全股票名称...")
        stocks = _fill_names_from_sina(stocks)
        missing_names = sum(1 for s in stocks if not s["name"])
        if missing_names:
            print(f"  仍有 {missing_names} 只名称缺失（将以代码代替）")

        # 3. 导入到强势池
        print(f"\n[第3步] 写入强势池...")
        added, updated = import_to_strong_pool(stocks, db)
        db.commit()
        print(f"  新增: {added} 只，更新: {updated} 只")

        # 4. 统计
        total_pool = db.query(Stock).filter(Stock.in_strong_pool == True).count()  # noqa

        print(f"\n{'─'*55}")
        print(f"  本次导入: {len(stocks)} 只")
        print(f"  当前强势池总计: {total_pool} 只")
        print(f"{'─'*55}")
        print("\n  代码列表:")
        codes = [s["code"] for s in stocks]
        for i in range(0, len(codes), 10):
            print("  ", " ".join(codes[i:i+10]))
        print("\n✅ 导入完成\n")
        print("  提示：运行 daily_update.py 后将补全 K 线指标和板块关联。")

    except Exception as e:
        db.rollback()
        print(f"\n❌ 失败: {e}")
        import traceback; traceback.print_exc()
        raise
    finally:
        db.close()
        from app.database import engine
        engine.dispose()


if __name__ == "__main__":
    main()
