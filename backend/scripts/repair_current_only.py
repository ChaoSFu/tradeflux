"""
修「只给当前值」的两份存档：成交额前列名单、涨停雷达核心召回名单（2026-11-25）。

起因：2026-11-25 日更把目标日错改回 11-24（K 线源缺当天那根被当成休市），这两份就把 11-25 的数据记成了 11-24，
11-24 原来的被覆盖。选股 prompt 认指定日期的问法，可以按「那一天」重新问回来：
  · 成交额前列：问法跟日更同一句（读库里配置的，生产上是自定义的），每个行情条件前加日期，再要那天的涨跌幅、换手；
    列名带那天（TRADING_VOLUMES{2026-11-24}），涨跌幅跟腾讯日 K 逐只对上。
  · 召回名单：整句加日期接口不认，拆成四个分支分别问「截至那天」再并起来（用今天验证过，并集跟整句一只不差），
    再按代码问那天的区间涨幅、涨跌幅，涨停天数每个窗口单问「大于等于1」（直接要那一列会把没涨停的票筛掉）。最高连板这一项接口不认日期（给的是截至今天的）：只会多召回、不会漏。
监管状态快照不在这里：监管名单没有按日期问的接口，11-24 那份记的是 11-25 的状态，修不回来（一天只差一两只）。

    cd /opt/code/tradeflux/backend
    .venv/bin/python -m scripts.repair_current_only --date 2026-11-24            # 试跑：按日期问回来，跟库里比，不写
    .venv/bin/python -m scripts.repair_current_only --date 2026-11-24 --apply    # 覆盖写回

请求：成交额 1 次、召回 4 + 3×4 次，间隔 ≥2 秒。退出码：0 成功 / 1 有一份没拿到。
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db  # noqa: E402
from app.services import prompt_api  # noqa: E402


def _log(msg: str) -> None:
    print(msg, flush=True)


def run(db, d: date, *, apply: bool, what=("turnover", "recall"), log=_log) -> bool:
    from app.services.limit_up_detail_service import restore_core_recall
    from app.services.turnover_service import restore_turnover_pool
    ok = True
    if "turnover" in what:
        try:
            r = restore_turnover_pool(db, d, apply=apply)
            log(f"[修复] {d} 成交额前列：按日期问到 {r['fetched']} 只，库里 {r['stored']} 只，重合 {r['same']} 只"
                + ("，✅ 已覆盖写回" if r["applied"] else "（试跑，没写）"))
            log(f"    按日期问到的前 5：{r['top5']}")
            log(f"    库里现在的前 5：  {r['stored_top5']}")
            if r["only_new"] or r["only_stored"]:
                log(f"    只在新名单里：{r['only_new']}；只在库里：{r['only_stored']}")
        except prompt_api.PromptError as e:
            ok = False
            log(f"[修复] {d} 成交额前列 ❌ 没问到：{e}（库里的没动）")
    if "recall" in what:
        try:
            r = restore_core_recall(db, d, apply=apply)
            log(f"[修复] {d} 召回名单：按日期重建 {r['fetched']} 只，库里 {r['stored']} 只，重合 {r['same']} 只，"
                f"当日涨跌幅不一样的 {r['chg_differs']} 只" + ("，✅ 已覆盖写回" if r["applied"] else "（试跑，没写）"))
            if r["only_new"] or r["only_stored"]:
                log(f"    只在新名单里：{r['only_new']}；只在库里：{r['only_stored']}")
            if r["missing"]:
                log(f"    ⚠️ 分支里有、按代码没问到的 {len(r['missing'])} 只（停牌 / 退市之类）：{r['missing'][:10]}")
        except prompt_api.PromptError as e:
            ok = False
            log(f"[修复] {d} 召回名单 ❌ 没重建成：{e}（库里的没动）")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="按日期重问成交额前列、召回名单，修被别的日子覆盖的存档")
    ap.add_argument("--date", required=True, type=date.fromisoformat)
    ap.add_argument("--apply", action="store_true", help="覆盖写回（不加就是试跑：问回来跟库里比，不写）")
    ap.add_argument("--only", choices=["turnover", "recall"], help="只修其中一份")
    ap.add_argument("--interval", type=float, default=2.0, help="任意两次选股请求至少隔几秒（默认 2）")
    a = ap.parse_args()
    init_db()
    from app.services import api_call_counter
    api_call_counter.install()
    prompt_api.MIN_INTERVAL_S = max(prompt_api.MIN_INTERVAL_S, a.interval)
    db = SessionLocal()
    try:
        ok = run(db, a.date, apply=a.apply, what=(a.only,) if a.only else ("turnover", "recall"))
        return 0 if ok else 1
    finally:
        for ln in api_call_counter.lines(prefix="[修复] "):
            _log(ln)
        db.close()


if __name__ == "__main__":
    sys.exit(main())
