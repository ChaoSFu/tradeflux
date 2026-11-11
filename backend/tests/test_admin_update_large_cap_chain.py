"""
收盘后的手动更新要接上大市值收盘快照（定时日更那条链本来就有）。2026-11-11 15:17 手动更新跑完，
大市值概览还停在前一天：手动这条一直没接。
"""
import fcntl
from datetime import date, datetime

import pytest

from app.routers import admin
from app.services import daily_update_runner as runner
from app.services.eastmoney_fetcher import SH_TZ

D = date(2026, 11, 11)


@pytest.fixture
def env(tmp_path, monkeypatch):
    lock = tmp_path / "daily.lock"
    monkeypatch.setattr(admin, "DAILY_UPDATE_LOCK_FILE", str(lock))
    monkeypatch.setattr(admin, "_write_log_file", lambda *a, **k: None)
    monkeypatch.setattr(admin, "_save_last_update", lambda *a, **k: None)
    monkeypatch.setattr(admin, "record_job_duration", lambda *a, **k: None)
    monkeypatch.setattr(runner, "run_daily_update_subprocess", lambda *a, **k: {"degraded": False, "warnings": []})
    calls = []

    def script(args, on_line=None):
        # 同步大市值时日更的锁必须已经放掉，不然会拦住 15:30 的定时日更
        with open(lock, "w") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        calls.append(args)
        if on_line:
            on_line("[大市值] ✅ 2026-11-11 收盘快照就绪")
        return 0
    monkeypatch.setattr(runner, "run_script_subprocess", script)
    admin._job.update(status="running", log_lines=[], message="")
    return calls


def _at(monkeypatch, h, m):
    monkeypatch.setattr(admin, "_sh_now", lambda: datetime(2026, 11, 11, h, m, tzinfo=SH_TZ))


def test_收盘后手动更新接着同步大市值(env, monkeypatch):
    _at(monkeypatch, 15, 17)
    admin._capture_update(D, skip_boards=True)
    assert env == [["-m", "scripts.large_cap_sync", "--apply", "--if-missing"]], "今天已有东财完整快照就秒退"
    assert admin._job["status"] == "done"
    assert any("大市值" in ln for ln in admin._job["log_lines"]), "进度显示在更新日志里"


def test_收盘前不同步(env, monkeypatch):
    _at(monkeypatch, 14, 30)
    admin._capture_update(D, skip_boards=True)
    assert env == [] and admin._job["status"] == "done"


def test_同步失败不影响这次更新(env, monkeypatch):
    _at(monkeypatch, 15, 40)

    def boom(args, on_line=None):
        raise RuntimeError("clist 被掐")
    monkeypatch.setattr(runner, "run_script_subprocess", boom)
    admin._capture_update(D, skip_boards=True)
    assert admin._job["status"] == "done"
