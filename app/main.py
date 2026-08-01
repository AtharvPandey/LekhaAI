"""
LekhaAI — AI Accountant-in-a-Box
FastAPI application entry point.
"""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.api.webhooks import router as webhook_router
from app.db.database import init_database

# ── Setup logging ──
settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("lekha")

# ── Create app ──
app = FastAPI(
    title="LekhaAI",
    description="WhatsApp-first GST compliance agent for Indian micro-businesses",
    version="0.2.0",
    docs_url="/docs" if settings.app_debug else None,
    redoc_url=None,
)

# ── CORS (for future CA dashboard) ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.app_debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register routes (both /api/webhook and /webhook) ──
app.include_router(webhook_router, prefix="/api")
app.include_router(webhook_router)


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "lekha-ai",
        "version": "0.2.0",
    }


@app.on_event("startup")
async def startup():
    logger.info("🚀 LekhaAI starting up...")
    logger.info(f"   Environment: {settings.app_env}")
    logger.info(f"   Debug: {settings.app_debug}")
    init_database()
    logger.info("   Database initialized")


@app.on_event("shutdown")
async def shutdown():
    logger.info("LekhaAI shutting down...")
