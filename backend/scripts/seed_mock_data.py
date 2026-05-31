"""
Mock data seeder — generates 30 days of realistic A-share market data.

Narrative:
  AI Chips (AI芯片):        Expansion → Euphoria (hot sector, dragon leader)
  New Energy Vehicles (新能源车): Divergence → Decline (cooling off, weak-to-strong candidates)
  Defense (军工):            Initiation → Expansion (emerging theme)
  Medical Devices (医疗器械): Stealth → Initiation (early stage)
  Consumer Electronics (消费电子): Decline → Dead Zone (exit territory)
  Energy Storage (储能):     Stealth → Initiation (early entry)

Run: cd backend && python scripts/seed_mock_data.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
from datetime import date, timedelta
from app.database import SessionLocal, init_db
from app.models.stock import Stock, StockDailySnapshot
from app.models.sector import Sector, StockSectorRelation, SectorDailySnapshot
from app.models.signal import Signal
from app.models.review import DailyReview

random.seed(42)

TODAY = date(2026, 5, 25)
START_DATE = TODAY - timedelta(days=29)


# ---------------------------------------------------------------------------
# Master data definitions
# ---------------------------------------------------------------------------

SECTORS_DATA = [
    {
        "code": "AI_CHIP",
        "name": "AI芯片",
        "description": "人工智能芯片、算力基础设施相关概念",
        "phase_trajectory": [1, 1, 2, 2, 2, 3],   # phase over 6 weeks
        "base_emotion": 75,
        "base_risk": 35,
    },
    {
        "code": "NEV",
        "name": "新能源车",
        "description": "新能源汽车整车及产业链",
        "phase_trajectory": [3, 4, 4, 5, 5, 5],
        "base_emotion": 35,
        "base_risk": 65,
    },
    {
        "code": "DEFENSE",
        "name": "军工",
        "description": "国防军工、航空航天概念",
        "phase_trajectory": [0, 1, 1, 2, 2, 2],
        "base_emotion": 60,
        "base_risk": 40,
    },
    {
        "code": "MEDICAL",
        "name": "医疗器械",
        "description": "医疗器械、诊断试剂、医疗AI",
        "phase_trajectory": [0, 0, 1, 1, 1, 1],
        "base_emotion": 45,
        "base_risk": 30,
    },
    {
        "code": "CONSUMER_ELEC",
        "name": "消费电子",
        "description": "消费电子整机及零部件",
        "phase_trajectory": [5, 5, 6, 6, 6, 6],
        "base_emotion": 15,
        "base_risk": 75,
    },
    {
        "code": "ENERGY_STORAGE",
        "name": "储能",
        "description": "储能系统、超级电容、液流电池",
        "phase_trajectory": [0, 0, 0, 1, 1, 1],
        "base_emotion": 50,
        "base_risk": 38,
    },
]

STOCKS_DATA = [
    # AI Chips — hot sector
    {"code": "688001", "name": "龙腾科技", "market": "SZ", "sector": "AI_CHIP",
     "is_leader": True, "base_price": 45.0, "volatility": 0.08,
     "limit_up_days_60d": 14, "limit_up_days_10d": 5, "board_count_60d": 6,
     "base_leader": 88, "base_risk": 30, "base_emotion": 85},
    {"code": "688002", "name": "芯联集成", "market": "SZ", "sector": "AI_CHIP",
     "is_leader": False, "is_core": True, "base_price": 28.0, "volatility": 0.07,
     "limit_up_days_60d": 11, "limit_up_days_10d": 4, "board_count_60d": 5,
     "base_leader": 72, "base_risk": 35, "base_emotion": 78},
    {"code": "688003", "name": "国芯科技", "market": "SZ", "sector": "AI_CHIP",
     "is_leader": False, "is_core": True, "base_price": 32.0, "volatility": 0.07,
     "limit_up_days_60d": 10, "limit_up_days_10d": 4, "board_count_60d": 4,
     "base_leader": 65, "base_risk": 38, "base_emotion": 72},
    {"code": "688004", "name": "智芯微电", "market": "SZ", "sector": "AI_CHIP",
     "is_compensation": True, "base_price": 18.0, "volatility": 0.06,
     "limit_up_days_60d": 6, "limit_up_days_10d": 2, "board_count_60d": 3,
     "base_leader": 48, "base_risk": 42, "base_emotion": 60},

    # New Energy Vehicles — cooling
    {"code": "002001", "name": "能源先锋", "market": "SZ", "sector": "NEV",
     "is_leader": True, "base_price": 38.0, "volatility": 0.06,
     "limit_up_days_60d": 8, "limit_up_days_10d": 1, "board_count_60d": 4,
     "base_leader": 55, "base_risk": 58, "base_emotion": 40},
    {"code": "002002", "name": "锂电之王", "market": "SZ", "sector": "NEV",
     "is_core": True, "base_price": 52.0, "volatility": 0.05,
     "limit_up_days_60d": 7, "limit_up_days_10d": 0, "board_count_60d": 3,
     "base_leader": 42, "base_risk": 65, "base_emotion": 32},
    {"code": "002003", "name": "充电科技", "market": "SZ", "sector": "NEV",
     "is_core": True, "base_price": 22.0, "volatility": 0.06,
     "limit_up_days_60d": 6, "limit_up_days_10d": 0, "board_count_60d": 3,
     "base_leader": 38, "base_risk": 68, "base_emotion": 28},
    {"code": "002004", "name": "新能智联", "market": "SZ", "sector": "NEV",
     "is_compensation": True, "base_price": 15.0, "volatility": 0.05,
     "limit_up_days_60d": 4, "limit_up_days_10d": 0, "board_count_60d": 2,
     "base_leader": 22, "base_risk": 72, "base_emotion": 20},

    # Defense — emerging
    {"code": "600011", "name": "战鹰航空", "market": "SH", "sector": "DEFENSE",
     "is_leader": True, "base_price": 42.0, "volatility": 0.07,
     "limit_up_days_60d": 10, "limit_up_days_10d": 4, "board_count_60d": 5,
     "base_leader": 76, "base_risk": 36, "base_emotion": 72},
    {"code": "600012", "name": "导弹精工", "market": "SH", "sector": "DEFENSE",
     "is_core": True, "base_price": 29.0, "volatility": 0.07,
     "limit_up_days_60d": 8, "limit_up_days_10d": 3, "board_count_60d": 4,
     "base_leader": 60, "base_risk": 40, "base_emotion": 65},
    {"code": "600013", "name": "军工新材", "market": "SH", "sector": "DEFENSE",
     "is_compensation": True, "base_price": 19.0, "volatility": 0.06,
     "limit_up_days_60d": 5, "limit_up_days_10d": 2, "board_count_60d": 3,
     "base_leader": 44, "base_risk": 44, "base_emotion": 55},

    # Medical Devices — early stage
    {"code": "300001", "name": "医疗先驱", "market": "SZ", "sector": "MEDICAL",
     "is_leader": True, "base_price": 35.0, "volatility": 0.05,
     "limit_up_days_60d": 5, "limit_up_days_10d": 2, "board_count_60d": 3,
     "base_leader": 55, "base_risk": 32, "base_emotion": 58},
    {"code": "300002", "name": "智影医疗", "market": "SZ", "sector": "MEDICAL",
     "is_core": True, "base_price": 24.0, "volatility": 0.05,
     "limit_up_days_60d": 3, "limit_up_days_10d": 1, "board_count_60d": 2,
     "base_leader": 38, "base_risk": 28, "base_emotion": 48},
    {"code": "300003", "name": "基因科技", "market": "SZ", "sector": "MEDICAL",
     "is_compensation": True, "base_price": 18.0, "volatility": 0.05,
     "limit_up_days_60d": 2, "limit_up_days_10d": 1, "board_count_60d": 1,
     "base_leader": 25, "base_risk": 25, "base_emotion": 38},

    # Consumer Electronics — declining
    {"code": "002011", "name": "苹果配套", "market": "SZ", "sector": "CONSUMER_ELEC",
     "is_leader": True, "base_price": 24.0, "volatility": 0.04,
     "limit_up_days_60d": 2, "limit_up_days_10d": 0, "board_count_60d": 1,
     "base_leader": 18, "base_risk": 72, "base_emotion": 15},
    {"code": "002012", "name": "屏幕科技", "market": "SZ", "sector": "CONSUMER_ELEC",
     "is_core": True, "base_price": 16.0, "volatility": 0.04,
     "limit_up_days_60d": 1, "limit_up_days_10d": 0, "board_count_60d": 1,
     "base_leader": 12, "base_risk": 78, "base_emotion": 10},
    {"code": "002013", "name": "精密制造", "market": "SZ", "sector": "CONSUMER_ELEC",
     "is_compensation": True, "base_price": 12.0, "volatility": 0.04,
     "limit_up_days_60d": 1, "limit_up_days_10d": 0, "board_count_60d": 0,
     "base_leader": 8, "base_risk": 82, "base_emotion": 8},

    # Energy Storage — early
    {"code": "300011", "name": "储能龙头", "market": "SZ", "sector": "ENERGY_STORAGE",
     "is_leader": True, "base_price": 31.0, "volatility": 0.06,
     "limit_up_days_60d": 6, "limit_up_days_10d": 3, "board_count_60d": 4,
     "base_leader": 62, "base_risk": 38, "base_emotion": 62},
    {"code": "300012", "name": "超级电容", "market": "SZ", "sector": "ENERGY_STORAGE",
     "is_core": True, "base_price": 22.0, "volatility": 0.06,
     "limit_up_days_60d": 4, "limit_up_days_10d": 2, "board_count_60d": 3,
     "base_leader": 48, "base_risk": 40, "base_emotion": 52},
    {"code": "300013", "name": "光伏储能", "market": "SZ", "sector": "ENERGY_STORAGE",
     "is_compensation": True, "base_price": 17.0, "volatility": 0.05,
     "limit_up_days_60d": 3, "limit_up_days_10d": 1, "board_count_60d": 2,
     "base_leader": 32, "base_risk": 35, "base_emotion": 45},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def trading_days(start: date, end: date) -> list[date]:
    """Return weekdays only (simplified — ignores holidays)."""
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def random_walk(base: float, days: int, vol: float) -> list[float]:
    prices = [base]
    for _ in range(days - 1):
        change = random.gauss(0, vol)
        prices.append(max(0.5, prices[-1] * (1 + change)))
    return prices


def simulate_limit_ups(n_days: int, target_lu_days: int, vol: float) -> list[float]:
    """Generate daily pct_changes with realistic limit-up distribution."""
    changes = []
    lu_remaining = target_lu_days
    for i in range(n_days):
        prob_lu = lu_remaining / max(1, n_days - i) * 1.5
        if lu_remaining > 0 and random.random() < min(0.6, prob_lu):
            changes.append(10.0)
            lu_remaining -= 1
        else:
            changes.append(random.gauss(0.5, vol * 100))
    return changes


# ---------------------------------------------------------------------------
# Seeder
# ---------------------------------------------------------------------------

def seed():
    init_db()
    db = SessionLocal()

    # Clear existing data
    db.query(Signal).delete()
    db.query(DailyReview).delete()
    db.query(SectorDailySnapshot).delete()
    db.query(StockDailySnapshot).delete()
    db.query(StockSectorRelation).delete()
    db.query(Sector).delete()
    db.query(Stock).delete()
    db.commit()

    t_days = trading_days(START_DATE, TODAY)
    n = len(t_days)

    # ---- Sectors ----
    sector_objs: dict[str, Sector] = {}
    for sd in SECTORS_DATA:
        # Current phase = last in trajectory
        traj = sd["phase_trajectory"]
        cur_phase = traj[-1]
        # Compute current metrics from phase
        emotion = sd["base_emotion"] + random.uniform(-5, 5)
        risk = sd["base_risk"] + random.uniform(-5, 5)
        continuity = max(0, min(100, 70 - cur_phase * 10 + random.uniform(-5, 5)))
        sector = Sector(
            code=sd["code"],
            name=sd["name"],
            description=sd["description"],
            phase=cur_phase,
            strong_stock_count=0,  # updated after stocks
            limit_up_count=0,
            board_height=max(0, 5 - cur_phase) + random.randint(0, 2),
            continuity_score=round(continuity, 1),
            risk_score=round(risk, 1),
            emotion_score=round(emotion, 1),
        )
        db.add(sector)
        db.flush()
        sector_objs[sd["code"]] = sector

    # ---- Stocks ----
    stock_objs: dict[str, Stock] = {}
    for sd in STOCKS_DATA:
        sector_code = sd["sector"]
        lu_60d = sd["limit_up_days_60d"]
        lu_10d = sd["limit_up_days_10d"]
        bc_60d = sd["board_count_60d"]
        in_pool = bc_60d > 3 or lu_60d > 9 or lu_10d > 4

        stock = Stock(
            code=sd["code"],
            name=sd["name"],
            market=sd["market"],
            in_strong_pool=in_pool,
            phase=_phase_from_sector(sector_code, SECTORS_DATA),
            leader_score=round(sd["base_leader"] + random.uniform(-3, 3), 1),
            risk_score=round(sd["base_risk"] + random.uniform(-3, 3), 1),
            emotion_score=round(sd["base_emotion"] + random.uniform(-3, 3), 1),
            board_count_60d=bc_60d,
            limit_up_days_60d=lu_60d,
            limit_up_days_10d=lu_10d,
            top_10_pct_change_20d=(sd.get("base_leader", 0) >= 60),
        )
        db.add(stock)
        db.flush()
        stock_objs[sd["code"]] = stock

        # Sector relation
        rel = StockSectorRelation(
            stock_id=stock.id,
            sector_id=sector_objs[sector_code].id,
            is_leader=sd.get("is_leader", False),
            is_core=sd.get("is_core", False),
            is_compensation=sd.get("is_compensation", False),
        )
        db.add(rel)

    # Update sector leader references
    for sd in STOCKS_DATA:
        if sd.get("is_leader"):
            sector = sector_objs[sd["sector"]]
            sector.leader_stock_id = stock_objs[sd["code"]].id

    # Update sector strong_stock_count
    for skey, sector in sector_objs.items():
        count = sum(
            1 for s in STOCKS_DATA
            if s["sector"] == skey and stock_objs[s["code"]].in_strong_pool
        )
        sector.strong_stock_count = count
        sector.limit_up_count = max(0, count - random.randint(0, 2))

    db.flush()

    # ---- Stock daily snapshots ----
    for sd in STOCKS_DATA:
        stock = stock_objs[sd["code"]]
        sector_def = next(x for x in SECTORS_DATA if x["code"] == sd["sector"])
        traj = sector_def["phase_trajectory"]

        changes = simulate_limit_ups(n, sd["limit_up_days_60d"], sd["volatility"])
        prices = [sd["base_price"]]
        for c in changes[1:]:
            prices.append(max(0.5, prices[-1] * (1 + c / 100)))

        board_count = 0
        max_board_60d = 0

        for i, d in enumerate(t_days):
            pct = changes[i]
            is_lu = abs(pct - 10.0) < 0.1
            is_ld = pct < -9.5
            is_broken = (
                board_count >= 2
                and not is_lu
                and pct < -2
                and random.random() < 0.25
            )

            if is_lu:
                board_count += 1
            elif not is_lu:
                board_count = 0

            max_board_60d = max(max_board_60d, board_count)

            week = min(5, i * 6 // n)
            phase = traj[week] if week < len(traj) else traj[-1]

            leader = sd["base_leader"] + random.uniform(-5, 5) + (i * 0.3 if sd.get("is_leader") else 0)
            risk = sd["base_risk"] + random.uniform(-4, 4) + (i * 0.2 if phase >= 4 else 0)
            emotion = sd["base_emotion"] + random.uniform(-6, 6)

            # Weak-to-strong flag: broken board followed by strong rebound
            prev_snap = None
            if i > 0:
                prev_snap = i > 1 and changes[i - 1] < -3
            is_w2s = bool(prev_snap and pct > 6)

            snap = StockDailySnapshot(
                stock_id=stock.id,
                date=d,
                open_price=round(prices[i] * (1 + random.uniform(-0.01, 0.01)), 2),
                close_price=round(prices[i], 2),
                high_price=round(prices[i] * (1 + abs(random.uniform(0, 0.02))), 2),
                low_price=round(prices[i] * (1 - abs(random.uniform(0, 0.02))), 2),
                volume=round(random.uniform(5, 80) * (1.5 if is_lu else 1.0), 2),
                turnover_rate=round(random.uniform(2, 15) * (1.5 if is_lu else 1.0), 2),
                pct_change=round(pct, 2),
                is_limit_up=is_lu,
                is_limit_down=is_ld,
                is_broken_board=is_broken,
                board_count=board_count,
                board_count_60d=max_board_60d,
                limit_up_days_60d=sd["limit_up_days_60d"],
                limit_up_days_10d=sd["limit_up_days_10d"],
                top_10_pct_change_20d=(sd.get("base_leader", 0) >= 60),
                phase=_phase_name(phase),
                leader_score=round(min(100, max(0, leader)), 1),
                risk_score=round(min(100, max(0, risk)), 1),
                emotion_score=round(min(100, max(0, emotion)), 1),
                is_weak_to_strong=is_w2s,
            )
            db.add(snap)

    # ---- Sector daily snapshots ----
    for sd_def in SECTORS_DATA:
        sector = sector_objs[sd_def["code"]]
        traj = sd_def["phase_trajectory"]
        for i, d in enumerate(t_days):
            week = min(5, i * 6 // n)
            phase = traj[week] if week < len(traj) else traj[-1]
            emotion = sd_def["base_emotion"] + random.uniform(-8, 8)
            risk = sd_def["base_risk"] + random.uniform(-5, 5) + (i * 0.3 if phase >= 4 else 0)
            continuity = max(0, 80 - phase * 12 + random.uniform(-5, 5))
            snap = SectorDailySnapshot(
                sector_id=sector.id,
                date=d,
                phase=phase,
                strong_stock_count=max(0, sector.strong_stock_count + random.randint(-1, 1)),
                limit_up_count=max(0, min(4, phase - 1 + random.randint(-1, 2))),
                board_height=max(0, 5 - phase + random.randint(0, 2)),
                continuity_score=round(continuity, 1),
                risk_score=round(min(100, max(0, risk)), 1),
                emotion_score=round(min(100, max(0, emotion)), 1),
                leader_stock_id=sector.leader_stock_id,
            )
            db.add(snap)

    db.flush()

    # ---- Signals ----
    _seed_signals(db, stock_objs, sector_objs, t_days)

    # ---- Daily Reviews ----
    _seed_reviews(db, t_days, SECTORS_DATA)

    db.commit()
    print(f"Seeded: {len(STOCKS_DATA)} stocks, {len(SECTORS_DATA)} sectors, "
          f"{len(t_days)} trading days of snapshots.")
    db.close()


def _phase_from_sector(sector_code: str, sectors_data: list) -> str:
    sd = next((x for x in sectors_data if x["code"] == sector_code), None)
    if not sd:
        return "stealth"
    return _phase_name(sd["phase_trajectory"][-1])


def _phase_name(phase: int) -> str:
    return {0: "stealth", 1: "initiation", 2: "expansion",
            3: "euphoria", 4: "divergence", 5: "decline", 6: "dead_zone"}.get(phase, "stealth")


def _seed_signals(db, stock_objs, sector_objs, t_days):
    signal_templates = [
        # Broken board recovery — NEV sector (was hot, now recovering)
        {"stock_code": "002001", "sector_code": "NEV",
         "signal_type": "broken_board_recovery", "confidence_score": 72.0,
         "risk_level": "medium", "suggested_action": "watchlist",
         "explanation": "能源先锋 炸板后连续两日收复涨停价附近，量能温和放大，或为短线修复机会。"},
        {"stock_code": "002002", "sector_code": "NEV",
         "signal_type": "divergence_repair", "confidence_score": 58.0,
         "risk_level": "medium", "suggested_action": "observe",
         "explanation": "锂电之王 板块分歧期中相对抗跌，情绪修复节奏较同类股领先。"},
        # Weak-to-strong — AI chips
        {"stock_code": "688004", "sector_code": "AI_CHIP",
         "signal_type": "rebound_acceleration", "confidence_score": 65.0,
         "risk_level": "low", "suggested_action": "low_position_trial",
         "explanation": "智芯微电 板块扩张期中反弹加速，属典型补涨逻辑，注意量能配合。"},
        # Energy storage emerging
        {"stock_code": "300011", "sector_code": "ENERGY_STORAGE",
         "signal_type": "weak_to_strong", "confidence_score": 68.0,
         "risk_level": "low", "suggested_action": "watchlist",
         "explanation": "储能龙头 在板块启动初期率先放量，情绪得分领跑板块，关注后续连板高度。"},
        {"stock_code": "300012", "sector_code": "ENERGY_STORAGE",
         "signal_type": "sector_repair_sync", "confidence_score": 55.0,
         "risk_level": "low", "suggested_action": "observe",
         "explanation": "超级电容 随板块修复同步回升，属板块第二梯队潜在补涨标的。"},
        # Defense
        {"stock_code": "600013", "sector_code": "DEFENSE",
         "signal_type": "rebound_acceleration", "confidence_score": 60.0,
         "risk_level": "medium", "suggested_action": "watchlist",
         "explanation": "军工新材 军工板块扩张期补涨，涨幅开始超越板块均值，关注持续性。"},
        # Medical early stage
        {"stock_code": "300001", "sector_code": "MEDICAL",
         "signal_type": "weak_to_strong", "confidence_score": 52.0,
         "risk_level": "low", "suggested_action": "observe",
         "explanation": "医疗先驱 医疗器械板块刚进启动期，龙头情绪率先回升，可列入观察。"},
    ]

    recent_date = t_days[-1]
    for tmpl in signal_templates:
        stock = stock_objs.get(tmpl["stock_code"])
        sector = sector_objs.get(tmpl["sector_code"])
        if not stock or not sector:
            continue
        sig = Signal(
            stock_id=stock.id,
            sector_id=sector.id,
            date=recent_date,
            signal_type=tmpl["signal_type"],
            confidence_score=tmpl["confidence_score"],
            risk_level=tmpl["risk_level"],
            explanation=tmpl["explanation"],
            suggested_action=tmpl["suggested_action"],
            is_active=True,
        )
        db.add(sig)


MARKET_PHASES = [
    "caution", "caution", "neutral", "neutral", "warm",
    "warm", "neutral", "warm", "bull_frenzy", "warm",
    "warm", "neutral", "neutral", "caution", "neutral",
    "warm", "warm", "warm", "bull_frenzy", "bull_frenzy",
    "warm", "warm", "neutral", "neutral", "warm",
    "warm", "warm", "bull_frenzy", "warm", "warm",
]


def _seed_reviews(db, t_days, sectors_data):
    strong_sector_names = ["AI芯片", "军工", "储能"]
    danger_sector_names = ["消费电子", "新能源车"]
    watchlist = ["688001", "600011", "300011", "688002", "300001"]

    for i, d in enumerate(t_days):
        phase = MARKET_PHASES[i % len(MARKET_PHASES)]
        temp = {"bull_frenzy": 82, "warm": 65, "neutral": 50, "caution": 32, "bear_fear": 15}[phase]
        profit = temp * 0.9 + random.uniform(-5, 5)
        loss = (100 - temp) * 0.6 + random.uniform(-5, 5)

        summary = (
            f"【{d} 市场复盘】"
            f"今日市场{'偏强' if temp > 55 else '偏弱'}，情绪温度 {temp:.0f}。"
            f"赚钱效应 {profit:.0f}，亏钱效应 {loss:.0f}。"
            f"重点关注 {'、'.join(strong_sector_names[:2])} 板块延续性。"
            f"⚠️ 仅供辅助分析，不构成投资建议。"
        )
        review = DailyReview(
            date=d,
            market_phase=phase,
            profit_effect_score=round(min(100, max(0, profit)), 1),
            loss_effect_score=round(min(100, max(0, loss)), 1),
            emotion_cycle=_emotion_cycle(temp),
            emotional_temperature=round(temp + random.uniform(-3, 3), 1),
            suggested_position_level=round(max(5, temp * 0.7), 1),
            strong_sectors=strong_sector_names,
            dangerous_sectors=danger_sector_names,
            active_sectors=["AI芯片", "军工"],
            dragon_changes=[{"type": "new_leader", "stock": "龙腾科技", "sector": "AI芯片"}],
            tomorrow_watchlist=watchlist[:3],
            market_summary=summary,
        )
        db.add(review)


def _emotion_cycle(temp: float) -> str:
    if temp >= 80:
        return "euphoric"
    if temp >= 65:
        return "heating"
    if temp >= 50:
        return "awakening"
    if temp >= 35:
        return "cooling"
    return "dormant"


if __name__ == "__main__":
    seed()
