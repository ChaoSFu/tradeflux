"""
probe_full_dump.py —— 在服务器上实测 fuyao 10 年全量 dump（`daily-k`）到底能不能用。

**只测量，不改库、不接入日更。** 用它回答四个决定设计的问题：

  1. 多大？        —— 决定磁盘够不够、多久能下完
  2. 怎么排的？    —— 按日期分块还是按代码分块，决定"只读几只票"时能跳过多少
  3. 按需读几只票，**峰值内存**多少？ —— 这台机器只有 1.87G，这是生死线
  4. 按需读库里全部股票近 110 天（≈75 个交易日），峰值内存多少？ —— 最坏情况

为什么要单独量：现在的 `load_bars` 是 `read_table` 后逐列 `.to_pylist()`，
10 天 × 5545 只 = 5.5 万行没问题；10 年约 1300 万行，照这个读法会变成
上亿个 Python 对象，直接把机器打进 swap。设计必须等这几个数。

用法（在服务器上，**分四步，每步看完再决定要不要下一步**）：

    cd /opt/code/tradeflux/backend
    .venv/bin/python -m scripts.probe_full_dump              # 1. 只问大小，每个 dump 花 1 字节
    .venv/bin/python -m scripts.probe_full_dump --kind daily-k   # 1'. 只问这一个（遇到 429 时用）
    .venv/bin/python -m scripts.probe_full_dump --download   # 2. 流式下载到 data/fuyao/，不占内存
    .venv/bin/python -m scripts.probe_full_dump --inspect    # 3. 只读 footer，看文件怎么排的
    .venv/bin/python -m scripts.probe_full_dump --trial      # 4. 按需读取，量峰值内存
"""
import argparse
import json
import os
import resource
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.services.fuyao_dump import (
    _download_url, _path_date, _remote_size, download_dump_resumable, get_api_key, thscode_suffix,
)

KIND = "daily-k"
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "fuyao"
PARQUET = DATA_DIR / f"{KIND}.parquet"
META = DATA_DIR / f"{KIND}.meta.json"
SH = timezone(timedelta(hours=8))


# ── 内存：只在 Linux 上有意义，拿不到就是 None，不是 0 ─────────────────────────

def _rss_mb():
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024
    except Exception:  # noqa: BLE001
        return None


def _peak_mb():
    # ru_maxrss 的单位：Linux 是 KB，macOS 是字节。不分平台的话本地试跑会大 1024 倍
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / 1024 / 1024 if sys.platform == "darwin" else v / 1024


def _mem_available_mb():
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except Exception:  # noqa: BLE001
        return None
    return None


def _fmt(v, unit="MB"):
    return "—" if v is None else f"{v:,.0f}{unit}"


# ── 1. 大小 ───────────────────────────────────────────────────────────────────

KINDS = ("daily-k-10d", "daily-k", "adjustment-factors")


def step_size(key, only=None):
    kinds = (only,) if only else KINDS
    print(f"== 1. dump 大小（每个只下 1 字节）：{', '.join(kinds)} ==")
    hit_429 = False
    for i, kind in enumerate(kinds):
        # **隔开问。** 第一版三个下载链接背靠背连发，结果 10d 成功、后两个 429——
        # 分不清是"连发太快"还是"账号没有这两个 dump 的额度"。先把"太快"这个
        # 解释排除掉
        if i:
            time.sleep(5)
        try:
            url = _download_url(key, kind)
            size = _remote_size(url) if url else None
            print(f"  {kind:20s} {_fmt(size and size / 1024 / 1024)}   生成日 {_path_date(url) if url else '—'}")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            hit_429 = hit_429 or "429" in msg or "limit" in msg.lower()
            print(f"  {kind:20s} 失败 {type(e).__name__}: {msg[:100]}")
    if hit_429:
        # 三种解释，靠两次单独询问就能分开（2026-09-11 第一次见到 fuyao 429）
        print("\n  429 有三种可能，隔一分钟以上**依次单独**问这两个就能分开：")
        print("      --kind daily-k      然后再隔一分钟    --kind daily-k-10d")
        print("    · daily-k 能过               → 只是连发太快")
        print("    · daily-k 429、10d 能过      → 账号没有 daily-k 的额度，去 "
              "https://fuyao.aicubes.cn/admin 看套餐")
        print("    · 两个都 429                 → **整个 key 的配额用完了（跨端点共享）**，"
              "日更自己的 10 日 dump 也在抢这份额度")
        print("  **别连着重试**：如果是按天/按窗口计数，每试一次都在消耗额度")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n  {DATA_DIR} 所在磁盘剩余 {_fmt(shutil.disk_usage(DATA_DIR).free / 1024 / 1024)}")
    print(f"  当前可用内存 MemAvailable {_fmt(_mem_available_mb())}")


# ── 2. 下载 ───────────────────────────────────────────────────────────────────

def step_download(key):
    print("== 2. 下载 daily-k（可续传：断了从断点接着下；两轮之间等 65 秒——"
          "下载链接端点连着要必 429）==")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(DATA_DIR).free
    # 第 1 步量过：172MB。**不为了查大小再去要一次下载链接**——那会占掉速率窗口，
    # 紧接着的真下载就 429 了。1GB 的余量足够（文件 + 原子替换时的旧文件）
    if free < 1024 ** 3:
        print(f"  磁盘剩余 {free / 1024 / 1024:,.0f}MB，不到 1GB，不下"); return
    if (PARQUET.with_name(PARQUET.name + ".part")).exists():
        print("  发现上次没下完的半截，会先核对版本再接着续")

    shown = {"pct": -10}

    def _progress(have, total):
        pct = int(have * 100 / total)
        if pct >= shown["pct"] + 10:
            shown["pct"] = pct - pct % 10
            print(f"    {have / 1048576:6.1f} / {total / 1048576:.1f}MB  ({pct}%)", flush=True)

    r = download_dump_resumable(key, KIND, PARQUET, progress=_progress)
    META.write_text(json.dumps({
        "path_date": r["path_date"], "size": r["bytes"],
        "fetched_at": datetime.now(SH).isoformat(timespec="seconds"),
    }, ensure_ascii=False), encoding="utf-8")
    mb = r["bytes"] / 1024 / 1024
    print(f"  {mb:,.1f}MB，用时 {r['seconds']:.0f}s（{mb / max(r['seconds'], 0.1):.1f}MB/s），"
          f"{r['rounds']} 轮，断点续传 {r['resumed']} 次")
    for e in r["errors"]:
        print(f"    · {e}")
    print(f"  → {PARQUET}")
    print(f"  峰值内存 {_fmt(_peak_mb())}（流式写盘，应当只有几十到一百多 MB）")


# ── 3. 结构：只读 footer ──────────────────────────────────────────────────────

def _rg_stats(pf, col):
    """每个 row group 这一列的 (min, max)。没统计信息就是 None——那样就跳不过任何块。"""
    idx = pf.schema_arrow.get_field_index(col)
    out = []
    for i in range(pf.metadata.num_row_groups):
        st = pf.metadata.row_group(i).column(idx).statistics
        out.append((st.min, st.max) if st is not None and st.has_min_max else None)
    return out


def _monotonic(stats):
    """相邻块互不重叠且递增 = 按这一列排过序、分块的。"""
    s = [x for x in stats if x]
    return len(s) == len(stats) and all(a[1] <= b[0] for a, b in zip(s, s[1:]))


def step_inspect():
    import pyarrow.parquet as pq
    print("== 3. 文件结构（只读 footer，不解码数据）==")
    if not PARQUET.exists():
        print(f"  {PARQUET} 不存在，先跑 --download"); return
    pf = pq.ParquetFile(PARQUET)
    md = pf.metadata
    print(f"  行数 {md.num_rows:,}   row group {md.num_row_groups}   列 {pf.schema_arrow.names}")
    print(f"  created_by: {md.created_by}")
    sizes = [md.row_group(i).total_byte_size for i in range(md.num_row_groups)]
    rows = [md.row_group(i).num_rows for i in range(md.num_row_groups)]
    print(f"  每个 row group：{min(rows):,}~{max(rows):,} 行，解码后 "
          f"{min(sizes) / 1024 / 1024:.1f}~{max(sizes) / 1024 / 1024:.1f}MB")

    d = _rg_stats(pf, "date_ms")
    c = _rg_stats(pf, "thscode")
    dd = [x for x in d if x]
    if dd:
        lo = min(x[0] for x in dd); hi = max(x[1] for x in dd)
        to_d = lambda ms: datetime.fromtimestamp(ms / 1000, tz=SH).date()  # noqa: E731
        print(f"  日期范围 {to_d(lo)} ~ {to_d(hi)}")
    print(f"  date_ms 有统计信息的块：{len(dd)}/{len(d)}   按日期分块：{_monotonic(d)}")
    print(f"  thscode 有统计信息的块：{sum(1 for x in c if x)}/{len(c)}   按代码分块：{_monotonic(c)}")
    # 照现在 load_bars 的读法（read_table + 每列 to_pylist）会吃多少
    est = md.num_rows * 8 * 60 / 1024 / 1024 / 1024
    print(f"\n  照 load_bars 现在的读法（全表 to_pylist）估计要 ~{est:.1f}GB 内存 —— "
          + ("不能用" if est > 0.5 else "勉强可以"))


# ── 4. 按需读取的峰值内存 ─────────────────────────────────────────────────────

def _read(codes, since):
    import pyarrow.compute as pc
    import pyarrow.dataset as ds
    ths = [f"{c}.{thscode_suffix(c)}" for c in codes]
    since_ms = int(datetime(since.year, since.month, since.day, tzinfo=SH).timestamp() * 1000)
    t0 = time.time()
    tbl = ds.dataset(PARQUET, format="parquet").to_table(
        columns=["thscode", "date_ms", "open_price", "high_price", "low_price",
                 "close_price", "volume", "turnover"],
        filter=pc.field("thscode").isin(ths) & (pc.field("date_ms") >= since_ms))
    return tbl, time.time() - t0


def step_trial(force: bool):
    print("== 4. 按需读取：峰值内存 ==")
    if not PARQUET.exists():
        print(f"  {PARQUET} 不存在，先跑 --download"); return
    avail = _mem_available_mb()
    # **基线峰值必须先记下来**：光是 import app（SQLAlchemy/pyarrow/配置）就要
    # 两百多 MB，不减掉它，下面的"进程峰值"读不出读 dump 本身花了多少
    base = _peak_mb()
    print(f"  开始前 MemAvailable {_fmt(avail)}，本进程 RSS {_fmt(_rss_mb())}，"
          f"基线峰值 {_fmt(base)}（import 的开销）")
    if avail is not None and avail < 500 and not force:
        print("  可用内存不到 500MB，不跑（加 --force 强制）"); return

    since = date.today() - timedelta(days=110)   # ≈ 75 个交易日，够一个 65 日窗口
    few = ["600519", "000001", "300750", "688981", "601127"]
    tbl, dt = _read(few, since)
    per = {}
    for t in tbl.column("thscode").to_pylist():
        per[t] = per.get(t, 0) + 1
    print(f"  A. 5 只 × 近 110 天：{tbl.num_rows} 行，{dt:.1f}s，"
          f"RSS {_fmt(_rss_mb())}，进程峰值 {_fmt(_peak_mb())}（比基线 +{_fmt(_peak_mb() - base)}）")
    print(f"     每只行数 {per}")
    del tbl

    # 最坏情况：库里全部股票（相当于用它给全库补历史）
    from app.database import SessionLocal
    from app.models.stock import Stock
    db = SessionLocal()
    try:
        codes = [c for (c,) in db.query(Stock.code).all()]
    finally:
        db.close()
    tbl, dt = _read(codes, since)
    print(f"  B. 库里全部 {len(codes)} 只 × 近 110 天：{tbl.num_rows:,} 行，{dt:.1f}s，"
          f"RSS {_fmt(_rss_mb())}，进程峰值 {_fmt(_peak_mb())}（比基线 +{_fmt(_peak_mb() - base)}）")
    print(f"\n  结束后 MemAvailable {_fmt(_mem_available_mb())}")
    print("  **把整段输出发给我**——A、B 两行「比基线」多出来的那个数，决定能不能在日更进程里直接读")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--trial", action="store_true")
    ap.add_argument("--force", action="store_true", help="可用内存不足时仍然跑 --trial")
    ap.add_argument("--kind", choices=KINDS, help="第 1 步只问这一个 dump（遇到 429 时用）")
    a = ap.parse_args()

    if a.inspect:
        step_inspect(); return
    if a.trial:
        step_trial(a.force); return
    key = get_api_key()
    if not key:
        print("没配 FUYAO_API_KEY"); sys.exit(1)
    if a.download:
        step_download(key); return
    step_size(key, a.kind)


if __name__ == "__main__":
    main()
