"""
数据体检接口（2026-09-12）。

报告是 scripts/data_audit.py 在子进程里生成、落盘的文件，这里只读文件。「立即检测 /
试跑 / 确认补上 / 导入」也都是起子进程——检测要读 10 年存档，不能在常驻进程里跑（坑 18）。

读报告、下载导出脚本公开（跟 /admin/update/last 一样）；会起任务或列服务器文件的都要登录。
"""
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.auth import require_auth
from app.services import data_audit_service as svc
from app.services.daily_update_runner import run_script_subprocess

router = APIRouter(prefix="/admin/data-audit", tags=["data-audit"])

_FILE_RE = re.compile(r"^[\w.\-]+\.jsonl$")
_MAX_LOG = 400
_lock = threading.Lock()
_job: dict = {
    "status": "idle",        # idle | running | done | error
    "kind": None,            # audit | fix
    "check_id": None,
    "apply": False,
    "file": None,
    "started_at": None,
    "finished_at": None,
    "message": "",
    "log_lines": [],
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _update_due_soon(minutes: int = 20) -> Optional[str]:
    """日更（盘后 / 盘前）是不是马上要跑。补数要拿同一把锁，撞上了日更那天就被跳过。"""
    try:
        from app.main import _scheduler
    except Exception:  # noqa: BLE001
        return None
    if not _scheduler:
        return None
    now = datetime.now(timezone.utc)
    for jid in ("daily_update", "daily_update_preopen"):
        job = _scheduler.get_job(jid)
        t = job.next_run_time if job else None
        if t and timedelta(0) <= t - now <= timedelta(minutes=minutes):
            return t.strftime("%H:%M")
    return None


def _run_job(args: list) -> None:
    lines: list = []

    def on_line(ln: str) -> None:
        lines.append(ln)
        with _lock:
            _job["log_lines"] = lines[-_MAX_LOG:]

    try:
        code = run_script_subprocess(["-m", "scripts.data_audit", *args], on_line=on_line)
        if code == 0:
            status, msg = "done", "完成"
        elif code == 3:
            status, msg = "error", "日更或别的补数任务正在跑，这次什么都没动，等它跑完再试"
        else:
            status, msg = "error", f"没跑成（退出码 {code}），看日志"
    except Exception as e:  # noqa: BLE001
        status, msg = "error", f"{type(e).__name__}: {e}"
    with _lock:
        _job.update(status=status, message=msg, finished_at=_now())


def _start(kind: str, args: list, *, check_id: Optional[str] = None,
           apply: bool = False, file: Optional[str] = None) -> dict:
    from app.routers import admin
    with _lock:
        if _job["status"] == "running":
            return {"ok": False, "message": "上一个体检 / 补数任务还没跑完"}
        with admin._lock:
            if admin._job["status"] == "running":
                return {"ok": False, "message": "日更正在跑，等它跑完再试"}
        _job.update(status="running", kind=kind, check_id=check_id, apply=apply, file=file,
                    started_at=_now(), finished_at=None, message="启动中…", log_lines=[])
    threading.Thread(target=_run_job, args=(args,), daemon=True).start()
    return {"ok": True, "message": "已启动"}


@router.get("")
def get_report():
    """最新一份体检报告；还没检测过返回 {"report": null}。"""
    return {"report": svc.load_report()}


@router.get("/job")
def get_job():
    with _lock:
        return dict(_job)


@router.post("/run")
def run_now(_: str = Depends(require_auth)):
    return _start("audit", ["run", "--save"])


@router.post("/fix/{check_id}")
def fix(check_id: str, apply: bool = False, file: Optional[str] = None,
        _: str = Depends(require_auth)):
    """一键补：apply=false 试跑（只列出将补什么），apply=true 真的补，补完自动复查。"""
    if check_id not in svc.FIXABLE:
        raise HTTPException(status_code=404, detail=f"「{check_id}」没有一键补法")
    args = ["fix", check_id]
    if check_id == "sector_index":
        if not file or not _FILE_RE.match(file) or not (svc.INBOX_DIR / file).is_file():
            raise HTTPException(status_code=400, detail="收件箱里没有这个文件")
        args += ["--file", file]
    if apply:
        due = _update_due_soon()
        if due:
            return {"ok": False, "message": f"日更 {due} 就要跑了，等它跑完再补（补数跟日更用同一把锁）"}
        args.append("--apply")
    return _start("fix", args, check_id=check_id, apply=apply, file=file)


@router.get("/sector-index/export-script")
def export_script():
    """板块指数导出脚本：已填好最新报告里缺历史 / 有洞的关注板块。"""
    rep = svc.load_report()
    if rep is None:
        raise HTTPException(status_code=404, detail="还没检测过，先跑一次数据体检")
    return PlainTextResponse(svc.render_export_script(svc.sector_export_codes(rep)),
                             media_type="text/javascript; charset=utf-8")


@router.get("/inbox")
def inbox(_: str = Depends(require_auth)):
    return {"dir": str(svc.INBOX_DIR), "files": svc.list_inbox()}
