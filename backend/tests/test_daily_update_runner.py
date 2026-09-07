"""
日更跑在子进程里，不在 API 进程里。

2026-09-07 现场抓到的（每 10 秒采一次 RSS + 同窗口处理过的请求）：

    17:23:10  [UI] ✅ UI 手动触发，获取锁成功，开始执行
    17:23:30  871MB | /api/admin/update/status
    17:23:40  791MB | /api/admin/update/status
    17:23:50  730MB | /api/admin/update/status

那几行里唯一的请求是几十字节的状态轮询，RSS 却在 700~870MB 之间起伏——涨的不是
接口，是它在轮询的那个东西。对照组：整个应用日常浏览只花 +70MB。

这里测的是**交接链路**，不是日更本身：桩程序真的被拉起、stdout 真的逐行回来、
汇总真的通过文件交回、失败真的抛出来。只 mock 掉 Popen 等于什么都没测。
"""
import json
import textwrap
from datetime import date

import pytest

from app.services import daily_update_runner as R

TODAY = date(2026, 9, 7)


def _stub(tmp_path, body: str):
    """
    写一个假的 daily_update.py，接受同样的参数。

    body 要**单独 dedent 再拼**：直接塞进模板里一起 dedent，公共前缀会按模板那
    几行算，body 反而多留一层缩进，子进程起来就是 IndentationError。
    """
    f = tmp_path / "stub_update.py"
    head = textwrap.dedent("""
        import argparse, json, sys
        ap = argparse.ArgumentParser()
        ap.add_argument("--date")
        ap.add_argument("--skip-boards", action="store_true")
        ap.add_argument("--result-json")
        args = ap.parse_args()
    """)
    f.write_text(head + textwrap.dedent(body), encoding="utf-8")
    return str(f)


class TestSubprocessHandoff:

    def test_stdout_逐行回调(self, tmp_path, monkeypatch):
        """**流式**，不是跑完才给。以前 redirect_stdout 要等结束才 flush 一次。"""
        monkeypatch.setattr(R, "SCRIPT_PATH", _stub(tmp_path, """
            print("步骤1"); print(""); print("步骤2")
            open(args.result_json, "w").write(json.dumps({"degraded": False}))
        """))
        seen = []
        R.run_daily_update_subprocess(TODAY, on_line=seen.append)
        assert seen == ["步骤1", "步骤2"], "空行不进日志，其余原样逐行回来"

    def test_汇总通过文件交回(self, tmp_path, monkeypatch):
        """子进程没法把 dict 传回来，靠 --result-json。"""
        monkeypatch.setattr(R, "SCRIPT_PATH", _stub(tmp_path, """
            open(args.result_json, "w").write(
                json.dumps({"degraded": True, "warnings": ["东财降级"]}))
        """))
        r = R.run_daily_update_subprocess(TODAY)
        assert r["degraded"] is True and r["warnings"] == ["东财降级"]

    def test_参数原样传下去(self, tmp_path, monkeypatch):
        monkeypatch.setattr(R, "SCRIPT_PATH", _stub(tmp_path, """
            open(args.result_json, "w").write(
                json.dumps({"date": args.date, "skip": args.skip_boards}))
        """))
        r = R.run_daily_update_subprocess(TODAY, skip_boards=True)
        assert r == {"date": "2026-09-07", "skip": True}

    def test_子进程失败必须抛出来(self, tmp_path, monkeypatch):
        """**静默当成功，界面会显示「更新完成」而数据根本没动。**"""
        monkeypatch.setattr(R, "SCRIPT_PATH", _stub(tmp_path, """
            print("跑到一半炸了"); sys.exit(3)
        """))
        seen = []
        with pytest.raises(RuntimeError, match="3"):
            R.run_daily_update_subprocess(TODAY, on_line=seen.append)
        assert seen == ["跑到一半炸了"], "失败前的日志要留下，否则没法排查"

    def test_汇总文件缺失不等于失败(self, tmp_path, monkeypatch):
        """写不出汇总 ≠ 更新失败。返回空 dict，**不编一个 degraded=False**。"""
        monkeypatch.setattr(R, "SCRIPT_PATH", _stub(tmp_path, """
            print("跑完了但没写汇总")
        """))
        assert R.run_daily_update_subprocess(TODAY) == {}

    def test_临时文件不残留(self, tmp_path, monkeypatch):
        import glob, tempfile
        monkeypatch.setattr(R, "SCRIPT_PATH", _stub(tmp_path, """
            open(args.result_json, "w").write("{}")
        """))
        before = set(glob.glob(f"{tempfile.gettempdir()}/tradeflux_update_*.json"))
        R.run_daily_update_subprocess(TODAY)
        assert set(glob.glob(f"{tempfile.gettempdir()}/tradeflux_update_*.json")) == before


class TestCallersUseSubprocess:
    """三条路径必须走同一个入口。以前 cron 是子进程、定时任务和 UI 按钮是进程内。"""

    def _src(self, mod):
        import pathlib
        return pathlib.Path(mod.__file__).read_text(encoding="utf-8")

    def test_UI手动触发走子进程(self):
        from app.routers import admin
        src = self._src(admin)
        assert "run_daily_update_subprocess" in src
        assert "from scripts.daily_update import run_daily_update" not in src, \
            "进程内 import 执行会把 ~700MB 峰值留在常驻服务的堆里"

    def test_定时任务走子进程(self):
        from app import scheduler
        src = self._src(scheduler)
        assert "run_daily_update_subprocess" in src
        assert "from scripts.daily_update import run_daily_update" not in src
