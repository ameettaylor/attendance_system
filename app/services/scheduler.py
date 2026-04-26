"""
Scheduled jobs.

Jobs
----
checkout_reminder       — daily at CHECKOUT_REMINDER_TIME (UTC, from settings)
                          Reminds engineers still checked in to check out.

daily_summary           — daily at DAILY_SUMMARY_TIME (UTC, from settings)
                          Sends attendance summary to all active supervisors.

send_evening_notifications — every minute
                          For each tomorrow's allocation where notification_sent=False
                          and the engineer's preferred_notification_time matches the
                          current EAT HH:MM, sends job-details message and marks sent.
                          Engineers without a preferred_notification_time are skipped.

send_morning_reminders  — daily at 04:00 UTC (07:00 EAT)
                          Sends a morning-of reminder for every today's allocation
                          where morning_reminder_sent=False.

check_late_checkins     — every minute
                          For each today's allocation with a scheduled_start_time,
                          if the engineer has no check-in and it is 30+ minutes past
                          the scheduled start, creates an Alert record and WhatsApps
                          all active supervisors. Deduplicates via the alerts table.

APScheduler runs inside the FastAPI process — no separate worker needed.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.models.db import (
    Alert, Allocation, Attendance, Engineer, Supervisor,
    get_session_factory,
)
from app.services import messaging as msg

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

EAT = timezone(timedelta(hours=3))
LATE_CHECKIN_MINUTES = 30   # minutes past scheduled_start_time before alert fires


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_db():
    factory = get_session_factory()
    return factory()


def _utc_to_eat_hhmm(dt_utc: datetime) -> str:
    """Convert a naive UTC datetime to an EAT HH:MM string."""
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(EAT).strftime("%H:%M")


def _utc_to_eat_time_str(dt_utc: datetime) -> str:
    """Return a human-readable EAT time string, e.g. '08:30 EAT'."""
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(EAT).strftime("%H:%M EAT")


def _maps_url(latitude: float, longitude: float) -> str:
    return f"https://www.google.com/maps?q={latitude},{longitude}"


# ── Existing jobs (unchanged) ─────────────────────────────────────────────────

def send_checkout_reminders() -> None:
    """Send a WhatsApp reminder to any engineer still checked in."""
    db = _get_db()
    try:
        today = date.today()
        open_records = (
            db.query(Attendance)
            .filter(
                Attendance.work_date == today,
                Attendance.check_out_time.is_(None),
                Attendance.reminder_sent == False,  # noqa: E712
            )
            .all()
        )

        for record in open_records:
            engineer  = record.engineer
            site_name = record.site.name if record.site else "your site"
            try:
                msg.send_message(
                    engineer.whatsapp_number,
                    msg.msg_reminder(engineer.name, site_name),
                )
                record.reminder_sent = True
                logger.info("Checkout reminder sent to %s", engineer.name)
            except Exception as exc:
                logger.error("Reminder failed for %s: %s", engineer.name, exc)

        db.commit()
    finally:
        db.close()


def _build_summary_text(today: date, db) -> str:
    records = (
        db.query(Attendance)
        .filter(Attendance.work_date == today)
        .all()
    )

    if not records:
        return (
            f"*Daily Attendance Summary — {today.strftime('%d %b %Y')}*\n\n"
            "No check-ins recorded today."
        )

    lines = [f"*Daily Attendance Summary — {today.strftime('%d %b %Y')}*\n"]

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
            hours     = r.hours_on_site or 0
            site_name = r.site.name if r.site else "Unknown"
            flag      = " ⚠" if r.flagged else ""
            lines.append(f"  {r.engineer.name} — {site_name} — {hours:.1f}h{flag}")

    if still_on_site:
        lines.append("")
        lines.append("*Still on site:*")
        for r in still_on_site:
            site_name = r.site.name if r.site else "Unknown"
            lines.append(f"  {r.engineer.name} — {site_name}")

    if flagged:
        lines.append("")
        lines.append("*Flagged for review:*")
        for r in flagged:
            site_name = r.site.name if r.site else "Unknown"
            lines.append(f"  {r.engineer.name} — {site_name}: {r.flag_reason}")

    all_engineers = db.query(Engineer).filter(Engineer.active == True).all()  # noqa: E712
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
    db = _get_db()
    try:
        today        = date.today()
        summary_text = _build_summary_text(today, db)
        supervisors  = db.query(Supervisor).filter(Supervisor.active == True).all()  # noqa: E712

        if not supervisors:
            logger.warning("No active supervisors — daily summary not sent.")
            return

        for supervisor in supervisors:
            try:
                msg.send_message(supervisor.whatsapp_number, summary_text)
                logger.info("Daily summary sent to %s", supervisor.name)
            except Exception as exc:
                logger.error("Summary failed for %s: %s", supervisor.name, exc)
    finally:
        db.close()


# ── New job 1: evening-before notifications ───────────────────────────────────

def send_evening_notifications() -> None:
    """
    Runs every minute.

    For each allocation scheduled for TOMORROW where notification_sent=False,
    check whether the engineer's preferred_notification_time (HH:MM, EAT) matches
    the current EAT minute.  If so, send the job-details WhatsApp and mark sent.

    Engineers with no preferred_notification_time are silently skipped — their
    notification time must be configured in their profile first.
    """
    now_eat          = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(EAT)
    current_hhmm     = now_eat.strftime("%H:%M")
    tomorrow         = date.today() + timedelta(days=1)
    tomorrow_display = tomorrow.strftime("%A, %d %b %Y")   # e.g. "Tuesday, 27 Apr 2026"

    db = _get_db()
    try:
        pending = (
            db.query(Allocation)
            .filter(
                Allocation.work_date == tomorrow,
                Allocation.notification_sent == False,  # noqa: E712
            )
            .all()
        )

        for allocation in pending:
            engineer = allocation.engineer
            notif_time = engineer.preferred_notification_time  # "HH:MM" EAT or None

            if not notif_time:
                continue   # not configured — skip

            if notif_time != current_hhmm:
                continue   # not their notification minute yet

            site = allocation.site
            sched_str = (
                _utc_to_eat_time_str(allocation.scheduled_start_time)
                if allocation.scheduled_start_time else ""
            )

            try:
                msg.send_message(
                    engineer.whatsapp_number,
                    msg.msg_evening_notification(
                        engineer_name=engineer.name,
                        site_name=site.name,
                        address=site.address or "",
                        work_date_str=tomorrow_display,
                        scheduled_time_eat=sched_str,
                        work_description=allocation.work_description or "",
                        maps_url=_maps_url(site.latitude, site.longitude),
                    ),
                )
                allocation.notification_sent = True
                logger.info(
                    "Evening notification sent: engineer=%s site=%s date=%s",
                    engineer.name, site.name, tomorrow,
                )
            except Exception as exc:
                logger.error(
                    "Evening notification failed for %s: %s", engineer.name, exc
                )

        db.commit()
    finally:
        db.close()


# ── New job 2: morning-of reminders ──────────────────────────────────────────

def send_morning_reminders() -> None:
    """
    Runs once daily at 04:00 UTC (07:00 EAT).

    For each allocation today where morning_reminder_sent=False, send a
    reminder message and mark morning_reminder_sent=True.
    """
    today = date.today()
    db    = _get_db()
    try:
        allocations = (
            db.query(Allocation)
            .filter(
                Allocation.work_date == today,
                Allocation.morning_reminder_sent == False,  # noqa: E712
            )
            .all()
        )

        for allocation in allocations:
            engineer = allocation.engineer
            site     = allocation.site
            sched_str = (
                _utc_to_eat_time_str(allocation.scheduled_start_time)
                if allocation.scheduled_start_time else ""
            )

            try:
                msg.send_message(
                    engineer.whatsapp_number,
                    msg.msg_morning_reminder(
                        engineer_name=engineer.name,
                        site_name=site.name,
                        scheduled_time_eat=sched_str,
                        maps_url=_maps_url(site.latitude, site.longitude),
                    ),
                )
                allocation.morning_reminder_sent = True
                logger.info(
                    "Morning reminder sent: engineer=%s site=%s",
                    engineer.name, site.name,
                )
            except Exception as exc:
                logger.error(
                    "Morning reminder failed for %s: %s", engineer.name, exc
                )

        db.commit()
    finally:
        db.close()


# ── New job 3: late check-in alerts ──────────────────────────────────────────

def check_late_checkins() -> None:
    """
    Runs every minute.

    For each today's allocation that has a scheduled_start_time:
      - If it is now more than LATE_CHECKIN_MINUTES past the scheduled start
      - And the engineer has not checked in today
      - And no 'late_checkin' alert already exists for this allocation
    → Create an Alert record and WhatsApp all active supervisors.
    """
    now_utc = datetime.utcnow()
    today   = date.today()
    cutoff  = now_utc - timedelta(minutes=LATE_CHECKIN_MINUTES)

    db = _get_db()
    try:
        # Allocations today with a scheduled start that has passed the grace window
        candidates = (
            db.query(Allocation)
            .filter(
                Allocation.work_date == today,
                Allocation.scheduled_start_time.isnot(None),
                Allocation.scheduled_start_time <= cutoff,
            )
            .all()
        )

        if not candidates:
            return

        # Fetch checked-in engineer IDs for today (any check-in counts)
        checked_in_ids = {
            row.engineer_id
            for row in db.query(Attendance).filter(
                Attendance.work_date == today
            ).all()
        }

        # Fetch existing late_checkin alert allocation IDs to avoid duplicates
        alerted_allocation_ids = {
            row.allocation_id
            for row in db.query(Alert).filter(
                Alert.alert_type == "late_checkin",
                Alert.allocation_id.isnot(None),
            ).all()
        }

        supervisors = (
            db.query(Supervisor)
            .filter(Supervisor.active == True)  # noqa: E712
            .all()
        )

        for allocation in candidates:
            if allocation.engineer_id in checked_in_ids:
                continue   # already checked in — no alert needed

            if allocation.id in alerted_allocation_ids:
                continue   # alert already fired for this allocation

            engineer  = allocation.engineer
            site      = allocation.site
            sched_str = _utc_to_eat_hhmm(allocation.scheduled_start_time)

            minutes_late = int(
                (now_utc - allocation.scheduled_start_time).total_seconds() // 60
            )

            alert_message = (
                f"{engineer.name} has not checked in at {site.name}. "
                f"Scheduled {sched_str} EAT, now {minutes_late} min overdue."
            )

            # Create alert record
            alert = Alert(
                engineer_id=engineer.id,
                allocation_id=allocation.id,
                alert_type="late_checkin",
                message=alert_message,
            )
            db.add(alert)

            # WhatsApp all active supervisors
            wa_text = msg.msg_late_checkin_supervisor_alert(
                engineer_name=engineer.name,
                site_name=site.name,
                scheduled_time_eat=sched_str,
                minutes_overdue=minutes_late,
            )
            for supervisor in supervisors:
                try:
                    msg.send_message(supervisor.whatsapp_number, wa_text)
                    logger.warning(
                        "Late check-in alert sent: engineer=%s site=%s overdue=%dmin",
                        engineer.name, site.name, minutes_late,
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to send late alert to supervisor %s: %s",
                        supervisor.name, exc,
                    )

            # Mark alert as WhatsApp-sent if at least one supervisor exists
            if supervisors:
                alert.whatsapp_sent = True

        db.commit()
    finally:
        db.close()


# ── Scheduler setup ───────────────────────────────────────────────────────────

def start_scheduler() -> None:
    settings = get_settings()

    reminder_h, reminder_m = settings.checkout_reminder_time.split(":")
    summary_h,  summary_m  = settings.daily_summary_time.split(":")

    # ── Existing daily jobs ───────────────────────────────────────────────────
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

    # ── New: morning reminder at 04:00 UTC (07:00 EAT) ───────────────────────
    scheduler.add_job(
        send_morning_reminders,
        trigger=CronTrigger(hour=4, minute=0),
        id="morning_reminders",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # ── New: per-minute jobs for evening notifications and late check-ins ─────
    scheduler.add_job(
        send_evening_notifications,
        trigger=IntervalTrigger(minutes=1),
        id="evening_notifications",
        replace_existing=True,
        misfire_grace_time=60,
    )

    scheduler.add_job(
        check_late_checkins,
        trigger=IntervalTrigger(minutes=1),
        id="late_checkin_check",
        replace_existing=True,
        misfire_grace_time=60,
    )

    scheduler.start()
    logger.info(
        "Scheduler started. "
        "Checkout reminder: %s UTC | Daily summary: %s UTC | "
        "Morning reminder: 04:00 UTC | "
        "Evening notifications + late check-in checks: every minute.",
        settings.checkout_reminder_time,
        settings.daily_summary_time,
    )


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
