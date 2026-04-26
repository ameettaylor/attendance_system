"""
Shared Jinja2Templates instance with project-wide custom filters.

Import `templates` from here everywhere — one Jinja2 environment, consistent
filter set, no duplicate template directories.

Custom filters:
    eat_datetime  — UTC datetime → "26 Apr 2026, 14:30" (EAT)
    eat_time      — UTC datetime → "14:30" (EAT)
    eat_date      — date object  → "26 Apr 2026"

Flash helpers (session-based one-shot messages):
    set_flash(request, message, type="success")
    get_flash(request) -> dict | None   (pops after reading)
"""

import os
from datetime import timezone, timedelta
from typing import Optional

from fastapi import Request
from fastapi.templating import Jinja2Templates

# ── EAT timezone ──────────────────────────────────────────────────────────────
EAT = timezone(timedelta(hours=3))


def _eat_datetime(dt) -> str:
    """Return UTC datetime as a human-readable EAT string."""
    if dt is None:
        return "—"
    aware = dt.replace(tzinfo=timezone.utc).astimezone(EAT)
    return aware.strftime("%d %b %Y, %H:%M")


def _eat_time(dt) -> str:
    """Return UTC datetime as HH:MM in EAT."""
    if dt is None:
        return "—"
    aware = dt.replace(tzinfo=timezone.utc).astimezone(EAT)
    return aware.strftime("%H:%M")


def _eat_date(d) -> str:
    """Return a date object as a readable string."""
    if d is None:
        return "—"
    return d.strftime("%d %b %Y")


# ── Jinja2 environment ────────────────────────────────────────────────────────
_templates_dir = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)
templates = Jinja2Templates(directory=_templates_dir)
templates.env.filters["eat_datetime"] = _eat_datetime
templates.env.filters["eat_time"]     = _eat_time
templates.env.filters["eat_date"]     = _eat_date


# ── Session flash helpers ─────────────────────────────────────────────────────

def set_flash(request: Request, message: str, kind: str = "success") -> None:
    """Store a one-time flash message in the session."""
    request.session["_flash"] = {"message": message, "kind": kind}


def get_flash(request: Request) -> Optional[dict]:
    """Pop and return the flash dict, or None if none is set."""
    return request.session.pop("_flash", None)


# ── Template context helper ───────────────────────────────────────────────────

def ctx(request: Request, session: dict, **kwargs) -> dict:
    """
    Build the standard Jinja2 template context.

    Merges the request, current agent session, flash message, and any
    extra keyword args.  Use in every route:

        return templates.TemplateResponse("page.html",
            ctx(request, session, active_page="dashboard", data=...))
    """
    return {
        "request":      request,
        "session_user": session,
        "flash":        get_flash(request),
        **kwargs,
    }
