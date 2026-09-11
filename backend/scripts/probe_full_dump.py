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
    .venv/bin/python -m scripts.probe_full_dump                  # 1. 只问大小，每个 dump 花 1 字节
    .venv/bin/python -m scripts.probe_full_dump --kind daily-k   # 1'. 只问这一个
    .venv/bin/python -m scripts.probe_full_dump --download       # 2. 可续传下载到 data/fuyao/
    .venv/bin/python -m scripts.probe_full_dump --inspect        # 3. 只读 footer，看文件怎么排的
    .venv/bin/python -m scripts.probe_full_dump --trial          # 4. 按需读取，量峰值内存
    .venv/bin/python -m scripts.probe_full_dump --scan           # 5. 流式扫一遍：顺序、行数、内存

第 3、4、5 步只读本地文件，**不碰 fuyao**。
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
KINDS = ("daily-k-10d", "daily-k", "adjustment-factors")
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "fuyao"
PARQUET = DATA_DIR / f"{KIND}.parquet"
META = DATA_DIR / f"{KIND}.meta.json"
SH = timezone(timedelta(hours=8))
READ_COLS = ["thscode", "date_ms", "open_price", "high_price", "low_price",
             "close_price", "volume", "turnover"]


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


# ── 日期列：兼容 int 毫秒 / timestamp / date 三种存法 ──────────────────────────
# 10 日 dump 是 int 毫秒（Asia/Shanghai 零点），全量的没见过——别假设一样

def _to_date(v):
    if isinstance(v, datetime):
        return (v.astimezone(SH) if v.tzinfo else v).date()
    if isinstance(v, date):
        return v
    return datetime.fromtimestamp(v / 1000, tz=SH).date()


def _date_bound(field_type, d: date):
    """把「>= d」里的 d 转成跟这一列同类型的值，否则 pyarrow 的过滤直接报类型错。"""
    import pyarrow as pa
    if pa.types.is_timestamp(field_type):
        return pa.scalar(datetime(d.year, d.month, d.day, tzinfo=SH), type=field_type)
    if pa.types.is_date(field_type):
        return pa.scalar(d, type=field_type)
    return int(datetime(d.year, d.month, d.day, tzinfo=SH).timestamp() * 1000)


# ── 1. 大小 ───────────────────────────────────────────────────────────────────

def step_size(key, only=None):
    kinds = (only,) if only else KINDS
    print(f"== 1. dump 大小（每个只下 1 字节）：{', '.join(kinds)} ==")
    hit_429 = False
    for i, kind in enumerate(kinds):
        if i:
            time.sleep(5)       # 下载链接端点几秒内连着要会 429
        try:
            url = _download_url(key, kind)
            size = _remote_size(url) if url else None
            print(f"  {kind:20s} {_fmt(size and size / 1024 / 1024)}   生成日 {_path_date(url) if url else '—'}")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            hit_429 = hit_429 or "429" in msg
            print(f"  {kind:20s} 失败 {type(e).__name__}: {msg[:100]}")
    if hit_429:
        # 2026-09-11 已确认：是下载链接端点的短速率窗口，不是额度问题
        print("\n  429 = 下载链接要得太快（不是额度问题）。隔一两分钟单独问：--kind <名字>")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n  {DATA_DIR} 所在磁盘剩余 {_fmt(shutil.disk_usage(DATA_DIR).free / 1024 / 1024)}")
    print(f"  当前可用内存 MemAvailable {_fmt(_mem_available_mb())}")


# ── 2. 下载 ───────────────────────────────────────────────────────────────────

def step_download(key):
    print("== 2. 下载 daily-k（可续传：断了从断点接着下；两轮之间等 65 秒再要链接）==")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(DATA_DIR).free
    # 第 1 步量过：172MB。不为了查大小再去要一次下载链接——那会占掉速率窗口
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


def _col_width(t) -> int:
    """一行在内存里占几个字节（解码成普通 Arrow 数组之后）。"""
    import pyarrow as pa
    try:
        return t.byte_width              # int64 / double / timestamp = 8，date32 = 4
    except ValueError:
        pass
    if pa.types.is_large_string(t) or pa.types.is_large_binary(t):
        return 8 + 16                    # 8 字节偏移 + 字符串本体按 16 字节估（"600519.SH" 9 个）
    return 4 + 16                        # 4 字节偏移 + 16


def _rowgroup_estimate(pf, cols=READ_COLS):
    """
    最大那个 row group 读起来多大：{rows, decoded_mb, compressed_mb}。
    decoded_mb 按**行数 × 列类型宽度**算，是整块解码成 Arrow 之后的上界。

    **不能用 footer 里的 total_uncompressed_size**：那是「编码后、压缩前」的大小，
    字典编码 + RLE 之后小得离谱——合成文件 11 万行 × 8 列只报 0.2MB，而光 7 列
    数值就要 6MB。thscode 这种每天重复 5545 次的列尤其如此。第一版拿它当内存
    估计，保护就形同虚设，比没有保护还危险。
    """
    schema = pf.schema_arrow
    names = schema.names
    use = [c for c in cols if c in names]
    width = sum(_col_width(schema.field(c).type) for c in use)
    idx = [names.index(c) for c in use]
    md = pf.metadata
    rows = comp = 0
    for i in range(md.num_row_groups):
        rg = md.row_group(i)
        rows = max(rows, rg.num_rows)
        comp = max(comp, sum(rg.column(j).total_compressed_size for j in idx))
    return {"rows": rows, "decoded_mb": rows * width / 1048576, "compressed_mb": comp / 1048576}


def step_inspect():
    import pyarrow.parquet as pq
    print("== 3. 文件结构（只读 footer，不解码数据）==")
    if not PARQUET.exists():
        print(f"  {PARQUET} 不存在，先跑 --download"); return
    pf = pq.ParquetFile(PARQUET)
    md = pf.metadata
    print(f"  行数 {md.num_rows:,}   row group {md.num_row_groups}   列 {pf.schema_arrow.names}")
    print(f"  date_ms 的类型：{pf.schema_arrow.field('date_ms').type}")
    print(f"  created_by: {md.created_by}")
    rows = [md.row_group(i).num_rows for i in range(md.num_row_groups)]
    rge = _rowgroup_estimate(pf)
    print(f"  每个 row group：{min(rows):,}~{max(rows):,} 行；要读的 {len(READ_COLS)} 列，最大一块"
          f"压缩态 {rge['compressed_mb']:.1f}MB，整块解码后约 {rge['decoded_mb']:.0f}MB（行数×类型宽度估）")

    d = _rg_stats(pf, "date_ms")
    c = _rg_stats(pf, "thscode")
    dd = [x for x in d if x]
    if dd:
        print(f"  日期范围 {_to_date(min(x[0] for x in dd))} ~ {_to_date(max(x[1] for x in dd))}")
    if md.num_row_groups < 2:
        # 1 个块时"是否有序"恒为真，打出 True/True 是误导：任何过滤都跳不过它
        print("  **只有 1 个 row group**——谈不上按什么分块，任何过滤都跳不过它：读几只票也要整块解码")
    else:
        print(f"  date_ms 有统计信息的块：{len(dd)}/{len(d)}   按日期分块：{_monotonic(d)}")
        print(f"  thscode 有统计信息的块：{sum(1 for x in c if x)}/{len(c)}   按代码分块：{_monotonic(c)}")
    full_gb = md.num_rows * 8 * 60 / 1024 / 1024 / 1024
    print(f"\n  照 load_bars 现在的读法（全表 to_pylist）估计要 ~{full_gb:.1f}GB 内存 —— "
          + ("不能用" if full_gb > 0.5 else "勉强可以"))


# ── 4. 按需读取的峰值内存 ─────────────────────────────────────────────────────

def trial_refusal(est_mb, avail_mb):
    """
    能不能在这台机器上跑按需读取。返回拒绝理由，None = 可以跑。

    est_mb = 最大一块「压缩态 + 整块解码」的上界（见 _rowgroup_estimate）。Arrow 实际
    多半按批解码、峰值更低，但这台机器没有容错余地——这个项目之前就因为内存打进
    swap 整站超时过——所以按上界算，不许超过可用内存的一半。这条线是保守的经验值，
    不是测出来的。
    """
    if avail_mb is None:
        return "拿不到可用内存（不是 Linux？），判断不了"
    if avail_mb < 500:
        return f"可用内存只有 {avail_mb:.0f}MB，不到 500MB"
    if est_mb > avail_mb * 0.5:
        return f"最大一块读起来的上界约 {est_mb:.0f}MB，超过可用内存 {avail_mb:.0f}MB 的一半"
    return None


def _read(codes, since):
    """
    按需读取。**单线程、只预读 1 块**——默认的多线程加预读会同时解压好几块，
    峰值是这里的好几倍。这也是将来生产上该用的读法，所以量的就是它。
    """
    import pyarrow.compute as pc
    import pyarrow.dataset as ds
    dset = ds.dataset(PARQUET, format="parquet")
    ths = [f"{c}.{thscode_suffix(c)}" for c in codes]
    flt = pc.field("thscode").isin(ths) & (
        pc.field("date_ms") >= _date_bound(dset.schema.field("date_ms").type, since))
    cols = [c for c in READ_COLS if c in dset.schema.names]
    t0 = time.time()
    tbl = ds.Scanner.from_dataset(dset, columns=cols, filter=flt, use_threads=False,
                                  batch_readahead=1, fragment_readahead=1,
                                  batch_size=65536).to_table()
    return tbl, time.time() - t0


def step_trial(force: bool):
    import pyarrow.parquet as pq
    print("== 4. 按需读取：峰值内存 ==")
    if not PARQUET.exists():
        print(f"  {PARQUET} 不存在，先跑 --download"); return
    avail = _mem_available_mb()
    rge = _rowgroup_estimate(pq.ParquetFile(PARQUET))
    est_mb = rge["compressed_mb"] + rge["decoded_mb"]
    # **基线峰值必须先记下来**：光是 import app（SQLAlchemy/pyarrow/配置）就要
    # 几十到两百多 MB，不减掉它，读不出读 dump 本身花了多少
    base = _peak_mb()
    print(f"  开始前 MemAvailable {_fmt(avail)}，本进程 RSS {_fmt(_rss_mb())}，"
          f"基线峰值 {_fmt(base)}（import 的开销）")
    print(f"  最大一块 {rge['rows']:,} 行：压缩态 {rge['compressed_mb']:.1f}MB + 整块解码约 "
          f"{rge['decoded_mb']:.0f}MB = 读它的上界约 {est_mb:.0f}MB")
    why = trial_refusal(est_mb, avail)
    if why and not force:
        print(f"  不跑：{why}。确认要跑加 --force"); return

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


# ── 5. 流式扫一遍：能不能在这台机器上一次性转换 ─────────────────────────────

def step_scan(cap_mb: float):
    """
    逐批流式读完整个文件（8 列），回答决定转换方案的两件事：
      · 是不是按 (代码, 日期) 全局有序——是，就能边读边写成"每块只含少数几只票"的
        小块文件；不是，就得换别的办法
      · 流式读完一遍要多少内存、多少时间——决定这个一次性转换能不能在这台机器上跑

    背景（2026-09-11 --inspect）：2 个 row group、最大一块 763 万行、按代码分块。
    按需读两只票也得啃一整块，所以**不能在日更里直接读这个文件**，得先转换一次。

    为什么这一步不会把机器打挂：
      · ParquetFile(buffer_size=8MB, pre_buffer=False) + iter_batches(use_threads=False)：
        逐页读、逐批解码，不会把 553MB 的整块解出来
      · 每批之后看 RSS，比开始时多出 cap_mb 就立刻停
      · 单步最坏的尖峰 ≈ 一块的压缩数据（footer 里写着），开跑前核对它
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    print("== 5. 流式扫一遍：顺序、行数、内存 ==")
    if not PARQUET.exists():
        print(f"  {PARQUET} 不存在，先跑 --download"); return
    avail = _mem_available_mb()
    pf = pq.ParquetFile(PARQUET, buffer_size=8 << 20, pre_buffer=False)
    rge = _rowgroup_estimate(pf)
    base = _rss_mb()
    print(f"  开始前 MemAvailable {_fmt(avail)}，RSS {_fmt(base)}；单步最坏尖峰 ≈ 一块压缩态 "
          f"{rge['compressed_mb']:.0f}MB；RSS 比开始多出 {cap_mb:.0f}MB 就停")
    if avail is not None and rge["compressed_mb"] > avail * 0.5:
        print("  不跑：一块的压缩数据就超过可用内存一半"); return

    cols = [c for c in READ_COLS if c in pf.schema_arrow.names]
    t0 = time.time()
    rows = code_desc = date_bad = 0
    per_code: dict = {}          # thscode -> [行数, 最早 ms, 最晚 ms]
    prev = None                  # 上一批最后一行的 (thscode, date_ms)，用来接批与批的边界
    peak = base
    for rgi in range(pf.num_row_groups):
        for b in pf.iter_batches(batch_size=65536, columns=cols, row_groups=[rgi],
                                 use_threads=False):
            n = b.num_rows
            if not n:
                continue
            c, d = b.column("thscode"), b.column("date_ms")
            if n > 1:
                c0, c1, d0, d1 = c.slice(0, n - 1), c.slice(1), d.slice(0, n - 1), d.slice(1)
                code_desc += pc.sum(pc.less(c1, c0)).as_py() or 0
                date_bad += pc.sum(pc.and_(pc.equal(c1, c0), pc.less_equal(d1, d0))).as_py() or 0
            first = (c[0].as_py(), d[0].as_py())
            if prev is not None:
                if first[0] < prev[0]:
                    code_desc += 1
                elif first[0] == prev[0] and first[1] <= prev[1]:
                    date_bad += 1
            prev = (c[n - 1].as_py(), d[n - 1].as_py())
            g = pa.Table.from_batches([b.select(["thscode", "date_ms"])]).group_by("thscode").aggregate(
                [("date_ms", "count"), ("date_ms", "min"), ("date_ms", "max")])
            for code, cnt, mn, mx in zip(g["thscode"].to_pylist(), g["date_ms_count"].to_pylist(),
                                         g["date_ms_min"].to_pylist(), g["date_ms_max"].to_pylist()):
                e = per_code.get(code)
                if e:
                    e[0] += cnt; e[1] = min(e[1], mn); e[2] = max(e[2], mx)
                else:
                    per_code[code] = [cnt, mn, mx]
            rows += n
            rss = _rss_mb()
            if rss is not None:
                peak = max(peak or 0, rss)
                if base is not None and rss - base > cap_mb:
                    print(f"  ⚠️ 读到第 {rows:,} 行时 RSS 比开始多了 {rss - base:.0f}MB，超过上限，停")
                    return
    dt = time.time() - t0
    if not per_code:
        print("  文件里一行都没有"); return
    counts = sorted(v[0] for v in per_code.values())
    max_ms = max(v[2] for v in per_code.values())
    active = sum(1 for v in per_code.values() if v[2] == max_ms)
    ordered = code_desc == 0 and date_bad == 0
    print(f"  扫完 {rows:,} 行，用时 {dt:.0f}s；共 {len(per_code):,} 只票")
    print(f"  按 (代码, 日期) 全局有序：{'是' if ordered else '否'}"
          f"（代码倒退 {code_desc} 处，同一代码日期没有递增 {date_bad} 处）")
    print(f"  每只票行数：最少 {counts[0]:,}，中位 {counts[len(counts) // 2]:,}，最多 {counts[-1]:,}")
    print(f"  数据一直到最后一天（{_to_date(max_ms)}）的票：{active:,} 只；其余是退市或停牌到更早")
    delta = (peak - base) if (peak is not None and base is not None) else None
    print(f"  RSS 峰值 {_fmt(peak)}（比开始多 {_fmt(delta)}），进程峰值 {_fmt(_peak_mb())}")
    print("\n  **把整段输出发给我**——「全局有序」和「比开始多」这两个数决定一次性转换怎么做")


def main():
    global PARQUET, META
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--trial", action="store_true")
    ap.add_argument("--scan", action="store_true", help="流式扫一遍：顺序、行数、内存")
    ap.add_argument("--cap", type=float, default=300.0,
                    help="--scan 的内存上限：RSS 比开始时多出这么多 MB 就停（默认 300）")
    ap.add_argument("--force", action="store_true", help="--trial 的内存检查不通过时仍然跑")
    ap.add_argument("--kind", choices=KINDS, help="第 1 步只问这一个 dump")
    ap.add_argument("--path", type=Path, help="--inspect / --trial 改读这个文件（本地测试用）")
    a = ap.parse_args()
    if a.path:
        PARQUET, META = a.path, a.path.with_suffix(".meta.json")

    if a.inspect:
        step_inspect(); return
    if a.trial:
        step_trial(a.force); return
    if a.scan:
        step_scan(a.cap); return
    key = get_api_key()
    if not key:
        print("没配 FUYAO_API_KEY"); sys.exit(1)
    if a.download:
        step_download(key); return
    step_size(key, a.kind)


if __name__ == "__main__":
    main()
