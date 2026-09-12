"""
把 daily_update 拉成**子进程**跑，而不是在 API 进程里 import 执行。

## 为什么

2026-09-07 现场抓到的：

    17:23:10  [UI] ✅ UI 手动触发，获取锁成功，开始执行
    17:23:30  871MB | /api/admin/update/status
    17:23:40  791MB | /api/admin/update/status
    17:23:50  730MB | /api/admin/update/status

那三行里唯一的请求是几十字节的状态轮询，RSS 却在 700~870MB 之间起伏——涨的
不是接口，是它在轮询的那个东西：跑在同一个进程里的日更。

这台服务器只有 1.87G 物理内存，另有一个常驻 513MB 的无关进程。日更峰值一上来
就换页：实测一个只读文件的接口冷启 30 秒、热了 0.4 秒，%Cpu us 0.0 而 wa 69%，
kswapd0 排在 CPU 榜首。

对照组：日常浏览整个应用只花 +70MB（147→218MB），七个接口顺序打完 +25MB。
**页面从来不是问题，日更才是。**

Python 不会把释放的堆痛快还给操作系统，所以进程内跑完一次，常驻服务就一直背着
那个峰值。跑成子进程，峰值随进程退出一起消失。

`scripts/cron_daily_update.sh` 一直就是这么干的——定时任务和 UI 按钮却各自走了
进程内调用。同一件事三条路径，只有 cron 那条内存是干净的。现在统一。
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from typing import Callable, List, Optional

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: 被拉起的脚本。单独提出来是为了让测试能换成桩程序，真的跑一次子进程 + 管道 +
#: JSON 交接——这条链路只 mock 掉 Popen 的话等于什么都没测
SCRIPT_PATH = os.path.join(BACKEND_DIR, "scripts", "daily_update.py")


def _stream(cmd: List[str], on_line: Optional[Callable[[str], None]]) -> int:
    """在 backend 目录下跑 cmd，stdout / stderr 合流逐行回调，返回退出码。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = BACKEND_DIR + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONUNBUFFERED", "1")     # 不缓冲，UI 才看得到实时进度
    proc = subprocess.Popen(
        cmd, cwd=BACKEND_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if on_line and line.strip():
            on_line(line)
    return proc.wait()


def run_script_subprocess(args: List[str], *,
                          on_line: Optional[Callable[[str], None]] = None) -> int:
    """
    子进程跑 `python <args...>`（cwd=backend），逐行回调输出，**返回退出码**。

    给数据体检这类「退出码本身就是结果」的任务用（3 = 日更正在跑、什么都没动）；
    日更要的是汇总 dict，走下面的 run_daily_update_subprocess。
    """
    return _stream([sys.executable, *args], on_line)


def run_daily_update_subprocess(
    target_date: date,
    *,
    skip_boards: bool = False,
    on_line: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    子进程跑一次日更，逐行回调 stdout，返回 daily_update 的汇总 dict。

    `on_line` 是**流式**的：进程内版本靠 redirect_stdout 到最后才 flush 一次，
    子进程版本反而能让 UI 看到实时进度。

    子进程失败（非 0 退出码）抛 RuntimeError——静默当成功，界面会显示"更新完成"
    而数据其实没动。
    """
    fd, result_path = tempfile.mkstemp(prefix="tradeflux_update_", suffix=".json")
    os.close(fd)

    cmd: List[str] = [sys.executable, SCRIPT_PATH, "--date", target_date.isoformat(),
                      "--result-json", result_path]
    if skip_boards:
        cmd.append("--skip-boards")

    try:
        code = _stream(cmd, on_line)
        if code != 0:
            raise RuntimeError(f"daily_update 子进程退出码 {code}")
        try:
            with open(result_path, encoding="utf-8") as f:
                return json.load(f) or {}
        except (OSError, ValueError):
            # 汇总文件读不出来 ≠ 更新失败。**返回空 dict，不编一个 degraded=False**
            return {}
    finally:
        try:
            os.unlink(result_path)
        except OSError:
            pass
