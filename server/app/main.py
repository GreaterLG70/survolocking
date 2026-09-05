"""Survolocking 云端服务入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, family, logs, notify, qiling, rules
from app.config import settings
from app.database import get_engine, init_db, is_sqlite_fallback

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
)
logger = logging.getLogger("survolocking")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Survolocking 启动中 env=%s", settings.APP_ENV)
    init_db()
    logger.info(
        "数据库就绪 backend=%s cache=%s",
        "sqlite(降级)" if is_sqlite_fallback() else "mysql",
        "redis" if settings.REDIS_URL else "memory",
    )
    logger.info(
        "外部服务 deepseek=%s qiling=%s sms=%s push=%s",
        "ready" if settings.deepseek_ready else "未配置",
        "ready" if settings.qiling_ready else "未配置",
        settings.SMS_PROVIDER,
        settings.PUSH_PROVIDER,
    )

    from app.scheduler import start_scheduler, shutdown_scheduler

    start_scheduler()
    yield
    shutdown_scheduler()
    logger.info("Survolocking 已停止")


app = FastAPI(
    title="Survolocking API",
    description="骚扰电话拦截 · 端云协同服务",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.APP_ENV == "dev" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(family.router, prefix=settings.API_PREFIX)
app.include_router(logs.router, prefix=settings.API_PREFIX)
app.include_router(rules.router, prefix=settings.API_PREFIX)
app.include_router(notify.router, prefix=settings.API_PREFIX)
app.include_router(qiling.router, prefix=settings.API_PREFIX)
app.include_router(admin.router, prefix=settings.API_PREFIX)


@app.get("/health")
async def health():
    from app.core.cache import cache

    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "env": settings.APP_ENV,
        "db": "sqlite" if is_sqlite_fallback() else "mysql",
        "cache": cache.backend,
        "deepseek": settings.deepseek_ready,
        "qiling": settings.qiling_ready,
    }


@app.get("/")
async def root():
    return {"name": settings.APP_NAME, "docs": "/docs", "health": "/health"}
