#!/usr/bin/env python3
"""
一次性数据修正（2026-08-27）。默认只报告，加 --apply 才写库。

修两类**已知成因**的脏数据，都是当天查出来的 bug 留下的存量：

1) 北交所股票的 Stock.market 被标成 SZ
   成因：各处写死 `"SH" if market == 1 else "SZ"`，没有 BJ 这个值（东财 secid 里
   北交所也用 market=0，跟深一样）。取数链路不受影响——腾讯/新浪/东财三条路都先
   `_is_bj_code(code)` 按代码前缀短路。咬人的是拿它去拼别的东西（920895 被拼成
   `920895.SZ` 发给 fuyao 直接 Unknown thscode）和展示（前端交易所显示错误）。

2) 历史快照的收盘价跟 fuyao dump 对不上
   成因是"盘中写入、收盘后没人结算"：一只票盘中进候选池被写下当时的现价，收盘前
   又掉出池子，收盘那一跑的候选名单里没有它，那行盘中价就永久留下了。2026-08-26
   生产上 229 条快照里有 45 条是这样——600984 库里记成 +9.92% 涨停，实际收盘
   -5.67% 是炸板大阴线，那个假涨停会直接进涨停板块雷达的连板数和龙头分。

   **判据必须看内容，不能看 is_settled 标志**：那一列是 2026-08-26 才加的，
   DEFAULT FALSE，所以加列之前的所有历史行天然都是 False——那只表示"来路不明"，
   不是"已知的盘中值"。本脚本第一版就是拿标志判的，在本地库上报出 90191 条假阳性。
   现在改成拿 dump 的收盘价逐行对，对不上的才算脏。

   一个已知的口径差：dump 是未复权，库里历史是腾讯前复权的拼盘。窗口内有除权的
   股票也会显示为不一致，被"修"成未复权价——对涨停判定而言那反而更正确
   （涨停价按当日实际成交价算），但要知道有这回事。

用法：
    cd /opt/code/tradeflux/backend && source .venv/bin/activate
    python scripts/fix_bad_data.py            # 只看，不改
    python scripts/fix_bad_data.py --apply    # 真改
    deactivate
"""
import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import _is_bj_code, market_label
from app.services.fuyao_dump import daily_k_dump, get_api_key, load_bars


def fix_bj_market(db, apply: bool) -> int:
    rows = db.query(Stock).all()
    bad = [s for s in rows if market_label(s.code, 1 if s.market == "SH" else 0) != s.market]
    print(f"\n【1】北交所交易所标签  —— 共 {len(rows)} 只股票，标错 {len(bad)} 只")
    for s in bad[:20]:
        print(f"    {s.code} {s.name:<10} {s.market} → {market_label(s.code, 0)}")
    if len(bad) > 20:
        print(f"    …… 另有 {len(bad) - 20} 只")
    if bad and apply:
        for s in bad:
            s.market = market_label(s.code, 1 if s.market == "SH" else 0)
        db.commit()
        print(f"    ✅ 已修正 {len(bad)} 只")
    return len(bad)


def fix_stale_history(db, apply: bool, tol: float = 0.005) -> int:
    """拿 dump 逐行对收盘价，对不上的用 dump 覆盖。当日那一行不碰——它归主流程。"""
    today = date.today()
    key = get_api_key()
    print(f"\n【2】历史快照收盘价与 dump 比对")
    if not key:
        print("    ⚠️  未配置 FUYAO_API_KEY，无法比对，本项跳过")
        return 0

    codes = {r[0]: (r[1], bool(r[2])) for r in db.query(Stock.code, Stock.id, Stock.is_st).all()}
    try:
        with daily_k_dump(key, require_date=today - timedelta(days=1)) as p:
            bars_map = load_bars(p, {c: v[1] for c, v in codes.items()})
    except Exception as e:  # noqa: BLE001
        print(f"    ⚠️  dump 取不到（{type(e).__name__}: {e}），本项跳过")
        return 0
    if not bars_map:
        print("    dump 里没有任何本库股票，跳过")
        return 0

    dates = sorted({b.date for bars in bars_map.values() for b in bars if b.date < today})
    if not dates:
        return 0
    print(f"    比对窗口：{dates[0]} ~ {dates[-1]}（{len(dates)} 个交易日 × {len(bars_map)} 只）")

    snaps = {
        (r.stock_id, r.date): r
        for r in db.query(StockDailySnapshot)
        .filter(StockDailySnapshot.date >= dates[0],
                StockDailySnapshot.date <= dates[-1]).all()
    }
    bad = []
    for code, bars in bars_map.items():
        sid = codes.get(code, (None, None))[0]
        if not sid:
            continue
        for bar in bars:
            if bar.date >= today:
                continue
            snap = snaps.get((sid, bar.date))
            if not snap or snap.close_price is None:
                continue
            if abs(snap.close_price - bar.close_price) > tol:
                bad.append((snap, code, bar))

    print(f"    收盘价对不上：{len(bad)} 条 / 共比对 {len(snaps)} 条")
    for snap, code, bar in sorted(bad, key=lambda x: -abs(x[0].close_price - x[2].close_price))[:15]:
        diff = (bar.close_price / snap.close_price - 1) * 100 if snap.close_price else 0
        print(f"      {snap.date} {code}  库={snap.close_price:<9} dump={bar.close_price:<9} 差{diff:+.2f}%")
    if len(bad) > 15:
        print(f"      …… 另有 {len(bad) - 15} 条")
    if bad and apply:
        for snap, _, bar in bad:
            snap.close_price = round(bar.close_price, 4)
            snap.pct_change = round(bar.pct_change or 0.0, 4)
            snap.open_price = round(bar.open_price, 4) if bar.open_price else None
            snap.high_price = round(bar.high_price, 4) if bar.high_price else None
            snap.low_price = round(bar.low_price, 4) if bar.low_price else None
            snap.is_limit_up = bar.is_limit_up
            snap.is_limit_down = bar.is_limit_down
            snap.is_broken_board = bar.is_broken_board
            snap.is_one_word_limit_up = bar.is_one_word_limit_up
            snap.is_one_word_limit_down = bar.is_one_word_limit_down
            snap.is_settled = True
        db.commit()
        print(f"    ✅ 已用 dump 覆盖 {len(bad)} 条")
    return len(bad)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的写库（默认只报告）")
    args = ap.parse_args()
    print("=" * 68)
    print("数据修正" + ("（写库）" if args.apply else "（只读，加 --apply 才写）"))
    print("=" * 68)
    db = SessionLocal()
    try:
        n1 = fix_bj_market(db, args.apply)
        n2 = fix_stale_history(db, args.apply)
    finally:
        db.close()
    print("\n" + "=" * 68)
    print(f"合计：交易所标签 {n1} 只 / 历史快照收盘价 {n2} 条"
          + ("  已写库" if args.apply else "  未写库，确认后加 --apply"))
    print("=" * 68)


if __name__ == "__main__":
    main()
