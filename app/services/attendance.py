"""
Attendance service.

Handles the core business logic for check-in and check-out events.
Called by the webhook router when a valid location message is received.
"""

from datetime import datetime, date
from typing import Optional
from sqlalchemy.orm import Session
from app.models.db import Allocation, Engineer, Log, Site, Assignment, Attendance
from app.services.geofence import GeoPoint, is_within_geofence
from app.config import get_settings
import logging

logger = logging.getLogger(__name__)


def get_todays_assignment(db: Session, engineer: Engineer):
    today = date.today()
    return (
        db.query(Assignment)
        .filter(
            Assignment.engineer_id == engineer.id,
            Assignment.work_date == today,
        )
        .first()
    )


def get_todays_allocation(db: Session, engineer: Engineer):
    """Return today's allocation for this engineer (new dispatch system)."""
    today = date.today()
    return (
        db.query(Allocation)
        .filter(
            Allocation.engineer_id == engineer.id,
            Allocation.work_date == today,
        )
        .order_by(Allocation.scheduled_start_time.nullslast())
        .first()
    )


def get_open_attendance(db: Session, engineer: Engineer):
    """Return today's attendance record that has no check-out time yet."""
    today = date.today()
    return (
        db.query(Attendance)
        .filter(
            Attendance.engineer_id == engineer.id,
            Attendance.work_date == today,
            Attendance.check_out_time.is_(None),
        )
        .first()
    )


def get_todays_attendance(db: Session, engineer: Engineer):
    """Return today's most recent attendance record (open or closed)."""
    today = date.today()
    return (
        db.query(Attendance)
        .filter(
            Attendance.engineer_id == engineer.id,
            Attendance.work_date == today,
        )
        .order_by(Attendance.check_in_time.desc())
        .first()
    )


def process_checkin(
    db: Session,
    engineer: Engineer,
    latitude: float,
    longitude: float,
) -> dict:
    """
    Record a check-in for an engineer.

    Returns a dict with keys:
        status        : "confirmed" | "outside_geofence" | "no_assignment" | "already_checked_in"
        site_name     : str | None
        distance_m    : float | None
        time_str      : str
    """
    settings = get_settings()
    now = datetime.utcnow()
    time_str = now.strftime("%H:%M UTC")

    # Already checked in today?
    open_record = get_open_attendance(db, engineer)
    if open_record:
        site_name = open_record.site.name if open_record.site else "your site"
        return {"status": "already_checked_in", "site_name": site_name, "distance_m": None, "time_str": time_str}

    # Check assignment
    assignment = get_todays_assignment(db, engineer)
    if not assignment:
        return {"status": "no_assignment", "site_name": None, "distance_m": None, "time_str": time_str}

    site = assignment.site
    engineer_point = GeoPoint(latitude=latitude, longitude=longitude)
    site_point = GeoPoint(latitude=site.latitude, longitude=site.longitude)
    radius = site.geofence_radius_meters or settings.geofence_radius_meters
    within, distance_m = is_within_geofence(engineer_point, site_point, radius)

    record = Attendance(
        engineer_id=engineer.id,
        site_id=site.id,
        work_date=date.today(),
        check_in_time=now,
        check_in_latitude=latitude,
        check_in_longitude=longitude,
        check_in_distance_m=distance_m,
        check_in_within_geofence=within,
        flagged=not within,
        flag_reason=None if within else f"Check-in {distance_m:.0f}m from site (allowed {radius:.0f}m)",
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    logger.info(
        "Check-in: engineer=%s site=%s distance=%.0fm within=%s",
        engineer.name, site.name, distance_m, within,
    )

    status = "confirmed" if within else "outside_geofence"
    return {"status": status, "site_name": site.name, "distance_m": distance_m, "time_str": time_str}


def process_checkout(
    db: Session,
    engineer: Engineer,
    latitude: float,
    longitude: float,
) -> dict:
    """
    Record a check-out for an engineer.

    Returns a dict with keys:
        status        : "confirmed" | "outside_geofence" | "no_checkin"
        site_name     : str | None
        distance_m    : float | None
        hours         : float | None
        time_str      : str
    """
    settings = get_settings()
    now = datetime.utcnow()
    time_str = now.strftime("%H:%M UTC")

    open_record = get_open_attendance(db, engineer)
    if not open_record:
        return {"status": "no_checkin", "site_name": None, "distance_m": None, "hours": None, "time_str": time_str}

    site = open_record.site
    engineer_point = GeoPoint(latitude=latitude, longitude=longitude)
    site_point = GeoPoint(latitude=site.latitude, longitude=site.longitude)
    radius = site.geofence_radius_meters or settings.geofence_radius_meters
    within, distance_m = is_within_geofence(engineer_point, site_point, radius)

    open_record.check_out_time = now
    open_record.check_out_latitude = latitude
    open_record.check_out_longitude = longitude
    open_record.check_out_distance_m = distance_m
    open_record.check_out_within_geofence = within

    if not within:
        open_record.flagged = True
        existing = open_record.flag_reason or ""
        open_record.flag_reason = (
            existing + f" | Check-out {distance_m:.0f}m from site"
        ).lstrip(" | ")

    db.commit()
    db.refresh(open_record)

    hours = open_record.hours_on_site
    logger.info(
        "Check-out: engineer=%s site=%s hours=%.2f distance=%.0fm within=%s",
        engineer.name, site.name, hours or 0, distance_m, within,
    )

    status = "confirmed" if within else "outside_geofence"
    return {
        "status":        status,
        "site_name":     site.name,
        "distance_m":    distance_m,
        "hours":         hours,
        "time_str":      time_str,
        "attendance_id": open_record.id,   # needed to link progress report log
    }


def save_log(
    db: Session,
    engineer_id: int,
    log_type: str,
    content: str,
    attendance_id: Optional[int] = None,
    allocation_id: Optional[int] = None,
) -> Log:
    """
    Persist a progress report or material request to the logs table.

    log_type: "progress_report" | "material_request"
    """
    entry = Log(
        engineer_id=engineer_id,
        log_type=log_type,
        content=content,
        attendance_id=attendance_id,
        allocation_id=allocation_id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    logger.info(
        "Log saved: engineer_id=%s type=%s attendance_id=%s allocation_id=%s",
        engineer_id, log_type, attendance_id, allocation_id,
    )
    return entry
