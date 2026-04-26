"""
Twilio webhook router.

Twilio POST's to /webhook/twilio for every inbound WhatsApp message.
This router parses the payload, identifies the engineer, and dispatches
to the correct handler based on:
  1. The current conversation state (are we waiting for a location?)
  2. The message body keyword (IN / OUT / STATUS / HELP)
  3. Whether a location was shared
"""

import logging
from fastapi import APIRouter, Form, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from twilio.request_validator import RequestValidator

from datetime import timezone, timedelta

from app.config import get_settings
from app.models.db import Alert, Engineer, Supervisor, get_session_factory
from app.services import messaging as msg
from app.services import attendance as att
from app.services.state import (
    ConversationStep, get_state, set_state, clear_state
)

EAT = timezone(timedelta(hours=3))

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Database dependency ───────────────────────────────────────────────────────

def get_db():
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


# ── Twilio signature validation ───────────────────────────────────────────────

def validate_twilio_signature(request: Request, body: bytes) -> bool:
    """
    Verify the request genuinely came from Twilio.
    Returns True if valid, False otherwise.
    Set TWILIO_SKIP_VALIDATION=true in .env during local development only.
    """
    settings = get_settings()
    validator = RequestValidator(settings.twilio_auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)

    # Form params must be passed as a flat dict for validation
    # We rely on FastAPI parsing them via Form() parameters in the handler
    return validator.validate(url, {}, signature)


# ── Main webhook handler ──────────────────────────────────────────────────────

@router.post("/webhook/twilio")
async def twilio_webhook(
    request: Request,
    From: str = Form(...),           # e.g. whatsapp:+254700000001
    Body: str = Form(default=""),
    Latitude: str = Form(default=None),
    Longitude: str = Form(default=None),
    db: Session = Depends(get_db),
):
    """
    Entry point for all inbound WhatsApp messages from Twilio.

    Message types we care about:
      - Text: IN, OUT, STATUS, HELP (case-insensitive)
      - Location share: Latitude and Longitude form fields are populated
    """
    settings = get_settings()
    sender = From.strip()
    text = Body.strip().upper()

    logger.info("Inbound: from=%s body=%r lat=%s lon=%s", sender, Body, Latitude, Longitude)

    # Look up engineer
    engineer = db.query(Engineer).filter(
        Engineer.whatsapp_number == sender,
        Engineer.active == True,
    ).first()

    if not engineer:
        msg.send_message(sender, msg.msg_not_registered())
        return {"status": "unregistered"}

    state = get_state(sender)
    has_location = Latitude is not None and Longitude is not None

    # ── Location message received ─────────────────────────────────────────────
    if has_location:
        latitude = float(Latitude)
        longitude = float(Longitude)

        if state.step == ConversationStep.AWAITING_CHECKIN_LOCATION:
            clear_state(sender)
            result = att.process_checkin(db, engineer, latitude, longitude)
            _send_checkin_response(sender, result, db, engineer)

        elif state.step == ConversationStep.AWAITING_CHECKOUT_LOCATION:
            clear_state(sender)
            result = att.process_checkout(db, engineer, latitude, longitude)
            _send_checkout_response(sender, result, db, engineer)

        else:
            # Unsolicited location — ignore gracefully
            msg.send_message(sender, "Location received but no active check-in/out in progress. Reply *IN* to check in.")

        return {"status": "ok"}

    # ── Free-text capture states (checked BEFORE keyword commands) ───────────
    # These states expect prose from the engineer, not a keyword.
    # We capture whatever they type (even if it looks like a command word).

    if state.step == ConversationStep.AWAITING_PROGRESS_REPORT:
        att.save_log(
            db,
            engineer_id=engineer.id,
            log_type="progress_report",
            content=Body.strip(),
            attendance_id=state.attendance_id,
            allocation_id=state.allocation_id,
        )
        # Advance to material request — keep the same attendance/allocation IDs
        set_state(
            sender,
            ConversationStep.AWAITING_MATERIAL_REQUEST,
            attendance_id=state.attendance_id,
            allocation_id=state.allocation_id,
        )
        msg.send_message(sender, msg.msg_progress_report_saved())
        return {"status": "ok"}

    if state.step == ConversationStep.AWAITING_MATERIAL_REQUEST:
        if Body.strip().upper() == "NONE":
            clear_state(sender)
            msg.send_message(sender, msg.msg_no_material_requests())
        else:
            att.save_log(
                db,
                engineer_id=engineer.id,
                log_type="material_request",
                content=Body.strip(),
                attendance_id=state.attendance_id,
                allocation_id=state.allocation_id,
            )
            clear_state(sender)
            msg.send_message(sender, msg.msg_material_request_saved())
        return {"status": "ok"}

    # ── Text command ──────────────────────────────────────────────────────────
    if text == "IN":
        assignment = att.get_todays_assignment(db, engineer)
        if not assignment:
            msg.send_message(sender, msg.msg_checkin_no_assignment())
            return {"status": "ok"}

        open_record = att.get_open_attendance(db, engineer)
        if open_record:
            site_name = open_record.site.name if open_record.site else "your site"
            msg.send_message(sender, msg.msg_already_checked_in(site_name))
            return {"status": "ok"}

        set_state(sender, ConversationStep.AWAITING_CHECKIN_LOCATION)
        msg.send_message(sender, msg.msg_welcome_checkin(engineer.name, assignment.site.name))

    elif text == "OUT":
        open_record = att.get_open_attendance(db, engineer)
        if not open_record:
            msg.send_message(sender, msg.msg_checkout_no_checkin())
            return {"status": "ok"}

        set_state(sender, ConversationStep.AWAITING_CHECKOUT_LOCATION)
        msg.send_message(sender, msg.msg_checkout_prompt(engineer.name))

    elif text == "STATUS":
        _handle_status(sender, engineer, db)

    elif text in ("HELP", "HI", "HELLO", "START", "MENU"):
        msg.send_message(sender, msg.msg_help())

    else:
        # Unexpected text while waiting for location
        if state.step == ConversationStep.AWAITING_CHECKIN_LOCATION:
            msg.send_message(sender, "Please share your location to complete check-in, or reply *HELP* to cancel.")
        elif state.step == ConversationStep.AWAITING_CHECKOUT_LOCATION:
            msg.send_message(sender, "Please share your location to complete check-out, or reply *HELP* to cancel.")
        else:
            msg.send_message(sender, msg.msg_help())

    return {"status": "ok"}


# ── Response helpers ──────────────────────────────────────────────────────────

def _send_checkin_response(
    sender: str,
    result: dict,
    db,
    engineer: Engineer,
) -> None:
    status = result["status"]
    if status == "confirmed":
        msg.send_message(sender, msg.msg_checkin_confirmed(
            result["site_name"], result["distance_m"], result["time_str"]
        ))
    elif status == "outside_geofence":
        msg.send_message(sender, msg.msg_checkin_outside_geofence(
            result["site_name"], result["distance_m"]
        ))
        _fire_geofence_alert(
            db, engineer,
            attendance_id=result.get("attendance_id"),
            site_name=result["site_name"],
            distance_m=result["distance_m"],
            event_type="Check-in",
        )
    elif status == "no_assignment":
        msg.send_message(sender, msg.msg_checkin_no_assignment())
    elif status == "already_checked_in":
        msg.send_message(sender, msg.msg_already_checked_in(result["site_name"]))


def _send_checkout_response(
    sender: str,
    result: dict,
    db,
    engineer: Engineer,
) -> None:
    status = result["status"]
    if status == "confirmed":
        msg.send_message(sender, msg.msg_checkout_confirmed(
            result["site_name"], result["hours"] or 0, result["time_str"]
        ))
    elif status == "outside_geofence":
        msg.send_message(sender, msg.msg_checkout_outside_geofence(
            result["site_name"], result["distance_m"]
        ))
        _fire_geofence_alert(
            db, engineer,
            attendance_id=result.get("attendance_id"),
            site_name=result["site_name"],
            distance_m=result["distance_m"],
            event_type="Check-out",
        )
    elif status == "no_checkin":
        msg.send_message(sender, msg.msg_checkout_no_checkin())
        return   # no check-out occurred — do not start progress report flow

    # Checkout was recorded (confirmed or outside_geofence).
    # Look up today's allocation so the log can be linked.
    allocation = att.get_todays_allocation(db, engineer)
    set_state(
        sender,
        ConversationStep.AWAITING_PROGRESS_REPORT,
        attendance_id=result.get("attendance_id"),
        allocation_id=allocation.id if allocation else None,
    )
    msg.send_message(sender, msg.msg_progress_report_prompt())


def _handle_status(sender: str, engineer: Engineer, db: Session) -> None:
    """
    Send a STATUS reply.

    If the engineer has a today's allocation, include the full job details
    (site, address, scheduled start time, work description, Maps link) plus
    their current attendance status.

    Falls back to the simple attendance-only reply if no allocation exists.
    """
    allocation = att.get_todays_allocation(db, engineer)
    record     = att.get_todays_attendance(db, engineer)

    # Build attendance status string
    if not record:
        attendance_status = "Not checked in yet — reply *IN* to check in."
    elif record.check_out_time is None:
        eat_in = record.check_in_time.replace(tzinfo=timezone.utc).astimezone(EAT)
        attendance_status = f"Checked in at {eat_in.strftime('%H:%M')} EAT — reply *OUT* to check out."
    else:
        attendance_status = (
            f"Checked out today after {record.hours_on_site or 0:.1f} hours on site."
        )

    if allocation:
        site = allocation.site

        # Scheduled start in EAT
        if allocation.scheduled_start_time:
            eat_start = allocation.scheduled_start_time.replace(
                tzinfo=timezone.utc
            ).astimezone(EAT)
            sched_str = eat_start.strftime("%H:%M EAT")
        else:
            sched_str = "Not set"

        maps_url = (
            f"https://www.google.com/maps?q={site.latitude},{site.longitude}"
        )

        msg.send_message(
            sender,
            msg.msg_status_with_allocation(
                site_name=site.name,
                address=site.address or "",
                scheduled_time_eat=sched_str,
                work_description=allocation.work_description or "",
                attendance_status=attendance_status,
                maps_url=maps_url,
            ),
        )
    else:
        # No allocation — fall back to simple attendance reply
        if not record:
            msg.send_message(sender, msg.msg_status_not_checked_in())
        elif record.check_out_time is None:
            eat_in = record.check_in_time.replace(tzinfo=timezone.utc).astimezone(EAT)
            site_name = record.site.name if record.site else "unknown site"
            msg.send_message(
                sender,
                msg.msg_status_checked_in(site_name, eat_in.strftime("%H:%M EAT")),
            )
        else:
            site_name = record.site.name if record.site else "unknown site"
            msg.send_message(
                sender,
                msg.msg_status_checked_out(site_name, record.hours_on_site or 0),
            )


# ── Geofence breach: create Alert + WhatsApp supervisors immediately ──────────

def _fire_geofence_alert(
    db,
    engineer: Engineer,
    attendance_id,
    site_name: str,
    distance_m: float,
    event_type: str,
) -> None:
    """
    Called immediately after a geofence breach (checkin or checkout).
    Creates an Alert record and notifies all active supervisors via WhatsApp.
    """
    alert_message = (
        f"{event_type} outside geofence: {engineer.name} at {site_name}, "
        f"{distance_m:.0f}m from site centre."
    )

    alert = Alert(
        engineer_id=engineer.id,
        attendance_id=attendance_id,
        alert_type="geofence_breach",
        message=alert_message,
    )
    db.add(alert)

    supervisors = db.query(Supervisor).filter(Supervisor.active == True).all()  # noqa: E712
    wa_text = msg.msg_geofence_breach_supervisor_alert(
        engineer_name=engineer.name,
        site_name=site_name,
        event_type=event_type,
        distance_m=distance_m,
    )

    for supervisor in supervisors:
        try:
            msg.send_message(supervisor.whatsapp_number, wa_text)
            logger.warning(
                "Geofence breach alert sent to %s: %s", supervisor.name, alert_message
            )
        except Exception as exc:
            logger.error(
                "Failed to send geofence alert to supervisor %s: %s",
                supervisor.name, exc,
            )

    if supervisors:
        alert.whatsapp_sent = True

    db.commit()
