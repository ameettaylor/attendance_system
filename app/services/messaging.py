"""
Twilio WhatsApp messaging service.

All outbound messages go through send_message().
The Twilio client is instantiated once and reused.
"""

from twilio.rest import Client
from app.config import get_settings

_client = None


def _get_client() -> Client:
    global _client
    if _client is None:
        s = get_settings()
        _client = Client(s.twilio_account_sid, s.twilio_auth_token)
    return _client


def send_message(to: str, body: str) -> str:
    """
    Send a WhatsApp message via Twilio.

    Args:
        to:   Recipient in whatsapp:+XXXXXXXXXXX format.
        body: Plain text message body.

    Returns:
        Twilio message SID.
    """
    settings = get_settings()
    client = _get_client()
    message = client.messages.create(
        from_=settings.twilio_whatsapp_number,
        to=to,
        body=body,
    )
    return message.sid


# ── Canned message templates ──────────────────────────────────────────────────
# Keep all user-facing text here so it is easy to update or translate.

def msg_welcome_checkin(engineer_name: str, site_name: str) -> str:
    return (
        f"Hi {engineer_name}! To check in at *{site_name}*, "
        f"please share your current location using the WhatsApp attachment button "
        f"(paperclip icon) > Location > Send Your Current Location."
    )


def msg_checkin_confirmed(site_name: str, distance_m: float, time_str: str) -> str:
    return (
        f"Check-in confirmed at *{site_name}* at {time_str}. "
        f"You are {distance_m:.0f}m from the site centre. "
        f"Reply *OUT* when you are ready to check out."
    )


def msg_checkin_outside_geofence(site_name: str, distance_m: float) -> str:
    return (
        f"Location received. You appear to be *{distance_m:.0f}m* from *{site_name}* "
        f"which is outside the allowed radius. Your check-in has been logged and "
        f"flagged for supervisor review. Reply *OUT* when you leave."
    )


def msg_checkin_no_assignment() -> str:
    return (
        "You do not have a site assignment for today. "
        "Please contact your supervisor to confirm your schedule."
    )


def msg_already_checked_in(site_name: str) -> str:
    return (
        f"You are already checked in at *{site_name}* today. "
        f"Reply *OUT* to check out."
    )


def msg_checkout_prompt(engineer_name: str) -> str:
    return (
        f"Hi {engineer_name}, checking out? Please share your current location "
        f"to confirm you are still on site."
    )


def msg_checkout_confirmed(site_name: str, hours: float, time_str: str) -> str:
    return (
        f"Checked out of *{site_name}* at {time_str}. "
        f"Time on site: *{hours:.1f} hours*. Have a safe trip!"
    )


def msg_checkout_outside_geofence(site_name: str, distance_m: float) -> str:
    return (
        f"Check-out recorded. You were *{distance_m:.0f}m* from *{site_name}* -- "
        f"this has been flagged for supervisor review."
    )


def msg_checkout_no_checkin() -> str:
    return (
        "No active check-in found for today. "
        "Reply *IN* to check in first."
    )


def msg_not_registered() -> str:
    return (
        "Your number is not registered in the attendance system. "
        "Please contact your supervisor."
    )


def msg_reminder(engineer_name: str, site_name: str) -> str:
    return (
        f"Hi {engineer_name}, a reminder to check out of *{site_name}* "
        f"if you have finished for the day. Reply *OUT* to check out."
    )


def msg_help() -> str:
    return (
        "Available commands:\n"
        "*IN* -- check in to your assigned site\n"
        "*OUT* -- check out from your current site\n"
        "*STATUS* -- see your check-in status for today\n\n"
        "For help contact your supervisor."
    )


def msg_status_checked_in(site_name: str, time_str: str) -> str:
    return f"You are checked in at *{site_name}* since {time_str}."


def msg_status_checked_out(site_name: str, hours: float) -> str:
    return f"You checked out of *{site_name}* today after {hours:.1f} hours on site."


def msg_status_not_checked_in() -> str:
    return "You have not checked in today. Reply *IN* to check in."
