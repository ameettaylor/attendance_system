"""
Dispatcher web interface router.

All routes are protected by the require_auth dependency and display EAT times.

Routes
------
GET  /dashboard                  Today's overview (HTMX polls activity feed)
GET  /dashboard/activity         HTMX partial: activity feed table

GET  /technicians                Roster list + add form
POST /technicians                Create technician
GET  /technicians/{id}/edit      Edit form
POST /technicians/{id}/edit      Update technician

GET  /customers                  Customer list + site list + add forms
POST /customers                  Create customer
POST /customers/{id}/edit        Update customer
POST /sites                      Create site
POST /sites/{id}/edit            Update site

GET  /allocations                Dispatch screen for a given date
POST /allocations                Create allocation
POST /allocations/{id}/delete    Remove allocation
"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.db import (
    Agent, Alert, Allocation, Attendance, Customer,
    Engineer, Log, Site, get_session_factory,
)
from app.routers.auth import require_auth
from app.utils.templates import ctx, set_flash, templates

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_number(raw: str) -> str:
    """Ensure whatsapp:+XXXXXXXXXXX format."""
    raw = raw.strip()
    if raw.startswith("whatsapp:"):
        return raw
    if not raw.startswith("+"):
        raw = "+" + raw
    return "whatsapp:" + raw


def _eat_to_utc(work_date: date, time_str: str) -> Optional[datetime]:
    """
    Combine a date and an HH:MM EAT time string into a UTC datetime.
    Returns None if time_str is empty.
    """
    if not time_str or not time_str.strip():
        return None
    try:
        h, m = [int(x) for x in time_str.strip().split(":")]
    except (ValueError, AttributeError):
        return None
    eat_dt = datetime(work_date.year, work_date.month, work_date.day, h, m,
                      tzinfo=EAT)
    return eat_dt.astimezone(timezone.utc).replace(tzinfo=None)


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
):
    today = date.today()

    total_allocations = db.query(Allocation).filter(
        Allocation.work_date == today
    ).count()

    on_site = db.query(Attendance).filter(
        Attendance.work_date == today,
        Attendance.check_out_time.is_(None),
    ).count()

    completed = db.query(Attendance).filter(
        Attendance.work_date == today,
        Attendance.check_out_time.isnot(None),
    ).count()

    open_alerts = db.query(Alert).filter(Alert.resolved == False).count()  # noqa: E712

    allocations_today = (
        db.query(Allocation)
        .filter(Allocation.work_date == today)
        .order_by(Allocation.scheduled_start_time.nullslast(), Allocation.id)
        .all()
    )

    return templates.TemplateResponse(
        "dashboard.html",
        ctx(
            request, session,
            active_page="dashboard",
            today=today,
            total_allocations=total_allocations,
            on_site=on_site,
            completed=completed,
            open_alerts=open_alerts,
            allocations_today=allocations_today,
        ),
    )


@router.get("/dashboard/activity", response_class=HTMLResponse)
def dashboard_activity(
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
):
    """HTMX partial — returns only the activity table rows."""
    today = date.today()
    records = (
        db.query(Attendance)
        .filter(Attendance.work_date == today)
        .order_by(Attendance.check_in_time.desc())
        .limit(20)
        .all()
    )
    return templates.TemplateResponse(
        "partials/activity_feed.html",
        {"request": request, "records": records},
    )


# ══════════════════════════════════════════════════════════════════════════════
# TECHNICIANS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/technicians", response_class=HTMLResponse)
def technicians(
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
):
    engineers = (
        db.query(Engineer)
        .order_by(Engineer.active.desc(), Engineer.name)
        .all()
    )
    return templates.TemplateResponse(
        "technicians.html",
        ctx(request, session, active_page="technicians", engineers=engineers),
    )


@router.post("/technicians", response_class=HTMLResponse)
def technician_create(
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
    name: str = Form(...),
    whatsapp_number: str = Form(...),
    technician_type: str = Form(default=""),
    skill_level: str = Form(default=""),
    preferred_notification_time: str = Form(default=""),
):
    name = name.strip()
    if not name:
        set_flash(request, "Name is required.", "error")
        return RedirectResponse("/technicians", status_code=302)

    number = _normalise_number(whatsapp_number)
    existing = db.query(Engineer).filter(
        Engineer.whatsapp_number == number
    ).first()
    if existing:
        set_flash(
            request,
            f"A technician with number {number} already exists.",
            "error",
        )
        return RedirectResponse("/technicians", status_code=302)

    notif_time = preferred_notification_time.strip() or None
    if notif_time and not re.match(r"^\d{2}:\d{2}$", notif_time):
        set_flash(request, "Notification time must be in HH:MM format (EAT).", "error")
        return RedirectResponse("/technicians", status_code=302)

    eng = Engineer(
        name=name,
        whatsapp_number=number,
        technician_type=technician_type.strip() or None,
        skill_level=skill_level.strip() or None,
        preferred_notification_time=notif_time,
    )
    db.add(eng)
    db.commit()
    set_flash(request, f"Technician '{name}' added successfully.")
    return RedirectResponse("/technicians", status_code=302)


@router.get("/technicians/{eng_id}/edit", response_class=HTMLResponse)
def technician_edit_form(
    eng_id: int,
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
):
    eng = db.query(Engineer).filter(Engineer.id == eng_id).first()
    if not eng:
        set_flash(request, "Technician not found.", "error")
        return RedirectResponse("/technicians", status_code=302)
    return templates.TemplateResponse(
        "technician_edit.html",
        ctx(request, session, active_page="technicians", eng=eng),
    )


@router.post("/technicians/{eng_id}/edit", response_class=HTMLResponse)
def technician_update(
    eng_id: int,
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
    name: str = Form(...),
    technician_type: str = Form(default=""),
    skill_level: str = Form(default=""),
    preferred_notification_time: str = Form(default=""),
    active: str = Form(default=""),
):
    eng = db.query(Engineer).filter(Engineer.id == eng_id).first()
    if not eng:
        set_flash(request, "Technician not found.", "error")
        return RedirectResponse("/technicians", status_code=302)

    name = name.strip()
    if not name:
        set_flash(request, "Name is required.", "error")
        return RedirectResponse(f"/technicians/{eng_id}/edit", status_code=302)

    notif_time = preferred_notification_time.strip() or None
    if notif_time and not re.match(r"^\d{2}:\d{2}$", notif_time):
        set_flash(request, "Notification time must be in HH:MM format (EAT).", "error")
        return RedirectResponse(f"/technicians/{eng_id}/edit", status_code=302)

    eng.name                        = name
    eng.technician_type             = technician_type.strip() or None
    eng.skill_level                 = skill_level.strip() or None
    eng.preferred_notification_time = notif_time
    eng.active                      = (active == "on")

    db.commit()
    set_flash(request, f"Technician '{eng.name}' updated.")
    return RedirectResponse("/technicians", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOMERS & SITES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/customers", response_class=HTMLResponse)
def customers(
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
):
    all_customers = (
        db.query(Customer)
        .order_by(Customer.active.desc(), Customer.name)
        .all()
    )
    all_sites = (
        db.query(Site)
        .order_by(Site.active.desc(), Site.name)
        .all()
    )
    return templates.TemplateResponse(
        "customers.html",
        ctx(
            request, session,
            active_page="customers",
            customers=all_customers,
            sites=all_sites,
        ),
    )


@router.post("/customers", response_class=HTMLResponse)
def customer_create(
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
    name: str = Form(...),
    contact_name: str = Form(default=""),
    contact_phone: str = Form(default=""),
    address: str = Form(default=""),
):
    name = name.strip()
    if not name:
        set_flash(request, "Customer name is required.", "error")
        return RedirectResponse("/customers", status_code=302)

    customer = Customer(
        name=name,
        contact_name=contact_name.strip() or None,
        contact_phone=contact_phone.strip() or None,
        address=address.strip() or None,
    )
    db.add(customer)
    db.commit()
    set_flash(request, f"Customer '{name}' added.")
    return RedirectResponse("/customers", status_code=302)


@router.post("/customers/{cust_id}/edit", response_class=HTMLResponse)
def customer_update(
    cust_id: int,
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
    name: str = Form(...),
    contact_name: str = Form(default=""),
    contact_phone: str = Form(default=""),
    address: str = Form(default=""),
    active: str = Form(default=""),
):
    cust = db.query(Customer).filter(Customer.id == cust_id).first()
    if not cust:
        set_flash(request, "Customer not found.", "error")
        return RedirectResponse("/customers", status_code=302)

    name = name.strip()
    if not name:
        set_flash(request, "Customer name is required.", "error")
        return RedirectResponse("/customers", status_code=302)

    cust.name          = name
    cust.contact_name  = contact_name.strip() or None
    cust.contact_phone = contact_phone.strip() or None
    cust.address       = address.strip() or None
    cust.active        = (active == "on")
    db.commit()
    set_flash(request, f"Customer '{cust.name}' updated.")
    return RedirectResponse("/customers", status_code=302)


@router.post("/sites", response_class=HTMLResponse)
def site_create(
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
    name: str = Form(...),
    address: str = Form(default=""),
    latitude: str = Form(...),
    longitude: str = Form(...),
    geofence_radius_meters: str = Form(default=""),
):
    name = name.strip()
    if not name:
        set_flash(request, "Site name is required.", "error")
        return RedirectResponse("/customers", status_code=302)

    try:
        lat = float(latitude)
        lon = float(longitude)
    except (ValueError, TypeError):
        set_flash(request, "Latitude and longitude must be valid numbers.", "error")
        return RedirectResponse("/customers", status_code=302)

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        set_flash(request, "Coordinates out of valid range.", "error")
        return RedirectResponse("/customers", status_code=302)

    radius = None
    if geofence_radius_meters.strip():
        try:
            radius = float(geofence_radius_meters)
            if radius <= 0:
                raise ValueError
        except ValueError:
            set_flash(request, "Geofence radius must be a positive number.", "error")
            return RedirectResponse("/customers", status_code=302)

    site = Site(
        name=name,
        address=address.strip() or None,
        latitude=lat,
        longitude=lon,
        geofence_radius_meters=radius,
    )
    db.add(site)
    db.commit()
    set_flash(request, f"Site '{name}' added.")
    return RedirectResponse("/customers", status_code=302)


@router.post("/sites/{site_id}/edit", response_class=HTMLResponse)
def site_update(
    site_id: int,
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
    name: str = Form(...),
    address: str = Form(default=""),
    latitude: str = Form(...),
    longitude: str = Form(...),
    geofence_radius_meters: str = Form(default=""),
    active: str = Form(default=""),
):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        set_flash(request, "Site not found.", "error")
        return RedirectResponse("/customers", status_code=302)

    try:
        lat = float(latitude)
        lon = float(longitude)
    except (ValueError, TypeError):
        set_flash(request, "Latitude and longitude must be valid numbers.", "error")
        return RedirectResponse("/customers", status_code=302)

    radius = None
    if geofence_radius_meters.strip():
        try:
            radius = float(geofence_radius_meters)
        except ValueError:
            set_flash(request, "Geofence radius must be a positive number.", "error")
            return RedirectResponse("/customers", status_code=302)

    site.name                    = name.strip()
    site.address                 = address.strip() or None
    site.latitude                = lat
    site.longitude               = lon
    site.geofence_radius_meters  = radius
    site.active                  = (active == "on")
    db.commit()
    set_flash(request, f"Site '{site.name}' updated.")
    return RedirectResponse("/customers", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ALLOCATIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/allocations", response_class=HTMLResponse)
def allocations(
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
    work_date: str = "",
):
    try:
        selected_date = date.fromisoformat(work_date) if work_date else date.today()
    except ValueError:
        selected_date = date.today()

    alloc_list = (
        db.query(Allocation)
        .filter(Allocation.work_date == selected_date)
        .order_by(Allocation.scheduled_start_time.nullslast(), Allocation.id)
        .all()
    )

    # Enrich with attendance status
    # Build a set of engineer IDs with open check-in today
    open_checkins = {
        r.engineer_id
        for r in db.query(Attendance).filter(
            Attendance.work_date == selected_date,
            Attendance.check_out_time.is_(None),
        ).all()
    }
    completed_checkins = {
        r.engineer_id
        for r in db.query(Attendance).filter(
            Attendance.work_date == selected_date,
            Attendance.check_out_time.isnot(None),
        ).all()
    }

    active_engineers = (
        db.query(Engineer)
        .filter(Engineer.active == True)  # noqa: E712
        .order_by(Engineer.name)
        .all()
    )
    active_sites = (
        db.query(Site)
        .filter(Site.active == True)  # noqa: E712
        .order_by(Site.name)
        .all()
    )
    active_customers = (
        db.query(Customer)
        .filter(Customer.active == True)  # noqa: E712
        .order_by(Customer.name)
        .all()
    )

    return templates.TemplateResponse(
        "allocations.html",
        ctx(
            request, session,
            active_page="allocations",
            selected_date=selected_date,
            alloc_list=alloc_list,
            open_checkins=open_checkins,
            completed_checkins=completed_checkins,
            active_engineers=active_engineers,
            active_sites=active_sites,
            active_customers=active_customers,
        ),
    )


@router.post("/allocations", response_class=HTMLResponse)
def allocation_create(
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
    engineer_id: int = Form(...),
    site_id: int = Form(...),
    customer_id: str = Form(default=""),
    work_date: str = Form(...),
    scheduled_start_time: str = Form(default=""),
    work_description: str = Form(default=""),
):
    try:
        w_date = date.fromisoformat(work_date)
    except ValueError:
        set_flash(request, "Invalid date format.", "error")
        return RedirectResponse("/allocations", status_code=302)

    eng  = db.query(Engineer).filter(Engineer.id == engineer_id).first()
    site = db.query(Site).filter(Site.id == site_id).first()
    if not eng or not site:
        set_flash(request, "Invalid engineer or site selection.", "error")
        return RedirectResponse(f"/allocations?work_date={work_date}", status_code=302)

    cust_id = None
    if customer_id.strip():
        try:
            cust_id = int(customer_id)
        except ValueError:
            pass

    utc_start = _eat_to_utc(w_date, scheduled_start_time)

    alloc = Allocation(
        engineer_id=engineer_id,
        site_id=site_id,
        customer_id=cust_id,
        work_date=w_date,
        scheduled_start_time=utc_start,
        work_description=work_description.strip() or None,
    )
    db.add(alloc)
    db.commit()
    set_flash(request, f"Allocation created: {eng.name} → {site.name} on {w_date}.")
    return RedirectResponse(f"/allocations?work_date={work_date}", status_code=302)


@router.post("/allocations/{alloc_id}/delete", response_class=HTMLResponse)
def allocation_delete(
    alloc_id: int,
    request: Request,
    db: Session = Depends(get_db),
    session: dict = Depends(require_auth),
    work_date: str = Form(default=""),
):
    alloc = db.query(Allocation).filter(Allocation.id == alloc_id).first()
    if not alloc:
        set_flash(request, "Allocation not found.", "error")
        return RedirectResponse("/allocations", status_code=302)

    redirect_date = work_date or str(alloc.work_date)
    db.delete(alloc)
    db.commit()
    set_flash(request, "Allocation removed.")
    return RedirectResponse(f"/allocations?work_date={redirect_date}", status_code=302)
