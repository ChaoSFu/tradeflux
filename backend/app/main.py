from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .database import init_db
from .routers import stocks, sectors, signals, reviews, market_state, screening, admin, auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 启动 ──────────────────────────────────────────────────────────────
    init_db()

    # 启动内置调度器（与服务同生同死，重启自动清旧启动新）
    from .scheduler import create_scheduler
    scheduler = create_scheduler()
    scheduler.start()
    import logging
    logging.getLogger(__name__).info("✅ 内置调度器已启动（周一至周五 15:30~16:30 随机执行每日更新）")

    yield

    # ── 关闭 ──────────────────────────────────────────────────────────────
    scheduler.shutdown(wait=False)
    logging.getLogger(__name__).info("🛑 内置调度器已停止")


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


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}
