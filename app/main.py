"""
FastAPI application entry point.

Start with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.models.db import init_db
from app.routers import webhook
from app.services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initialising database...")
    init_db()
    logger.info("Starting scheduler...")
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()


app = FastAPI(
    title="Field Engineer Time & Attendance",
    description="WhatsApp-based check-in/check-out system with geofencing.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(webhook.router)


@app.get("/health")
def health_check():
    """Railway health check endpoint."""
    return {"status": "ok"}
