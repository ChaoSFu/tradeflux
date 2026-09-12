from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .database import init_db
from .routers import stocks, sectors, reviews, market_state, screening, admin, auth, watchlist, market_index, trade_journal, market_effects, turnover, weak_to_strong_radar, limit_up_radar, speculation_radar, leader_cycle, pre_trade_check


_scheduler = None  # 全局暴露，供 admin router 查询状态


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    # ── 启动 ──────────────────────────────────────────────────────────────
    init_db()

    # 买入检查的实战日志：backend/logs/pre_trade_check.log（一次取数一行各路耗时和数据质量）
    from .services.pre_trade_check_service import setup_file_log
    setup_file_log()

    # 启动内置调度器（与服务同生同死，重启自动清旧启动新）
    from .scheduler import create_scheduler
    _scheduler = create_scheduler()
    _scheduler.start()
    next_run = _scheduler.get_job("daily_update").next_run_time
    print(f"[SCHED] ✅ 内置调度器已启动，下次执行时间：{next_run}", flush=True)

    # 内存心跳：中间件只看得到请求，后台刷新线程涨的内存要靠它才看得见
    import asyncio
    from .rss_probe import rss_heartbeat
    _hb = asyncio.create_task(rss_heartbeat())

    yield

    _hb.cancel()

    # ── 关闭 ──────────────────────────────────────────────────────────────
    _scheduler.shutdown(wait=False)
    _scheduler = None
    print("[SCHED] 🛑 内置调度器已停止", flush=True)


app = FastAPI(
    title="TradeFlux API",
    description="短线晴雨表 — AI-native market state intelligence",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── 内存归因探针 ────────────────────────────────────────────────────────
# 从外面采样猜了四次都错（见 rss_probe.py 的模块注释）。让进程自己说：
# 哪个请求让 RSS 涨了，就把哪个路径打进日志
from .rss_probe import RssProbeMiddleware  # noqa: E402

app.add_middleware(RssProbeMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks.router, prefix=settings.API_PREFIX)
app.include_router(sectors.router, prefix=settings.API_PREFIX)
app.include_router(reviews.router, prefix=settings.API_PREFIX)
app.include_router(market_state.router, prefix=settings.API_PREFIX)
app.include_router(screening.router, prefix=settings.API_PREFIX)
app.include_router(admin.router, prefix=settings.API_PREFIX)
app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(watchlist.router, prefix=settings.API_PREFIX)
app.include_router(market_index.router, prefix=settings.API_PREFIX)
app.include_router(trade_journal.router, prefix=settings.API_PREFIX)
app.include_router(market_effects.router, prefix=settings.API_PREFIX)
app.include_router(turnover.router, prefix=settings.API_PREFIX)
app.include_router(weak_to_strong_radar.router, prefix=settings.API_PREFIX)
app.include_router(limit_up_radar.router, prefix=settings.API_PREFIX)
app.include_router(speculation_radar.router, prefix=settings.API_PREFIX)
app.include_router(leader_cycle.router, prefix=settings.API_PREFIX)
app.include_router(pre_trade_check.router, prefix=settings.API_PREFIX)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}
