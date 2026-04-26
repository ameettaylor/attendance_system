"""
FastAPI application entry point.

Start with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.models.db import init_db
from app.routers import webhook
from app.routers.auth import AuthRedirect, router as auth_router
from app.routers.supervisor import router as supervisor_router
from app.routers.web import router as web_router
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
    version="2.0.0",
    lifespan=lifespan,
)

# ── Session middleware (must be added before routes are called) ───────────────
# Uses itsdangerous to sign the cookie; SESSION_SECRET must be set in .env.
settings = get_settings()
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="fsm_session",
    max_age=60 * 60 * 10,   # 10 hours
    https_only=False,        # set True once on Railway with HTTPS
    same_site="lax",
)


# ── Exception handler: redirect unauthenticated users to /login ───────────────
@app.exception_handler(AuthRedirect)
async def auth_redirect_handler(request: Request, exc: AuthRedirect):
    return RedirectResponse(url=exc.url, status_code=302)


# ── Static files ─────────────────────────────────────────────────────────────
import os as _os
_static_dir = _os.path.join(_os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=_os.path.abspath(_static_dir)), name="static")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(web_router)
app.include_router(supervisor_router)
app.include_router(webhook.router)


# ── Health check (must stay working throughout all modules) ───────────────────
@app.get("/health")
def health_check():
    """Railway health check endpoint."""
    return {"status": "ok"}
