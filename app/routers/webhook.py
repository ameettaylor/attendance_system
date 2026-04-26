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

from app.config import get_settings
from app.models.db import Engineer, get_session_factory
from app.services import messaging as msg
from app.services import attendance as att
from app.services.state import (
    ConversationStep, get_state, set_state, clear_state
)

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
            _send_checkin_response(sender, result)

        elif state.step == ConversationStep.AWAITING_CHECKOUT_LOCATION:
            clear_state(sender)
            result = att.process_checkout(db, engineer, latitude, longitude)
            _send_checkout_response(sender, result)

        else:
            # Unsolicited location — ignore gracefully
            msg.send_message(sender, "Location received but no active check-in/out in progress. Reply *IN* to check in.")

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

def _send_checkin_response(sender: str, result: dict) -> None:
    status = result["status"]
    if status == "confirmed":
        msg.send_message(sender, msg.msg_checkin_confirmed(
            result["site_name"], result["distance_m"], result["time_str"]
        ))
    elif status == "outside_geofence":
        msg.send_message(sender, msg.msg_checkin_outside_geofence(
            result["site_name"], result["distance_m"]
        ))
    elif status == "no_assignment":
        msg.send_message(sender, msg.msg_checkin_no_assignment())
    elif status == "already_checked_in":
        msg.send_message(sender, msg.msg_already_checked_in(result["site_name"]))


def _send_checkout_response(sender: str, result: dict) -> None:
    status = result["status"]
    if status == "confirmed":
        msg.send_message(sender, msg.msg_checkout_confirmed(
            result["site_name"], result["hours"] or 0, result["time_str"]
        ))
    elif status == "outside_geofence":
        msg.send_message(sender, msg.msg_checkout_outside_geofence(
            result["site_name"], result["distance_m"]
        ))
    elif status == "no_checkin":
        msg.send_message(sender, msg.msg_checkout_no_checkin())


def _handle_status(sender: str, engineer: Engineer, db: Session) -> None:
    record = att.get_todays_attendance(db, engineer)
    if not record:
        msg.send_message(sender, msg.msg_status_not_checked_in())
    elif record.check_out_time is None:
        time_str = record.check_in_time.strftime("%H:%M UTC")
        site_name = record.site.name if record.site else "unknown site"
        msg.send_message(sender, msg.msg_status_checked_in(site_name, time_str))
    else:
        site_name = record.site.name if record.site else "unknown site"
        msg.send_message(sender, msg.msg_status_checked_out(site_name, record.hours_on_site or 0))
