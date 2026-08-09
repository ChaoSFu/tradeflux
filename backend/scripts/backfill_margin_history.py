"""
backfill_margin_history.py
===========================
一次性回填 market_breadth_daily 的两融余额/净买入/上证指数收盘/市盈率全部历史
（回溯至2010-03-31两融业务开办首日）。只写 margin_balance/margin_net_buy/
szzs_close/szzs_pe 这四个字段，不触碰同一行里涨跌统计/成交额等其他来源各自
独立写入的字段。

历史数据一旦落库不再变化，日常同步（daily_update -> sync_market_breadth）
只取最新几天，不需要每次都重新拉全部历史——这个脚本只需要跑一次（本地 + 部署
到服务器各跑一次），不进 daily_update 常规流程。

用法：
  cd backend
  source .venv/bin/activate
  python -m scripts.backfill_margin_history
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date as date_cls

from app.database import SessionLocal
from app.models.market_index import MarketBreadthDaily
from app.services.windvane_service import fetch_margin_history_full, fetch_szzs_pe_history


def run():
    print("拉取两融余额全部历史（RPTA_RZRQ_LSHJ，翻页）...")
    margin_rows = fetch_margin_history_full()
    if not margin_rows:
        print("两融历史拉取失败（分页不完整或接口不可用），中止。")
        return
    print(f"共 {len(margin_rows)} 条（{margin_rows[0]['date']} ~ {margin_rows[-1]['date']}）")

    start = margin_rows[0]["date"].replace("-", "")
    end = margin_rows[-1]["date"].replace("-", "")
    print(f"拉取上证指数收盘+市盈率（中证指数官网，{start} ~ {end}）...")
    pe_map = fetch_szzs_pe_history(start, end)
    if not pe_map:
        print("上证指数市盈率拉取失败，中止（避免只写一半数据）。")
        return
    print(f"共 {len(pe_map)} 条")

    db = SessionLocal()
    try:
        existing = {
            r.date: r
            for r in db.query(MarketBreadthDaily)
            .filter(MarketBreadthDaily.date.in_([
                date_cls.fromisoformat(r["date"]) for r in margin_rows
            ]))
            .all()
        }
        upserts = 0
        for r in margin_rows:
            d = date_cls.fromisoformat(r["date"])
            row = existing.get(d)
            if row is None:
                row = MarketBreadthDaily(date=d)
                db.add(row)
                existing[d] = row
            row.margin_balance = r["balance"]
            row.margin_net_buy = r["net_buy"]
            pe_row = pe_map.get(r["date"])
            if pe_row:
                if pe_row.get("close") is not None:
                    row.szzs_close = pe_row["close"]
                if pe_row.get("pe") is not None:
                    row.szzs_pe = pe_row["pe"]
            upserts += 1
            if upserts % 500 == 0:
                db.commit()
                print(f"  已写入 {upserts}/{len(margin_rows)} ...")
        db.commit()
        print(f"\n完成：{upserts} 条两融历史已落库（含上证收盘+市盈率）。")
    finally:
        db.close()


if __name__ == "__main__":
    run()
