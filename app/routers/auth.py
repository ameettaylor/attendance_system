"""
Authentication router.

Routes:
    GET  /login   — serve the login form
    POST /login   — validate credentials, set session, redirect to /dashboard
    GET  /logout  — clear session, redirect to /login

Dependency:
    require_auth  — FastAPI dependency that returns the current agent's session
                    data or raises AuthRedirect to /login if not authenticated.

Password hashing:
    Uses passlib with bcrypt. Never store plain-text passwords.
"""

import logging
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.models.db import Agent, get_session_factory

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Password hashing (bcrypt directly — passlib 1.7.4 is incompatible with bcrypt 4.x) ──

def hash_password(plain: str) -> str:
    """Return a bcrypt hash of the given plain-text password."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the bcrypt hash."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ── Custom redirect exception (raised from the require_auth dependency) ───────

class AuthRedirect(Exception):
    """Raised when an unauthenticated request hits a protected route."""
    def __init__(self, url: str = "/login"):
        self.url = url


# ── Database dependency ───────────────────────────────────────────────────────

def get_db():
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


# ── Auth helpers ──────────────────────────────────────────────────────────────

def authenticate_agent(db: Session, username: str, password: str) -> Optional[Agent]:
    """
    Look up the agent by username and verify the password.
    Returns the Agent on success, None on failure.
    """
    agent = db.query(Agent).filter(
        Agent.username == username,
        Agent.active == True,
    ).first()
    if not agent:
        return None
    if not verify_password(password, agent.password_hash):
        return None
    return agent


# ── Auth dependency ───────────────────────────────────────────────────────────

def require_auth(request: Request) -> dict:
    """
    FastAPI dependency for protected routes.

    Returns the session dict  {"agent_id": int, "username": str, "role": str}
    if the user is logged in.  Raises AuthRedirect("/login") otherwise.

    Usage:
        @router.get("/dashboard")
        def dashboard(session: dict = Depends(require_auth)):
            ...
    """
    agent_id = request.session.get("agent_id")
    if not agent_id:
        raise AuthRedirect("/login")
    return {
        "agent_id":  request.session["agent_id"],
        "username":  request.session["username"],
        "role":      request.session["role"],
    }


# ── Jinja2 templates ──────────────────────────────────────────────────────────
# Templates are resolved relative to the project root (one level above app/).
# Module 3 will set up a shared templates instance on the app; for now we
# create a local one just for the login page.

import os as _os
_templates_dir = _os.path.join(_os.path.dirname(__file__), "..", "..", "templates")
templates = Jinja2Templates(directory=_os.path.abspath(_templates_dir))


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """Serve the login form. Redirect to dashboard if already logged in."""
    if request.session.get("agent_id"):
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Validate credentials, set the session cookie, redirect to dashboard."""
    agent = authenticate_agent(db, username.strip(), password)
    if not agent:
        logger.warning("Failed login attempt for username=%r", username)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password."},
            status_code=401,
        )

    # Store minimal identity in the signed session cookie
    request.session["agent_id"] = agent.id
    request.session["username"] = agent.username
    request.session["role"]     = agent.role

    logger.info("Agent logged in: username=%s role=%s", agent.username, agent.role)
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/logout")
def logout(request: Request):
    """Clear the session and redirect to the login page."""
    username = request.session.get("username", "unknown")
    request.session.clear()
    logger.info("Agent logged out: username=%s", username)
    return RedirectResponse(url="/login", status_code=302)
