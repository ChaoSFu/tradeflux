"""
买入检查的规则（纯函数，pretrade_v1）。

对应需求里的验收场景：C 09:45 前 / D 第 3 笔 / E 5 日内重入 / F 次核心替代 / G 风险过大，
以及几条贯穿性的纪律：UNKNOWN ≠ FAIL、结构没确认到不了 READY、没有分数。
"""
import copy
import json

from app.services import pre_trade_rules as R

AS_OF = "2026-09-11T10:05:00"
BASE_CTX = {
    "as_of": AS_OF, "mode": "HISTORICAL", "is_trading_day": True,
    "stock": {"code": "600354", "name": "敦煌种业", "is_st": False, "limit_pct": 10.0,
              "limit_up_price": 12.6, "limit_down_price": 10.31},
    "intraday": {
        "price": 11.53, "prev_close": 11.45, "open": 11.65, "high": 11.8, "low": 10.5, "vwap": 11.2,
        "pct": 0.7, "amount": 5e8, "quality": "EXACT", "source": "新浪 1 分钟K", "notes": [], "vs_market": 1.2,
        "structure": {"status": "CONFIRMED", "state": "CONFIRMED", "anchor": 11.45, "pullback_min_pct": 1.5,
                      "first_repair_at": "2026-09-11T09:45:00", "h1": 11.6, "h1_at": "2026-09-11T09:50:00",
                      "l1": 11.47, "l1_at": "2026-09-11T09:58:00", "breakout_at": "2026-09-11T10:04:00"},
    },
    "market": {
        "indexes": [{"name": "上证指数", "pct": 0.3}, {"name": "创业板指", "pct": 0.8}],
        "breadth": {"up": 3000, "down": 2000, "limit_up": 60, "limit_down": 3},
        "high_boards": [{"name": "甲", "board_prev": 5, "pct": 3.0}],
        "prev_cohort": {"median_pct": 1.2, "n": 50, "red_ratio": 0.6},
        "limit_touch": {"touched": 40},
        "prev_day": {"date": "2026-09-10", "limit_up": 55, "limit_down": 4, "max_height": 5},
    },
    "sector": {"thesis": {"id": 1, "name": "农业种植"},
               "live": {"members": 50, "up": 40, "down": 8, "median_pct": 1.5, "limit_up_now": 3, "limit_down_now": 0},
               "stock_vs_sector": 0.5, "stock_pct": 2.0, "continuation": {"prev_limit_ups": 2, "down_now": 0}},
    "leader": {"lifecycle": {"date": "2026-09-10", "state": "REPAIRING", "days_in_state": 2},
               "board_count_60d": 6, "limit_up_days_20d": 5, "pct_change_20d": 40.0, "rs_market_20": 15.0,
               "board_prev": 0, "sector_max_board_prev": 3, "limit_room_pct": 9.3, "is_st": False},
    "discipline": {"available": True, "today_buys": [], "recent_same_stock": [], "consecutive_losses": 0,
                   "holding": {"holding": False}},
}


def _ctx():
    return copy.deepcopy(BASE_CTX)


def _inp(**over):
    base = {"intended_price": 11.53, "position_pct": 10.0, "planned_stop": 11.0, "reason": "修复确认",
            "answers": {**{f"q{i}": False for i in range(1, 9)}, "q9": True, "a_plus": None,
                        "new_market_fact": "", "second_trade_note": ""},
            "account_risk_budget_pct": 1.5, "stress_loss_pct": 8.0}
    answers = over.pop("answers", {})
    base.update(over)
    base["answers"].update(answers)
    return base


def _run(ctx=None, inp=None):
    return R.evaluate(ctx or _ctx(), inp or _inp())


def _texts(res, level=None):
    return [i["text"] for m in res["modules"] for i in m["items"] if level is None or i["level"] == level]


def test_基线全部满足才是READY_而且说清楚不是买入信号():
    res = _run()
    assert res["decision"]["verdict"] == "READY", res["decision"]
    assert "不是买入信号" in res["decision"]["summary"]
    assert res["decision"]["rule_version"] == "pretrade_v1"


def test_结果里没有任何分数():
    """不做黑箱综合评分——结果里不该出现 score 这种字段。"""
    assert "score" not in json.dumps(_run(), ensure_ascii=False).lower()


# ── Case C：09:45 前 ──────────────────────────────────────────────────────────

def test_C_0935没回答A加例外_是尚未满足():
    ctx = _ctx(); ctx["as_of"] = "2026-09-11T09:35:00"
    res = _run(ctx)
    assert res["decision"]["verdict"] == "WAIT"
    assert any("A+ 例外" in i["text"] for i in res["decision"]["unmet"])


def test_C_0935没有A加例外_BLOCKED():
    ctx = _ctx(); ctx["as_of"] = "2026-09-11T09:35:00"
    assert _run(ctx, _inp(answers={"a_plus": False}))["decision"]["verdict"] == "BLOCKED"


def test_C_0935有A加例外也到不了READY():
    ctx = _ctx(); ctx["as_of"] = "2026-09-11T09:35:00"
    assert _run(ctx, _inp(answers={"a_plus": True}))["decision"]["verdict"] == "WAIT"


def test_时间纪律通过跟结构确认是两件事():
    ctx = _ctx(); ctx["intraday"]["structure"].update(status="PARTIAL", state="PULLBACK", breakout_at=None)
    res = _run(ctx)
    assert any("时间纪律通过" in t for t in _texts(res, "PASS"))
    assert res["decision"]["verdict"] == "WAIT"
    assert "结构确认还没完成" in res["decision"]["summary"]


# ── Case D：第 3 笔 ───────────────────────────────────────────────────────────

def _buy(t="2026-09-11T09:50:00", code="000001"):
    return {"trade_time": t, "stock_code": code, "stock_name": code, "action": "买入", "price": 10}


def test_D_今天已有2笔_第3笔BLOCKED():
    ctx = _ctx(); ctx["discipline"]["today_buys"] = [_buy("2026-09-11T09:50:00"), _buy("2026-09-11T09:55:00")]
    res = _run(ctx)
    assert res["decision"]["verdict"] == "BLOCKED"
    assert any("第 3 笔" in t for t in _texts(res, "FAIL"))


def test_第2笔门槛更高_没写多出的证据是尚未满足():
    ctx = _ctx(); ctx["discipline"]["today_buys"] = [_buy()]
    assert _run(ctx)["decision"]["verdict"] == "WAIT"
    assert _run(ctx, _inp(answers={"second_trade_note": "板块第二个涨停出现"}))["decision"]["verdict"] == "READY"


# ── Case E：5 日内重入 ────────────────────────────────────────────────────────

def _recent():
    return [{"trade_time": "2026-09-09T10:00:00", "action": "卖出", "price": 10.8, "stock_code": "600354"}]


def test_E_5日内重入_不写新的市场事实就BLOCKED():
    ctx = _ctx(); ctx["discipline"]["recent_same_stock"] = _recent()
    res = _run(ctx)
    assert res["decision"]["verdict"] == "BLOCKED"
    assert any("新的市场事实" in t for t in _texts(res, "FAIL"))


def test_E_写了新的市场事实就不再拦():
    ctx = _ctx(); ctx["discipline"]["recent_same_stock"] = _recent()
    assert _run(ctx, _inp(answers={"new_market_fact": "今天板块新增 3 个涨停"}))["decision"]["verdict"] == "READY"


def test_上一笔刚盈利后快速重入是高风险警告():
    ctx = _ctx(); ctx["discipline"].update(recent_same_stock=_recent(), last_same_stock_pnl=1200.0)
    res = _run(ctx, _inp(answers={"new_market_fact": "x"}))
    assert res["decision"]["verdict"] == "WAIT"
    assert any("刚在它身上盈利" in t for t in _texts(res, "WARN"))


# ── Case F：人工一票否决 ──────────────────────────────────────────────────────

def test_F_最强买不到所以买它_BLOCKED():
    res = _run(inp=_inp(answers={"q1": True}))
    assert res["decision"]["verdict"] == "BLOCKED"
    assert any("最强的那只买不到" in t for t in _texts(res, "FAIL"))


def test_没有明确失效条件_BLOCKED():
    assert _run(inp=_inp(answers={"q9": False}))["decision"]["verdict"] == "BLOCKED"


def test_没回答的问题不等于回答了否():
    res = _run(inp=_inp(answers={"q7": None}))
    assert res["decision"]["verdict"] == "WAIT"
    assert any("没回答" in i["text"] for i in res["decision"]["unmet"])


# ── Case G：风险过大 ──────────────────────────────────────────────────────────

def test_G_仓位超过风险预算上限_BLOCKED():
    """11.53 → 11.00 结构止损 4.6%，按 max(4.6%, 压力 8%) 算，1.5% 预算最多 18.75%。"""
    assert _run(inp=_inp(position_pct=30))["decision"]["verdict"] == "BLOCKED"
    assert _run(inp=_inp(position_pct=18.75))["decision"]["verdict"] == "READY"


def test_连续2笔亏损_风险预算减半():
    ctx = _ctx(); ctx["discipline"]["consecutive_losses"] = 2
    res = _run(ctx, _inp(position_pct=10))     # 减半后上限 9.375%
    assert res["decision"]["verdict"] == "BLOCKED"
    assert any("连续亏损已减半" in t for t in _texts(res, "FAIL"))


def test_连续3笔亏损_暂停主动进攻():
    ctx = _ctx(); ctx["discipline"]["consecutive_losses"] = 3
    assert any("暂停主动进攻" in t for t in _texts(_run(ctx), "FAIL"))


def test_失效价不低于买入价_不是有效的失效条件():
    assert _run(inp=_inp(planned_stop=11.6))["decision"]["verdict"] == "BLOCKED"


# ── UNKNOWN ≠ FAIL ────────────────────────────────────────────────────────────

def test_单项拿不到不拖累结论():
    """历史复盘里涨跌家数、跌停数本来就还原不了——只列出来，不能让它把结论拖成 WAIT。"""
    ctx = _ctx(); ctx["market"]["breadth"] = {"meta": {"notes": ["历史时刻无法还原"]}}
    res = _run(ctx)
    assert res["decision"]["verdict"] == "READY"
    assert any("历史时刻无法还原" in i["text"] for i in res["decision"]["unknowns"])


def test_整个模块拿不到数据_降到WAIT而不是BLOCKED():
    ctx = _ctx(); ctx["intraday"] = {"quality": "UNKNOWN", "price": None, "notes": ["超出分钟数据的回溯范围"]}
    res = _run(ctx)
    assert res["decision"]["verdict"] == "WAIT"
    assert not res["decision"]["vetoes"]


# ── 市场与板块 ────────────────────────────────────────────────────────────────

def test_系统性下跌日不开新仓():
    ctx = _ctx(); ctx["market"]["indexes"][1]["pct"] = -3.2
    assert _run(ctx)["decision"]["verdict"] == "BLOCKED"


def test_指数明显走弱是警示():
    ctx = _ctx(); ctx["market"]["indexes"][0]["pct"] = -1.6
    assert _run(ctx)["decision"]["verdict"] == "WAIT"


def test_两只涨停不等于板块赚钱效应():
    ctx = _ctx()
    ctx["sector"]["live"].update(up=12, down=30, limit_up_now=2)
    res = _run(ctx)
    assert any("不是板块赚钱效应" in t for t in _texts(res, "WARN"))
    assert res["decision"]["verdict"] == "WAIT"


def test_个股明显弱于板块是警示():
    ctx = _ctx(); ctx["sector"]["stock_vs_sector"] = -4.0
    assert any("明显弱于板块" in t for t in _texts(_run(ctx), "WARN"))


# ── 交易日历 / 拿不到的数（2026-09-11 本地联调发现） ───────────────────────────

def test_日历落后_昨天的结论站不住就不给READY():
    c = _ctx()
    c["calendar"] = {"last": "2026-09-07", "behind": True}
    c["prev_trade_date"] = "2026-09-07"
    res = _run(c)
    assert res["decision"]["verdict"] == "WAIT"
    assert any(i["key"] == "calendar" for i in res["decision"]["cautions"])


def test_盘中当天还没进日历_不算非交易日():
    c = _ctx()
    c["is_trading_day"] = None
    c["calendar"] = {"last": "2026-09-10", "behind": False}
    res = _run(c)
    assert res["decision"]["verdict"] == "READY"
    assert not any("不是交易日" in t for t in _texts(res))


def test_拿不到的数显示横杠_文案里不漏出None():
    c = _ctx()
    c["market"]["prev_day"] = {"date": "2026-09-10", "limit_up": None, "limit_down": None, "max_height": 6}
    c["sector"]["prev_day"] = {"date": "2026-09-10", "limit_up_count": None, "board_height": None}
    c["sector"]["trend"] = [{"date": "2026-09-09", "limit_up_count": None}, {"date": "2026-09-10", "limit_up_count": 3}]
    texts = _texts(_run(c))
    assert not any("None" in t for t in texts)
    assert any("? → 3" in t and "没有日快照" in t for t in texts)


def test_停牌的高标不算进反馈_但要列出来():
    c = _ctx()
    c["market"]["high_boards"] = [{"name": "甲", "board_prev": 6, "pct": None},
                                  {"name": "乙", "board_prev": 3, "pct": -8.9}]
    t = [x for x in _texts(_run(c)) if "高标" in x]
    assert t and "拿不到：甲" in t[0] and "甲(6板)" not in t[0]


# ── 2026-09-12 生产：仓位 100%、没填失效价，只得到一句「风险算不出来」 ─────────────

def test_没填失效价_仓位超过压力损失算出的上限也要BLOCKED():
    res = _run(inp=_inp(planned_stop=None, position_pct=100.0))
    assert res["decision"]["verdict"] == "BLOCKED"
    assert any(i["key"] == "position" and i["level"] == "FAIL" for i in res["decision"]["vetoes"])


def test_没填失效价_仓位在压力上限以内只提示算不准():
    res = _run(inp=_inp(planned_stop=None, position_pct=10.0))
    assert res["decision"]["verdict"] == "WAIT" and not res["decision"]["vetoes"]
