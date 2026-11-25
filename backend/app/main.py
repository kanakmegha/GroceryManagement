import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import build_async_engine, build_session_maker, get_db
from app.core.errors import RequestIdMiddleware, register_exception_handlers
from app.core.logging import configure_logging
from app.core.security import JWKSCache
from app.modules.auth.router import router as auth_router
from app.modules.cooking.meals_router import router as meals_router
from app.modules.cooking.router import router as cooking_router
from app.modules.demo.router import router as demo_router
from app.modules.discovery.router import router as discover_router
from app.modules.experimental_ocr.router import router as experimental_ocr_router
from app.modules.households.router import router as households_router
from app.modules.insights.router import router as insights_router
from app.modules.inventory.router import catalog_router
from app.modules.inventory.router import router as inventory_router
from app.modules.purchases.router import router as purchases_router
from app.modules.recipes.router import router as recipes_router
from app.modules.replenishment.router import replenishment_router, shopping_list_router


def create_app(
    settings_override: Settings | None = None,
    jwks_http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    settings = settings_override or get_settings()
    configure_logging(settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_async_engine(settings.DATABASE_URL)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_maker = build_session_maker(engine)
        app.state.jwks_cache = JWKSCache(
            jwks_url=settings.effective_jwks_url,
            ttl_seconds=settings.JWKS_CACHE_TTL_SECONDS,
            refresh_cooldown_seconds=settings.JWKS_REFRESH_COOLDOWN_SECONDS,
            http_client=jwks_http_client,
        )
        scheduler = None
        if settings.DAILY_MEAL_SCHEDULER_ENABLED and not os.environ.get("PYTEST_CURRENT_TEST"):
            from app.modules.discovery.daily_scheduler import DailyMealPlanScheduler

            scheduler = DailyMealPlanScheduler(
                app.state.session_maker,
                poll_interval_seconds=settings.DAILY_MEAL_SCHEDULER_POLL_SECONDS,
                stale_threshold_seconds=settings.DAILY_MEAL_STALE_GENERATING_SECONDS,
                lease_seconds=settings.DAILY_MEAL_LEASE_SECONDS,
                heartbeat_seconds=settings.DAILY_MEAL_HEARTBEAT_SECONDS,
            )
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                await scheduler.stop()
            await engine.dispose()

    is_production = settings.ENVIRONMENT == "production"
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    @app.get("/health", tags=["health"])
    async def health_root(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "connected"}

    api_v1 = APIRouter(prefix="/api/v1")

    @api_v1.get("/health", tags=["health"])
    async def health_v1(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "connected"}

    api_v1.include_router(auth_router)
    api_v1.include_router(households_router)
    api_v1.include_router(purchases_router)
    api_v1.include_router(inventory_router)
    api_v1.include_router(catalog_router)
    api_v1.include_router(recipes_router)
    api_v1.include_router(cooking_router)
    api_v1.include_router(meals_router)
    api_v1.include_router(insights_router)
    api_v1.include_router(replenishment_router)
    api_v1.include_router(shopping_list_router)
    api_v1.include_router(discover_router)
    api_v1.include_router(demo_router)
    app.include_router(api_v1)
    app.include_router(experimental_ocr_router)

    return app


app = create_app()
