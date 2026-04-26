"""
Supervisor dashboard router.

Routes
------
GET  /supervisor                  Full dashboard page
GET  /supervisor/panels           HTMX partial — all three panels (auto-refreshed every 60 s)
POST /supervisor/alerts/{id}/resolve   Mark an alert as resolved

Panels
------
1. Alerts        — unresolved late_checkin and geofence_breach alerts
2. Utilisation   — active engineers with/without an allocation today
3. Activity feed — merged check-in, check-out, progress report, material request events
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.db import (
    Alert, Allocation, Attendance, Engineer, Log,
    get_session_factory,
)
from app.routers.auth import require_auth
from app.utils.templates import ctx, set_flash, templates

logger = logging.getLogger(__name__)
router = APIRouter()
EAT = timezone(timedelta(hours=3))


# ── DB dependency ─────────────────────────────────────────────────────────────

def get_db():
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


# ── Dashboard data builder ────────────────────────────────────────────────────

def _build_dashboard_data(db: Session) -> dict:
    """
    Assembles all three panels' data in one place so both the full-page
    route and the HTMX partial route share identical logic.
    """
    today = date.today()

    # ── Panel 1: open alerts ─────────────────────────────────────────────────
    open_alerts = (
        db.query(Alert)
        .filter(Alert.resolved == False)  # noqa: E712
        .order_by(Alert.created_at.desc())
        .limit(50)
        .all()
    )

    # ── Panel 2: utilisation ─────────────────────────────────────────────────
    allocated_ids = {
        row.engineer_id
        for row in db.query(Allocation).filter(Allocation.work_date == today).all()
    }
    all_active = (
        db.query(Engineer)
        .filter(Engineer.active == True)  # noqa: E712
        .order_by(Engineer.name)
        .all()
    )
    allocated   = [e for e in all_active if e.id in allocated_ids]
    unallocated = [e for e in all_active if e.id not in allocated_ids]

    # ── Panel 3: activity feed ────────────────────────────────────────────────
    attendance_today = (
        db.query(Attendance)
        .filter(Attendance.work_date == today)
        .all()
    )

    today_start = datetime(today.year, today.month, today.day, 0, 0, 0)
    logs_today = (
        db.query(Log)
        .filter(Log.created_at >= today_start)
        .all()
    )

    events = []

    for r in attendance_today:
        events.append({
            "time_utc":  r.check_in_time,
            "kind":      "checkin",
            "engineer":  r.engineer.name,
            "detail":    r.site.name if r.site else "Unknown site",
            "flagged":   not r.check_in_within_geofence,
            "extra":     None,
        })
        if r.check_out_time:
            out_flagged = (
                r.check_out_within_geofence is False
            )
            events.append({
                "time_utc":  r.check_out_time,
                "kind":      "checkout",
                "engineer":  r.engineer.name,
                "detail":    r.site.name if r.site else "Unknown site",
                "flagged":   out_flagged,
                "extra":     f"{r.hours_on_site:.1f} h" if r.hours_on_site else None,
            })

    for log in logs_today:
        events.append({
            "time_utc":  log.created_at,
            "kind":      log.log_type,          # progress_report | material_request
            "engineer":  log.engineer.name,
            "detail":    log.content,
            "flagged":   False,
            "extra":     None,
        })

    events.sort(key=lambda e: e["time_utc"], reverse=True)
    events = events[:40]   # cap at 40 most recent

    # Convert times to EAT for display
    for ev in events:
        dt = ev["time_utc"].replace(tzinfo=timezone.utc).astimezone(EAT)
        ev["time_eat"] = dt.strftime("%H:%M")

    return {
        "today":        today,
        "open_alerts":  open_alerts,
        "allocated":    allocated,
        "unallocated":  unallocated,
        "events":       events,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/supervisor", response_class=HTMLResponse)
def supervisor_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
):
    data = _build_dashboard_data(db)
    return templates.TemplateResponse(
        "supervisor.html",
        ctx(request, session, active_page="supervisor", **data),
    )


@router.get("/supervisor/panels", response_class=HTMLResponse)
def supervisor_panels(
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
):
    """HTMX endpoint — returns the three panels without the page shell."""
    data = _build_dashboard_data(db)
    return templates.TemplateResponse(
        "partials/supervisor_panels.html",
        {"request": request, **data},
    )


@router.post("/supervisor/alerts/{alert_id}/resolve", response_class=HTMLResponse)
def resolve_alert(
    alert_id: int,
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
):
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        set_flash(request, "Alert not found.", "error")
    else:
        alert.resolved = True
        db.commit()
        logger.info(
            "Alert %d resolved by %s", alert_id, session.get("username")
        )
        set_flash(request, "Alert marked as resolved.")
    return RedirectResponse("/supervisor", status_code=302)
