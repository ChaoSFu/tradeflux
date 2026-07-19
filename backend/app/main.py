from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .database import init_db
from .routers import stocks, sectors, signals, reviews, market_state, screening, admin, auth, watchlist, market_index


_scheduler = None  # 全局暴露，供 admin router 查询状态


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    # ── 启动 ──────────────────────────────────────────────────────────────
    init_db()

    # 启动内置调度器（与服务同生同死，重启自动清旧启动新）
    from .scheduler import create_scheduler
    _scheduler = create_scheduler()
    _scheduler.start()
    next_run = _scheduler.get_job("daily_update").next_run_time
    print(f"[SCHED] ✅ 内置调度器已启动，下次执行时间：{next_run}", flush=True)

    yield

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks.router, prefix=settings.API_PREFIX)
app.include_router(sectors.router, prefix=settings.API_PREFIX)
app.include_router(signals.router, prefix=settings.API_PREFIX)
app.include_router(reviews.router, prefix=settings.API_PREFIX)
app.include_router(market_state.router, prefix=settings.API_PREFIX)
app.include_router(screening.router, prefix=settings.API_PREFIX)
app.include_router(admin.router, prefix=settings.API_PREFIX)
app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(watchlist.router, prefix=settings.API_PREFIX)
app.include_router(market_index.router, prefix=settings.API_PREFIX)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}
