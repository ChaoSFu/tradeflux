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
                        "new_market_fact": "", "second_trade_note": "",
                        "why_sector": "农业主线", "why_stock": "板块内最早修复", "why_now": "回踩不破后再突破 H1",
                        "invalidation_type": "price", "invalidation_text": ""},
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
    assert res["decision"]["rule_version"] == "pretrade_v3"


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
    assert any("最强的那只现在能买" in t for t in _texts(res, "FAIL"))   # v2 换成反事实问法


def test_没写失效条件_到不了READY():
    res = _run(inp=_inp(planned_stop=None, answers={"invalidation_type": None}))
    assert res["decision"]["verdict"] == "WAIT"
    assert any(i["key"] == "invalidation" for i in res["decision"]["unmet"])


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
    ctx = _ctx(); ctx["market"]["indexes"][0]["pct"] = -3.2       # 上证：主板票自己的盘子
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


# ── pretrade_v2（2026-09-12 生产试用 + 评审）──────────────────────────────────────

def _dims(res):
    return {d["key"]: d["verdict"] for d in res["decision"]["dimensions"]}


def test_基线三个维度都是READY():
    assert set(_dims(_run()).values()) == {"READY"}


def test_三个维度分开给结论_总体取最严():
    ctx = _ctx()
    ctx["intraday"]["structure"].update(state="REPAIRING", status="PARTIAL", l1=None, breakout_at=None)
    ans = {f"q{i}": True for i in (1, 2, 4, 7, 8)}
    res = _run(ctx, _inp(planned_stop=None, position_pct=100.0, answers=ans))
    assert _dims(res) == {"setup": "WAIT", "execution": "BLOCKED", "risk": "BLOCKED"}
    assert res["decision"]["verdict"] == "BLOCKED"
    assert "客观交易条件本身是 WAIT" in res["decision"]["summary"]


def test_失效条件可以不是价格_仓位按压力损失卡():
    res = _run(inp=_inp(planned_stop=None, position_pct=10.0,
                        answers={"invalidation_type": "structure", "invalidation_text": "跌回分时均价下方"}))
    assert res["decision"]["verdict"] == "READY"
    assert any("≤ 上限 18.8%" in t for t in _texts(res, "PASS"))


def test_选了结构失效但没写具体条件_到不了READY():
    res = _run(inp=_inp(planned_stop=None, position_pct=10.0, answers={"invalidation_type": "structure"}))
    assert res["decision"]["verdict"] == "WAIT"


def test_买入理由没写全_到不了READY():
    res = _run(inp=_inp(answers={"why_now": ""}))
    assert res["decision"]["verdict"] == "WAIT"
    assert any("为什么是现在" in i["text"] for i in res["decision"]["unmet"])


def test_主板票不被创业板指单独否决():
    ctx = _ctx()
    ctx["market"]["indexes"] = [{"name": "上证指数", "pct": -0.4}, {"name": "深证成指", "pct": -0.8},
                                {"name": "创业板指", "pct": -3.4}]
    res = _run(ctx)
    assert res["decision"]["verdict"] == "WAIT"
    assert any("情绪可能传导" in t for t in _texts(res, "WARN"))


def test_创业板票看创业板指():
    ctx = _ctx()
    ctx["stock"]["code"] = "300750"
    ctx["market"]["indexes"] = [{"name": "上证指数", "pct": -0.4}, {"name": "创业板指", "pct": -3.4}]
    assert _run(ctx)["decision"]["verdict"] == "BLOCKED"


def test_第一波还在走_H1只是候选():
    ctx = _ctx()
    ctx["intraday"]["structure"].update(state="REPAIRING", status="PARTIAL", l1=None, breakout_at=None)
    t = [x for x in _texts(_run(ctx)) if "H1" in x]
    assert any("H1 还没确认" in x for x in t) and not any("H1 已确认" in x for x in t)


def test_板块内排名_告诉你谁比它强():
    ctx = _ctx()
    ctx["sector"]["live"]["ranking"] = {"top": [{"code": "600371", "name": "万向德农", "pct": 9.8}],
                                        "rank": 8, "total": 50, "leader_gap": 9.1}
    assert any("排第 8 / 50" in t and "落后领涨 9.1" in t for t in _texts(_run(ctx)))


def test_20日跑输板块要警惕():
    ctx = _ctx()
    ctx["leader"]["rs_sector_20"] = -6.0
    assert any("跑输板块" in t for t in _texts(_run(ctx), "WARN"))


def test_不属于高标二波要说清楚():
    ctx = _ctx()
    ctx["leader"]["lifecycle"] = None
    ctx["leader"]["board_count_60d"] = 3
    assert any("不属于「高标二波」" in t and "60 日最高 3 板" in t for t in _texts(_run(ctx)))


# ── pretrade_v3（2026-09-12）：点选的结构化计划 ─────────────────────────────────

def _plan(**over):
    """只点选、一个字不打的计划。"""
    ans = {"why_sector": "", "why_stock": "", "why_now": "", "invalidation_type": None, "invalidation_text": "",
           "sector_reason_codes": ["MAINLINE_STRENGTHENING"], "stock_reason_codes": ["INDEPENDENT_SETUP"],
           "entry_trigger_code": "SECOND_BREAKOUT_AFTER_L1", "invalidation_codes": ["BREAK_L1"],
           "sector_reason_other": "", "stock_reason_other": "", "entry_trigger_other": "", "invalidation_other": ""}
    ans.update(over)
    return _inp(planned_stop=None, answers=ans)


def _unmet_keys(res):
    return {i["key"] for i in res["decision"]["unmet"]}


def test_只点选不打字_计划完整就能READY():
    res = _run(inp=_plan())
    assert res["decision"]["verdict"] == "READY", res["decision"]["summary"]


def test_没选板块理由_到不了READY():
    res = _run(inp=_plan(sector_reason_codes=[]))
    assert res["decision"]["verdict"] == "WAIT" and "plan_sector" in _unmet_keys(res)


def test_没选个股理由_到不了READY():
    res = _run(inp=_plan(stock_reason_codes=[]))
    assert res["decision"]["verdict"] == "WAIT" and "plan_stock" in _unmet_keys(res)


def test_没选入场触发_到不了READY():
    res = _run(inp=_plan(entry_trigger_code=None))
    assert res["decision"]["verdict"] == "WAIT" and "plan_trigger" in _unmet_keys(res)


def test_没选失效条件_到不了READY():
    res = _run(inp=_plan(invalidation_codes=[]))
    assert res["decision"]["verdict"] == "WAIT" and "invalidation" in _unmet_keys(res)


def test_选了其他但没写是什么_到不了READY_写了就行():
    assert _run(inp=_plan(stock_reason_codes=["OTHER"]))["decision"]["verdict"] == "WAIT"
    assert _run(inp=_plan(stock_reason_codes=["OTHER"], stock_reason_other="龙头换手"))["decision"]["verdict"] == "READY"
    assert _run(inp=_plan(invalidation_codes=["CUSTOM"]))["decision"]["verdict"] == "WAIT"


def test_系统只标事实_不替你选():
    opts = R.plan_options(_ctx())
    trig = {o["code"]: o for o in opts["trigger"]["options"]}
    assert trig["SECOND_BREAKOUT_AFTER_L1"]["suggested"] is True
    assert all("selected" not in o for g in opts.values() for o in g["options"])
    assert "plan_trigger" in _unmet_keys(_run(inp=_plan(entry_trigger_code=None))), "有事实支持也不会被自动选上"


def test_选了二波核心_但60日最高只有3连板_明确冲突():
    ctx = _ctx()
    ctx["leader"]["lifecycle"] = None
    ctx["leader"]["board_count_60d"] = 3
    res = _run(ctx, _plan(stock_reason_codes=["HISTORICAL_HIGH_LEADER"]))
    assert res["decision"]["verdict"] == "BLOCKED"
    assert any("60 日最高只有 3 连板" in i["text"] for i in res["decision"]["vetoes"])
    assert {o["code"]: o for o in R.plan_options(ctx)["stock"]["options"]}["HISTORICAL_HIGH_LEADER"]["conflict_reason"]


def test_选了二次突破_但第一波还在形成_明确冲突():
    ctx = _ctx()
    ctx["intraday"]["structure"].update(state="REPAIRING", status="PARTIAL", l1=None, breakout_at=None)
    res = _run(ctx, _plan(invalidation_codes=["SECTOR_WEAKENING"]))
    assert any("第一波还在形成，H1 尚未确认" in i["text"] for i in res["decision"]["vetoes"])


def test_数据拿不到时不制造冲突():
    ctx = _ctx()
    ctx["sector"]["stock_vs_sector"] = None
    res = _run(ctx, _plan(stock_reason_codes=["OUTPERFORM_SECTOR"]))
    assert res["decision"]["verdict"] != "BLOCKED"
    assert any("强于板块" in i["text"] and "没法核对" in i["text"] for i in res["decision"]["unknowns"])


def test_计划摘要是人能看懂的():
    s = R.plan_summary(_ctx(), _plan(sector_reason_codes=["MAINLINE_STRENGTHENING", "HIGH_BOARD_ADVANCING"],
                                     invalidation_codes=["BREAK_L1", "SECTOR_WEAKENING"]))
    assert s == "板块：主线加强 / 高标晋级｜个股：独立Setup｜时机：二次突破｜失效：跌破L1 11.47 / 板块转弱"


def test_选了跌破VWAP_没填失效价_用VWAP算风险():
    res = _run(inp=_plan(invalidation_codes=["BREAK_VWAP"]))
    assert res["decision"]["verdict"] == "READY"
    assert any("按「跌破VWAP」11.2" in t for t in _texts(res))


def test_失效条件此刻就已成立_直接否决():
    ctx = _ctx()
    ctx["intraday"]["vwap"] = 11.6
    res = _run(ctx, _plan(invalidation_codes=["BREAK_VWAP"]))
    assert any("现在就成立" in i["text"] for i in res["decision"]["vetoes"])


def test_还没有L1时_跌破L1不能当失效条件():
    ctx = _ctx()
    ctx["intraday"]["structure"].update(state="REPAIRING", status="PARTIAL", l1=None, breakout_at=None)
    opt = {o["code"]: o for o in R.plan_options(ctx)["invalidation"]["options"]}["BREAK_L1"]
    assert opt["available"] is False and opt["ref_price"] is None
    res = _run(ctx, _plan(entry_trigger_code="VWAP_PULLBACK_REATTACK"))
    assert "invalidation" in _unmet_keys(res)


def test_1到2_用昨日板数和触板核对():
    ctx = _ctx()
    assert _run(ctx, _plan(entry_trigger_code="ONE_TO_TWO_CONFIRM"))["decision"]["verdict"] == "BLOCKED"   # 昨日 0 板
    ctx["leader"]["board_prev"] = 1
    ctx["market"]["limit_touch"] = {"touched": 40, "codes": ["600354"]}
    trig = {o["code"]: o for o in R.plan_options(ctx)["trigger"]["options"]}
    assert trig["ONE_TO_TWO_CONFIRM"]["suggested"] is True and trig["TWO_TO_THREE_CONFIRM"]["conflict_reason"]


def test_第2笔和快速重入_点选就行_其他要写():
    ctx = _ctx()
    ctx["discipline"]["today_buys"] = [{"trade_time": "2026-09-11T09:31:00", "stock_code": "000001", "stock_name": "甲"}]
    ctx["discipline"]["recent_same_stock"] = [{"trade_time": "2026-09-09T10:00:00", "action": "卖出", "price": 11.0}]
    ok = _run(ctx, _plan(second_trade_codes=["BETTER_SETUP"], reentry_fact_codes=["SECTOR_RESTRENGTHENED"]))
    assert ok["decision"]["verdict"] != "BLOCKED" and "trade_count" not in _unmet_keys(ok)
    bad = _run(ctx, _plan(second_trade_codes=["BETTER_SETUP"], reentry_fact_codes=["OTHER"]))
    assert any(i["key"] == "reentry" for i in bad["decision"]["vetoes"])
