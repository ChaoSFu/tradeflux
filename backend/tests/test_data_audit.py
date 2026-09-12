"""
数据体检（services/data_audit_service.py + scripts/data_audit.py + routers/data_audit.py，2026-09-12）。

护栏：
- 「那天该有」要有参照：交易日历定交易日，建表之前的日子不算缺；没有日历如实标 warn；
- 截止日：盘中不含今天，收盘后含今天；
- 只能当天拍的数据：今天缺 → 可补（重跑日更）；更早缺 → expired，不进待办、不亮红点；
- 板块指数：历史不足 / 窗口有洞进导出清单；只缺今天那根归日更；东财确认没有的排除；
- 导入绝不覆盖已有行；试跑不写库；
- 一项查挂了不拖垮其余各项；会起任务的接口要登录。
"""
import argparse
import importlib.util
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import create_access_token
from app.models.market_index import IndexDailySnapshot, SectorIndexDaily
from app.models.sector import Sector
from app.models.turnover_pool import TurnoverPoolDaily
from app.routers import data_audit as router_mod
from app.services import data_audit_service as svc
from app.services.index_trend_service import INDICES
from app.services.trading_calendar import _write_cache

_BACKEND = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _BACKEND / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = _load("_data_audit_cli", "scripts/data_audit.py")
imp = _load("_import_sector_klines", "scripts/import_sector_klines.py")

SH = svc.SH
TODAY = date(2026, 9, 11)                        # 周五
DAYS = [d for d in (date(2026, 5, 18) + timedelta(days=i) for i in range(200))
        if d.weekday() < 5 and d <= TODAY]       # 测试里工作日都当交易日
AFTER_CLOSE = datetime(2026, 9, 11, 16, 0, tzinfo=SH)
INTRADAY = datetime(2026, 9, 11, 10, 0, tzinfo=SH)


@pytest.fixture(autouse=True)
def _env(db, tmp_path, monkeypatch):
    _write_cache(db, DAYS)
    monkeypatch.setattr(svc, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(svc, "INBOX_DIR", tmp_path / "inbox")
    yield


def _turnover(db, d):
    db.add(TurnoverPoolDaily(date=d, stock_code="600000", stock_name="浦发银行",
                             rank=1, amount=1e9, pct_change=1.0))


def _sector(db, code, watched=True):
    db.add(Sector(code=code, name=f"板块{code[-2:]}", is_watched=watched))


def _bars(db, code, days):
    for d in days:
        db.add(SectorIndexDaily(sector_code=code, date=d, close=1000.0))


class Test截止日与窗口:
    def test_盘中不含今天(self, db):
        ctx = svc.build_context(db, INTRADAY)
        assert ctx.through == date(2026, 9, 10) and not ctx.through_is_today

    def test_收盘后含今天_窗口65个交易日(self, db):
        ctx = svc.build_context(db, AFTER_CLOSE)
        assert ctx.through == TODAY and ctx.through_is_today
        assert len(ctx.window) == svc.WINDOW_DAYS and ctx.window[-1] == TODAY

    def test_没有日历就如实标warn(self, db, monkeypatch):
        monkeypatch.setattr(svc, "get_trading_days", lambda db: None)
        c = svc.run_check(db, "calendar", now=AFTER_CLOSE)
        assert c["status"] == svc.WARN and "查不出来" in c["summary"]

    def test_日历判断只有一份(self):
        from app.services import pre_trade_check_service, trading_calendar
        assert pre_trade_check_service._calendar_status is trading_calendar.calendar_status


class Test只能当天拍的数据:
    def _seed(self, db, skip):
        for d in DAYS:
            if d >= date(2026, 8, 20) and d not in skip:
                _turnover(db, d)
        db.commit()

    def test_今天缺可补_更早缺补不回来(self, db):
        self._seed(db, {date(2026, 9, 2), TODAY})
        c = svc.run_check(db, "turnover_pool", now=AFTER_CLOSE)
        assert c["status"] == svc.GAP and c["fix"]["kind"] == "rerun_update"
        assert c["missing_dates"] == ["2026-09-11"]
        assert c["expired_dates"] == ["2026-09-02"]      # 08-20 建表之前的日子不算缺

    def test_盘中只剩更早的缺口_不进待办(self, db):
        self._seed(db, {date(2026, 9, 2), TODAY})
        c = svc.run_check(db, "turnover_pool", now=INTRADAY)
        assert c["status"] == svc.EXPIRED and c["expired_dates"] == ["2026-09-02"]
        assert svc.summarize([c])["todo"] == 0

    def test_空表如实报(self, db):
        assert svc.run_check(db, "turnover_pool", now=AFTER_CLOSE)["status"] == svc.WARN


class Test指数日线:
    def test_缺某个指数的某天(self, db):
        window = svc.build_context(db, AFTER_CLOSE).window
        for m in INDICES:
            for d in window:
                if not (m["code"] == "399006" and d == date(2026, 9, 3)):
                    db.add(IndexDailySnapshot(index_code=m["code"], date=d, close=1.0))
        db.commit()
        c = svc.run_check(db, "index_daily", now=AFTER_CLOSE)
        assert c["status"] == svc.GAP and c["fix"]["kind"] == "server"
        assert c["items"] == [{"code": "399006", "name": "创业板指", "missing": 1,
                               "missing_dates": ["2026-09-03"]}]


class Test板块指数:
    @pytest.fixture
    def seeded(self, db):
        full = DAYS[-80:]                                        # 80 根，盖住整个窗口
        _sector(db, "BK0001"); _bars(db, "BK0001", full)        # 齐的
        _sector(db, "BK0002"); _bars(db, "BK0002", DAYS[-5:])   # 历史不足 70 根
        _sector(db, "BK0003")
        _bars(db, "BK0003", [d for d in full if d != date(2026, 8, 5)])   # 窗口里有洞
        _sector(db, "BK0004", watched=False)                   # 不关注，不管
        _sector(db, "BK0005")                                   # 东财没有指数日线
        _sector(db, "BK0006")
        _bars(db, "BK0006", [d for d in full if d != TODAY])   # 只缺今天那根
        db.commit()
        svc.record_no_data_codes(db, ["BK0005"], TODAY)

    def test_历史不足和有洞进导出清单(self, db, seeded):
        c = svc.run_check(db, "sector_index", now=AFTER_CLOSE)
        assert c["status"] == svc.GAP and c["fix"]["kind"] == "local_export"
        assert [(it["code"], it["reason"]) for it in c["items"]] == [("BK0002", "历史不足"), ("BK0003", "有洞")]
        assert c["items"][1]["holes"] == ["2026-08-05"]
        # 只缺今天那根的归日更（板块同步写），不进导出清单；东财没有的排除
        assert c["today_missing"] == 1 and c["no_data"] == ["BK0005"]

    def test_导出脚本里正好是这些板块(self, db, seeded):
        rep = svc.run_audit(db, now=AFTER_CLOSE, only={"sector_index"})
        js = svc.render_export_script(svc.sector_export_codes(rep), TODAY)
        assert 'const ALL = ["BK0002", "BK0003"];' in js and "20260911" in js
        assert not any(p in js for p in ("__CODES__", "__DATE__", "__COUNT__"))

    def test_只缺今天那根_收盘后归日更_盘中算齐(self, db):
        _sector(db, "BK0006")
        _bars(db, "BK0006", [d for d in DAYS[-80:] if d != TODAY])
        db.commit()
        c = svc.run_check(db, "sector_index", now=AFTER_CLOSE)
        assert c["status"] == svc.GAP and c["fix"]["kind"] == "rerun_update" and not c["items"]
        assert svc.run_check(db, "sector_index", now=INTRADAY)["status"] == svc.OK


class Test导入:
    def test_试跑不写库_确认后不覆盖已有行_空记录记下来(self, db, tmp_path):
        db.add(SectorIndexDaily(sector_code="BK0002", date=date(2026, 9, 10), close=999.0))
        db.commit()
        p = tmp_path / "x.jsonl"
        p.write_text("\n".join([
            json.dumps({"code": "BK0002", "rows": [{"date": "2026-09-09", "close": 1.0},
                                                   {"date": "2026-09-10", "close": 2.0},
                                                   {"date": "bad", "close": 1.0}]}),
            json.dumps({"code": "BK0005", "rows": []}),
            "not json",
        ]) + "\n", encoding="utf-8")
        r = imp.import_file(db, str(p), dry_run=True)
        assert r == {"sectors": 2, "added": 1, "skipped_exist": 1, "bad": 2, "no_data": ["BK0005"]}
        assert db.query(SectorIndexDaily).count() == 1 and svc.load_no_data_codes(db) == {}

        assert imp.import_file(db, str(p))["added"] == 1
        kept = db.query(SectorIndexDaily).filter_by(sector_code="BK0002", date=date(2026, 9, 10)).one()
        assert kept.close == 999.0                         # 已有行一律不覆盖
        assert "BK0005" in svc.load_no_data_codes(db)

    def test_收件箱_试跑不动文件_确认后挪到done(self, db):
        svc.INBOX_DIR.mkdir(parents=True)
        f = svc.INBOX_DIR / "sector_klines_20260912_2200.jsonl"
        f.write_text(json.dumps({"code": "BK0002", "rows": [{"date": "2026-09-09", "close": 1.0}]}) + "\n")

        def ns(**kw):
            return argparse.Namespace(**{"file": f.name, "apply": False, **kw})

        assert cli.fix_sector_index(db, ns()) == 0
        assert f.exists() and db.query(SectorIndexDaily).count() == 0
        assert cli.fix_sector_index(db, ns(apply=True)) == 0
        assert not f.exists() and (svc.INBOX_DIR / "done" / f.name).exists()
        assert db.query(SectorIndexDaily).count() == 1

    def test_收件箱以外的文件不认(self, db):
        assert cli.fix_sector_index(db, argparse.Namespace(file="../etc/passwd.jsonl", apply=False)) == 2


class Test报告:
    def test_落盘读回_副本只留最近几份_复查只替换那一项(self, db, monkeypatch):
        monkeypatch.setattr(svc, "KEEP_REPORTS", 2)
        svc.REPORT_DIR.mkdir(parents=True)
        for i in range(5):
            (svc.REPORT_DIR / f"report-20260101-00000{i}.json").write_text("{}")
        rep = svc.save_report(svc.run_audit(db, now=AFTER_CLOSE, only={"calendar", "turnover_pool"}))
        assert len(list(svc.REPORT_DIR.glob("report-*.json"))) == 2
        assert svc.load_report()["summary"] == rep["summary"]
        assert svc.INBOX_DIR.is_dir()                      # scp 之前收件箱得先在

        _turnover(db, TODAY)
        db.commit()
        svc.save_report(svc.run_audit(db, now=AFTER_CLOSE, only={"turnover_pool"}), merge=True)
        after = svc.load_report()
        assert [c["id"] for c in after["checks"]] == ["calendar", "turnover_pool"]
        assert after["checks"][1]["status"] == svc.OK and "updated_at" in after

    def test_一项查挂了不拖垮其余各项(self, db):
        # market_effect_daily 有 JSONB 列，测试库里建不了——正好当「这张表查挂了」
        rep = svc.run_audit(db, now=AFTER_CLOSE, only={"market_effect", "calendar"})
        assert {c["id"]: c["status"] for c in rep["checks"]} == {"calendar": svc.OK,
                                                                  "market_effect": svc.ERROR}
        assert rep["summary"]["todo"] == 1

    def test_一键补的清单跟命令行一致(self):
        assert set(cli.FIXERS) == set(svc.FIXABLE)
        assert set(svc.FIXABLE) <= set(svc.CHECK_IDS)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


def _auth():
    return {"Authorization": f"Bearer {create_access_token('admin')}"}


def _wait_job():
    for _ in range(300):
        if router_mod._job["status"] != "running":
            return
        time.sleep(0.01)


class Test接口:
    def test_没检测过(self, client):
        assert client.get("/admin/data-audit", headers=_auth()).json() == {"report": None}
        r = client.get("/admin/data-audit/sector-index/export-script", headers=_auth())
        assert r.status_code == 404

    def test_全部要登录_没登录的人什么都读不到(self, client):
        # 管理功能：报告里有服务器路径和 scp 登录名，读也要登录
        for method, path in [("get", ""), ("get", "/job"), ("get", "/sector-index/export-script"),
                             ("get", "/inbox"), ("post", "/run"), ("post", "/fix/index_daily")]:
            assert getattr(client, method)(f"/admin/data-audit{path}").status_code == 401, path

    def test_参数校验(self, client):
        assert client.post("/admin/data-audit/fix/calendar", headers=_auth()).status_code == 404
        r = client.post("/admin/data-audit/fix/sector_index", params={"file": "../x.jsonl"}, headers=_auth())
        assert r.status_code == 400

    def test_导出脚本用的是报告里的板块(self, db, client):
        _sector(db, "BK0002")
        _bars(db, "BK0002", DAYS[-5:])
        db.commit()
        svc.save_report(svc.run_audit(db, now=AFTER_CLOSE, only={"sector_index"}))
        r = client.get("/admin/data-audit/sector-index/export-script", headers=_auth())
        assert r.status_code == 200 and 'const ALL = ["BK0002"];' in r.text

    def test_任务在子进程里跑_试跑不带apply(self, client, monkeypatch):
        seen = []

        def fake(args, on_line=None):
            seen.append(args)
            on_line("试跑（不写库）：……")
            return 0

        monkeypatch.setattr(router_mod, "run_script_subprocess", fake)
        monkeypatch.setitem(router_mod._job, "status", "idle")
        assert client.post("/admin/data-audit/fix/index_daily", headers=_auth()).json()["ok"]
        _wait_job()
        assert router_mod._job["status"] == "done"
        assert seen == [["-m", "scripts.data_audit", "fix", "index_daily"]]
        assert router_mod._job["log_lines"] == ["试跑（不写库）：……"]

    def test_退出码3如实报什么都没动(self, client, monkeypatch):
        monkeypatch.setattr(router_mod, "run_script_subprocess", lambda args, on_line=None: 3)
        monkeypatch.setitem(router_mod._job, "status", "idle")
        client.post("/admin/data-audit/run", headers=_auth())
        _wait_job()
        assert router_mod._job["status"] == "error" and "什么都没动" in router_mod._job["message"]

    def test_日更正在跑就不起任务(self, client, monkeypatch):
        from app.routers import admin
        monkeypatch.setitem(admin._job, "status", "running")
        monkeypatch.setitem(router_mod._job, "status", "idle")
        r = client.post("/admin/data-audit/run", headers=_auth()).json()
        assert r["ok"] is False and "日更" in r["message"]
