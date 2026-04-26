"""
Scheduled jobs.

Two jobs run on a daily schedule:
  1. checkout_reminder  — sent at 16:30 to engineers still checked in
  2. daily_summary      — sent at 17:00 to all supervisors

APScheduler runs inside the FastAPI process (no separate worker needed
at this scale).  Jobs are scheduled using cron triggers based on the
times configured in settings.
"""

import logging
from datetime import date, datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.models.db import Attendance, Engineer, Supervisor, get_session_factory
from app.services import messaging as msg

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


# ── Checkout reminder ─────────────────────────────────────────────────────────

def send_checkout_reminders() -> None:
    """Send a WhatsApp reminder to any engineer still checked in."""
    factory = get_session_factory()
    db = factory()
    try:
        today = date.today()
        open_records = (
            db.query(Attendance)
            .filter(
                Attendance.work_date == today,
                Attendance.check_out_time.is_(None),
                Attendance.reminder_sent == False,
            )
            .all()
        )

        for record in open_records:
            engineer = record.engineer
            site_name = record.site.name if record.site else "your site"
            try:
                msg.send_message(
                    engineer.whatsapp_number,
                    msg.msg_reminder(engineer.name, site_name),
                )
                record.reminder_sent = True
                logger.info("Reminder sent to %s", engineer.name)
            except Exception as e:
                logger.error("Failed to send reminder to %s: %s", engineer.name, e)

        db.commit()
    finally:
        db.close()


# ── Daily summary ─────────────────────────────────────────────────────────────

def _build_summary_text(today: date, db) -> str:
    records = (
        db.query(Attendance)
        .filter(Attendance.work_date == today)
        .all()
    )

    if not records:
        return f"*Daily Attendance Summary -- {today.strftime('%d %b %Y')}*\n\nNo check-ins recorded today."

    lines = [f"*Daily Attendance Summary -- {today.strftime('%d %b %Y')}*\n"]

    checked_out   = [r for r in records if r.check_out_time]
    still_on_site = [r for r in records if not r.check_out_time]
    flagged       = [r for r in records if r.flagged]

    lines.append(f"Total check-ins: {len(records)}")
    lines.append(f"Checked out: {len(checked_out)}")
    lines.append(f"Still on site: {len(still_on_site)}")
    lines.append(f"Flagged: {len(flagged)}")
    lines.append("")

    if checked_out:
        lines.append("*Completed:*")
        for r in checked_out:
            hours = r.hours_on_site or 0
            site_name = r.site.name if r.site else "Unknown"
            flag = " ⚠" if r.flagged else ""
            lines.append(f"  {r.engineer.name} -- {site_name} -- {hours:.1f}h{flag}")

    if still_on_site:
        lines.append("")
        lines.append("*Still on site:*")
        for r in still_on_site:
            site_name = r.site.name if r.site else "Unknown"
            lines.append(f"  {r.engineer.name} -- {site_name}")

    if flagged:
        lines.append("")
        lines.append("*Flagged for review:*")
        for r in flagged:
            site_name = r.site.name if r.site else "Unknown"
            lines.append(f"  {r.engineer.name} -- {site_name}: {r.flag_reason}")

    # Engineers with no record today
    all_engineers = db.query(Engineer).filter(Engineer.active == True).all()
    recorded_ids  = {r.engineer_id for r in records}
    absent        = [e for e in all_engineers if e.id not in recorded_ids]
    if absent:
        lines.append("")
        lines.append("*No check-in recorded:*")
        for e in absent:
            lines.append(f"  {e.name}")

    return "\n".join(lines)


def send_daily_summary() -> None:
    """Build and dispatch the daily summary to all active supervisors."""
    factory = get_session_factory()
    db = factory()
    try:
        today = date.today()
        summary_text = _build_summary_text(today, db)

        supervisors = db.query(Supervisor).filter(Supervisor.active == True).all()
        if not supervisors:
            logger.warning("No active supervisors — daily summary not sent.")
            return

        for supervisor in supervisors:
            try:
                msg.send_message(supervisor.whatsapp_number, summary_text)
                logger.info("Daily summary sent to %s", supervisor.name)
            except Exception as e:
                logger.error("Failed to send summary to %s: %s", supervisor.name, e)
    finally:
        db.close()


# ── Scheduler setup ───────────────────────────────────────────────────────────

def start_scheduler() -> None:
    settings = get_settings()

    # Parse "HH:MM" strings from config
    reminder_h, reminder_m = settings.checkout_reminder_time.split(":")
    summary_h, summary_m   = settings.daily_summary_time.split(":")

    scheduler.add_job(
        send_checkout_reminders,
        trigger=CronTrigger(hour=int(reminder_h), minute=int(reminder_m)),
        id="checkout_reminder",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        send_daily_summary,
        trigger=CronTrigger(hour=int(summary_h), minute=int(summary_m)),
        id="daily_summary",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.start()
    logger.info(
        "Scheduler started. Reminder at %s, summary at %s (UTC).",
        settings.checkout_reminder_time,
        settings.daily_summary_time,
    )


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
