#!/usr/bin/env python3
"""
Admin CLI — manage engineers, sites, assignments, and supervisors.

Usage (run from the project root with your .env loaded):

    python scripts/admin.py engineer add "Jane Doe" "+254700000001"
    python scripts/admin.py engineer list
    python scripts/admin.py engineer deactivate 3

    python scripts/admin.py site add "Westlands Office" "Westlands Rd, Nairobi" -1.2634 36.8031
    python scripts/admin.py site list
    python scripts/admin.py site radius 2 150    # set custom geofence for site 2

    python scripts/admin.py assign 1 2 2025-05-01          # engineer 1 -> site 2 on date
    python scripts/admin.py assign 1 2 2025-05-01 --bulk 5 # assign for 5 consecutive days

    python scripts/admin.py supervisor add "Alice Manager" "+254700000099"
    python scripts/admin.py supervisor list

    python scripts/admin.py report today          # print today's attendance to terminal
    python scripts/admin.py summary               # trigger the daily summary now (sends WhatsApp)
"""

import argparse
import sys
from datetime import date, timedelta, datetime

# Allow running from project root
sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv()

from app.models.db import (
    Engineer, Site, Assignment, Attendance, Supervisor, Agent,
    get_session_factory, init_db,
)
from app.routers.auth import hash_password
from app.services.scheduler import send_daily_summary


def get_db():
    factory = get_session_factory()
    return factory()


# ── Engineer commands ─────────────────────────────────────────────────────────

def cmd_engineer_add(args):
    db = get_db()
    number = f"whatsapp:{args.number}" if not args.number.startswith("whatsapp:") else args.number
    existing = db.query(Engineer).filter(Engineer.whatsapp_number == number).first()
    if existing:
        print(f"ERROR: Engineer with number {number} already exists (id={existing.id}).")
        return
    eng = Engineer(name=args.name, whatsapp_number=number)
    db.add(eng)
    db.commit()
    db.refresh(eng)
    print(f"Added engineer: id={eng.id}  name={eng.name}  number={eng.whatsapp_number}")


def cmd_engineer_list(args):
    db = get_db()
    engineers = db.query(Engineer).order_by(Engineer.id).all()
    if not engineers:
        print("No engineers registered.")
        return
    print(f"{'ID':<5} {'Name':<25} {'WhatsApp Number':<25} {'Active'}")
    print("-" * 65)
    for e in engineers:
        print(f"{e.id:<5} {e.name:<25} {e.whatsapp_number:<25} {'Yes' if e.active else 'No'}")


def cmd_engineer_deactivate(args):
    db = get_db()
    eng = db.query(Engineer).filter(Engineer.id == args.id).first()
    if not eng:
        print(f"ERROR: Engineer id={args.id} not found.")
        return
    eng.active = False
    db.commit()
    print(f"Deactivated: {eng.name}")


# ── Site commands ─────────────────────────────────────────────────────────────

def cmd_site_add(args):
    db = get_db()
    site = Site(
        name=args.name,
        address=args.address,
        latitude=args.latitude,
        longitude=args.longitude,
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    print(f"Added site: id={site.id}  name={site.name}  ({site.latitude}, {site.longitude})")


def cmd_site_list(args):
    db = get_db()
    sites = db.query(Site).order_by(Site.id).all()
    if not sites:
        print("No sites registered.")
        return
    print(f"{'ID':<5} {'Name':<25} {'Lat':>10} {'Lon':>12} {'Radius(m)':>10} {'Active'}")
    print("-" * 75)
    for s in sites:
        radius = f"{s.geofence_radius_meters:.0f}" if s.geofence_radius_meters else "(default)"
        print(f"{s.id:<5} {s.name:<25} {s.latitude:>10.5f} {s.longitude:>12.5f} {radius:>10} {'Yes' if s.active else 'No'}")


def cmd_site_radius(args):
    db = get_db()
    site = db.query(Site).filter(Site.id == args.id).first()
    if not site:
        print(f"ERROR: Site id={args.id} not found.")
        return
    site.geofence_radius_meters = args.radius
    db.commit()
    print(f"Set geofence radius for '{site.name}' to {args.radius}m")


# ── Assignment commands ───────────────────────────────────────────────────────

def cmd_assign(args):
    db = get_db()
    engineer = db.query(Engineer).filter(Engineer.id == args.engineer_id).first()
    site     = db.query(Site).filter(Site.id == args.site_id).first()
    if not engineer:
        print(f"ERROR: Engineer id={args.engineer_id} not found.")
        return
    if not site:
        print(f"ERROR: Site id={args.site_id} not found.")
        return

    try:
        start_date = date.fromisoformat(args.date)
    except ValueError:
        print("ERROR: Date must be in YYYY-MM-DD format.")
        return

    days = args.bulk or 1
    created = 0
    for i in range(days):
        work_date = start_date + timedelta(days=i)
        existing = db.query(Assignment).filter(
            Assignment.engineer_id == engineer.id,
            Assignment.work_date == work_date,
        ).first()
        if existing:
            print(f"  Skipped {work_date} — assignment already exists (site: {existing.site.name})")
            continue
        db.add(Assignment(engineer_id=engineer.id, site_id=site.id, work_date=work_date))
        created += 1

    db.commit()
    print(f"Created {created} assignment(s): {engineer.name} -> {site.name} starting {start_date}")


# ── Supervisor commands ───────────────────────────────────────────────────────

def cmd_supervisor_add(args):
    db = get_db()
    number = f"whatsapp:{args.number}" if not args.number.startswith("whatsapp:") else args.number
    existing = db.query(Supervisor).filter(Supervisor.whatsapp_number == number).first()
    if existing:
        print(f"ERROR: Supervisor with number {number} already exists.")
        return
    sup = Supervisor(name=args.name, whatsapp_number=number)
    db.add(sup)
    db.commit()
    db.refresh(sup)
    print(f"Added supervisor: id={sup.id}  name={sup.name}  number={sup.whatsapp_number}")


def cmd_supervisor_list(args):
    db = get_db()
    supervisors = db.query(Supervisor).order_by(Supervisor.id).all()
    if not supervisors:
        print("No supervisors registered.")
        return
    print(f"{'ID':<5} {'Name':<25} {'WhatsApp Number':<25} {'Active'}")
    print("-" * 65)
    for s in supervisors:
        print(f"{s.id:<5} {s.name:<25} {s.whatsapp_number:<25} {'Yes' if s.active else 'No'}")


# ── Agent commands (web dashboard accounts) ───────────────────────────────────

def cmd_agent_create(args):
    """Create a new web dashboard login account."""
    import getpass
    db = get_db()

    existing = db.query(Agent).filter(Agent.username == args.username).first()
    if existing:
        print(f"ERROR: An agent with username '{args.username}' already exists (id={existing.id}).")
        return

    # Prompt for password interactively (not passed as CLI arg — keeps it off
    # shell history and process listings).
    if args.password:
        password = args.password
    else:
        password = getpass.getpass(f"Password for '{args.username}': ")
        confirm  = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("ERROR: Passwords do not match.")
            return

    if len(password) < 8:
        print("ERROR: Password must be at least 8 characters.")
        return

    role = args.role or "dispatcher"
    valid_roles = ("dispatcher", "supervisor", "admin")
    if role not in valid_roles:
        print(f"ERROR: Role must be one of: {', '.join(valid_roles)}")
        return

    agent = Agent(
        username=args.username.strip(),
        password_hash=hash_password(password),
        email=args.email or None,
        role=role,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    print(f"Created agent: id={agent.id}  username={agent.username}  role={agent.role}")
    print("You can now log in at /login with these credentials.")


def cmd_agent_list(args):
    db = get_db()
    agents = db.query(Agent).order_by(Agent.id).all()
    if not agents:
        print("No agents registered.")
        return
    print(f"{'ID':<5} {'Username':<20} {'Role':<15} {'Email':<30} {'Active'}")
    print("-" * 80)
    for a in agents:
        print(f"{a.id:<5} {a.username:<20} {a.role:<15} {(a.email or ''):<30} {'Yes' if a.active else 'No'}")


def cmd_agent_deactivate(args):
    db = get_db()
    agent = db.query(Agent).filter(Agent.id == args.id).first()
    if not agent:
        print(f"ERROR: Agent id={args.id} not found.")
        return
    agent.active = False
    db.commit()
    print(f"Deactivated agent: {agent.username}")


# ── Report commands ───────────────────────────────────────────────────────────

def cmd_report_today(args):
    db = get_db()
    today = date.today()
    records = (
        db.query(Attendance)
        .filter(Attendance.work_date == today)
        .all()
    )
    print(f"\nAttendance for {today.strftime('%d %b %Y')}")
    print("=" * 70)
    if not records:
        print("No check-ins recorded today.")
        return
    for r in records:
        site_name = r.site.name if r.site else "Unknown"
        checked_in  = r.check_in_time.strftime("%H:%M") if r.check_in_time else "--:--"
        checked_out = r.check_out_time.strftime("%H:%M") if r.check_out_time else "still on site"
        hours = f"{r.hours_on_site:.1f}h" if r.hours_on_site else "--"
        flag  = " [FLAGGED]" if r.flagged else ""
        print(f"  {r.engineer.name:<20} {site_name:<25} in={checked_in}  out={checked_out}  {hours}{flag}")
        if r.flagged and r.flag_reason:
            print(f"    Reason: {r.flag_reason}")


def cmd_summary(args):
    print("Triggering daily summary (this will send WhatsApp messages to supervisors)...")
    send_daily_summary()
    print("Done.")


# ── Argument parser ───────────────────────────────────────────────────────────

def main():
    init_db()

    parser = argparse.ArgumentParser(description="Attendance system admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # engineer
    eng_p = sub.add_parser("engineer")
    eng_sub = eng_p.add_subparsers(dest="subcommand", required=True)

    p = eng_sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("number", help="Phone number e.g. +254700000001")
    p.set_defaults(func=cmd_engineer_add)

    p = eng_sub.add_parser("list")
    p.set_defaults(func=cmd_engineer_list)

    p = eng_sub.add_parser("deactivate")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_engineer_deactivate)

    # site
    site_p = sub.add_parser("site")
    site_sub = site_p.add_subparsers(dest="subcommand", required=True)

    p = site_sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("address")
    p.add_argument("latitude", type=float)
    p.add_argument("longitude", type=float)
    p.set_defaults(func=cmd_site_add)

    p = site_sub.add_parser("list")
    p.set_defaults(func=cmd_site_list)

    p = site_sub.add_parser("radius")
    p.add_argument("id", type=int)
    p.add_argument("radius", type=float, help="Geofence radius in metres")
    p.set_defaults(func=cmd_site_radius)

    # assign
    p = sub.add_parser("assign")
    p.add_argument("engineer_id", type=int)
    p.add_argument("site_id", type=int)
    p.add_argument("date", help="YYYY-MM-DD")
    p.add_argument("--bulk", type=int, default=None, help="Number of consecutive days to assign")
    p.set_defaults(func=cmd_assign)

    # supervisor
    sup_p = sub.add_parser("supervisor")
    sup_sub = sup_p.add_subparsers(dest="subcommand", required=True)

    p = sup_sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("number")
    p.set_defaults(func=cmd_supervisor_add)

    p = sup_sub.add_parser("list")
    p.set_defaults(func=cmd_supervisor_list)

    # agent (web dashboard accounts)
    agent_p = sub.add_parser("agent", help="Manage web dashboard login accounts")
    agent_sub = agent_p.add_subparsers(dest="subcommand", required=True)

    p = agent_sub.add_parser("create", help="Create a new dispatcher/supervisor account")
    p.add_argument("username", help="Login username")
    p.add_argument("--password", default=None,
                   help="Password (omit to be prompted securely)")
    p.add_argument("--email", default=None, help="Optional email address")
    p.add_argument("--role", default="dispatcher",
                   choices=["dispatcher", "supervisor", "admin"],
                   help="Account role (default: dispatcher)")
    p.set_defaults(func=cmd_agent_create)

    p = agent_sub.add_parser("list", help="List all agent accounts")
    p.set_defaults(func=cmd_agent_list)

    p = agent_sub.add_parser("deactivate", help="Deactivate an agent account")
    p.add_argument("id", type=int, help="Agent ID")
    p.set_defaults(func=cmd_agent_deactivate)

    # report
    p = sub.add_parser("report")
    p.add_argument("period", choices=["today"])
    p.set_defaults(func=cmd_report_today)

    # summary
    p = sub.add_parser("summary")
    p.set_defaults(func=cmd_summary)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
