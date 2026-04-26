# WhatsApp Field Engineer Time & Attendance System

## Project Overview

A WhatsApp-based time and attendance system for field engineers (security systems integrators). Engineers check in and out of work sites via WhatsApp. The system verifies their GPS location against the assigned site coordinates using a geofencing engine, logs attendance to a PostgreSQL database, and sends a daily summary report to supervisors.

Built and deployed by Ameet Taylor in April 2026.

---

## Architecture

```
Engineer's WhatsApp
        |
        v
   Twilio (BSP)
        |  POST /webhook/twilio
        v
   FastAPI (Railway)
        |
        +-- Conversation State Machine (in-memory)
        +-- Geofencing Engine (Haversine, no external API)
        +-- Attendance Service (check-in / check-out logic)
        +-- APScheduler (daily reminder + summary jobs)
        |
        v
   PostgreSQL (Railway managed)
```

### Tech Stack

| Component | Choice | Reason |
|---|---|---|
| Language | Python 3.9+ | Compatible with macOS system Python |
| Web framework | FastAPI | Async, fast, clean Form() support for Twilio |
| Database ORM | SQLAlchemy 2.0 | Mature, well supported |
| Database | PostgreSQL (Railway managed) | Included with Railway Pro |
| WhatsApp BSP | Twilio | Easiest sandbox for development |
| Hosting | Railway | Usage-based billing, managed Postgres, auto-deploy from GitHub |
| Geofencing | Custom Haversine formula | No external API, works offline |
| Scheduler | APScheduler | Runs inside the FastAPI process, no separate worker needed |

---

## Cost Summary (as of April 2026)

| Item | Cost |
|---|---|
| Startup (development) | $3,000 - $8,000 (once-off) |
| Domain registration | ~$12/year |
| Railway Pro hosting | $20 - $30/month |
| Twilio + Meta messaging (20 engineers) | ~$15 - $25/month |
| Google Maps API (reverse geocoding) | $0 (within free tier) |
| **Total monthly running** | **$36 - $61/month** |
| **Year 1 total** | **~$3,444 - $8,744** |

Assumptions: 20 engineers, 4 messages/day each, 22 working days/month, Kenya (Rest of Africa Meta pricing).

---

## Project Structure

```
attendance/
  app/
    main.py                   FastAPI app entry point, lifespan (DB init, scheduler)
    config.py                 Pydantic settings from environment variables
    models/
      db.py                   SQLAlchemy models + engine/session factory
    routers/
      webhook.py              Twilio POST webhook -- main message handler
    services/
      geofence.py             Haversine distance calculation
      attendance.py           Check-in / check-out business logic
      messaging.py            Twilio send_message + all message templates
      state.py                In-memory conversation state machine
      scheduler.py            APScheduler -- daily reminder + summary jobs
  scripts/
    admin.py                  CLI to manage engineers, sites, assignments, supervisors
  tests/
    test_geofence.py          Unit tests for geofencing engine
  requirements.txt
  railway.toml                Railway deployment config
  .env.example                Environment variable template
  .gitignore
```

---

## Database Schema

### engineers
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| name | String(120) | Full name |
| whatsapp_number | String(30) | Format: whatsapp:+254XXXXXXXXX |
| active | Boolean | Set to False to deactivate |
| created_at | DateTime | |

### sites
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| name | String(120) | |
| address | Text | Optional human-readable address |
| latitude | Float | WGS84 decimal degrees |
| longitude | Float | WGS84 decimal degrees |
| geofence_radius_meters | Float | Overrides global default if set |
| active | Boolean | |

### assignments
Links an engineer to a site for a specific date.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| engineer_id | FK engineers | |
| site_id | FK sites | |
| work_date | Date | YYYY-MM-DD |

### attendance
One row per check-in event. check_out_* fields are NULL until the engineer checks out.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| engineer_id | FK engineers | |
| site_id | FK sites | NULL if unrecognised site |
| work_date | Date | |
| check_in_time | DateTime | UTC |
| check_in_latitude | Float | |
| check_in_longitude | Float | |
| check_in_distance_m | Float | Distance from site centre at check-in |
| check_in_within_geofence | Boolean | |
| check_out_time | DateTime | NULL if still on site |
| check_out_latitude | Float | |
| check_out_longitude | Float | |
| check_out_distance_m | Float | |
| check_out_within_geofence | Boolean | |
| flagged | Boolean | True if either event was outside geofence |
| flag_reason | Text | Human-readable flag description |
| reminder_sent | Boolean | True if checkout reminder was sent |

### supervisors
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| name | String(120) | |
| whatsapp_number | String(30) | Format: whatsapp:+254XXXXXXXXX |
| active | Boolean | |

---

## Bot Conversation Flow

```
Engineer sends: IN
Bot: "Hi [Name]! To check in at [Site], please share your current location..."
Engineer shares location (WhatsApp paperclip > Location > Send Your Current Location)
Bot (within geofence):  "Check-in confirmed at [Site] at HH:MM UTC. You are Xm from the site centre."
Bot (outside geofence): "You appear to be Xm from [Site] which is outside the allowed radius. Check-in logged and flagged."

Engineer sends: OUT
Bot: "Hi [Name], checking out? Please share your current location..."
Engineer shares location
Bot: "Checked out of [Site] at HH:MM UTC. Time on site: X.X hours."

Engineer sends: STATUS
Bot: current check-in status for today

Engineer sends: HELP
Bot: available commands
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in all values.

| Variable | Description | Example |
|---|---|---|
| DATABASE_URL | PostgreSQL connection string | postgresql://user:pass@host:port/db |
| TWILIO_ACCOUNT_SID | From Twilio console > Account Dashboard | ACxxxxxxxx... |
| TWILIO_AUTH_TOKEN | From Twilio console > Account Dashboard | your_token |
| TWILIO_WHATSAPP_NUMBER | Twilio sandbox or dedicated number | whatsapp:+14155238886 |
| GEOFENCE_RADIUS_METERS | Default geofence radius in metres | 200 |
| CHECKOUT_REMINDER_TIME | UTC time for checkout reminder (HH:MM) | 13:30 (= 16:30 EAT) |
| DAILY_SUMMARY_TIME | UTC time for daily summary (HH:MM) | 14:00 (= 17:00 EAT) |

Note: Railway servers run UTC. Nairobi (EAT) is UTC+3.

---

## Installation and Setup

### Prerequisites

- Python 3.9 or higher
- A GitHub account
- A Railway account (railway.app)
- A Twilio account (twilio.com)

### Step 1 -- Local Setup

```bash
# Clone your repo
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO

# Install dependencies
# On macOS with system Python use pip3
pip3 install -r requirements.txt

# Create your .env file
cp .env.example .env
# Edit .env and fill in all values
```

### Step 2 -- Railway Setup

1. Create a new project at railway.app
2. Add a new service > Database > PostgreSQL
3. Add a new service > Deploy from GitHub repo > select your repo
4. In the GitHub service > Settings > Root Directory, set to `attendance` if your project is in a subfolder
5. In the GitHub service > Variables > Raw Editor, paste all environment variables from your `.env` file
6. For DATABASE_URL, either paste the value directly from Railway's PostgreSQL service > Connect tab, or use the `${{DATABASE_URL}}` reference syntax
7. Railway will auto-deploy on every git push

### Step 3 -- Twilio Setup

1. Create a Twilio account and select the Trial plan
2. Go to Messaging > Try it out > Send a WhatsApp message
3. Note your sandbox join code (e.g. `join sleep-west`) and sandbox number (`+14155238886`)
4. Go to Sandbox settings tab
5. Set "When a message comes in" to: `https://YOUR-RAILWAY-DOMAIN.up.railway.app/webhook/twilio`
6. Set method to HTTP POST
7. Save

### Step 4 -- Initialise Data

Run these commands from the project root with your `.env` present:

```bash
# Add engineers (number in E.164 format)
python3 scripts/admin.py engineer add "Jane Kamau" "+254700000001"
python3 scripts/admin.py engineer add "John Mwangi" "+254700000002"

# Add sites (get coordinates from Google Maps: right-click > copy coordinates)
python3 scripts/admin.py site add "Westlands Office" "Westlands Rd, Nairobi" -1.2634 36.8031
python3 scripts/admin.py site add "Karen Site" "Karen, Nairobi" -1.3190 36.7127

# Assign engineers to sites for today
python3 scripts/admin.py assign 1 1 2026-04-27
python3 scripts/admin.py assign 2 2 2026-04-27

# Assign for multiple consecutive days
python3 scripts/admin.py assign 1 1 2026-04-28 --bulk 5

# Add supervisors who receive the daily summary
python3 scripts/admin.py supervisor add "Alice Manager" "+254700000099"
```

### Step 5 -- Engineer Onboarding (Sandbox)

Each engineer must send the sandbox join message to `+1 415 523 8886` on WhatsApp before the bot will respond to them:

```
join sleep-west
```

This sandbox requirement goes away when you upgrade to a paid Twilio number and complete Meta Business verification.

---

## Admin CLI Reference

```bash
# Engineers
python3 scripts/admin.py engineer add "Name" "+254XXXXXXXXX"
python3 scripts/admin.py engineer list
python3 scripts/admin.py engineer deactivate ID

# Sites
python3 scripts/admin.py site add "Name" "Address" LATITUDE LONGITUDE
python3 scripts/admin.py site list
python3 scripts/admin.py site radius SITE_ID METRES

# Assignments
python3 scripts/admin.py assign ENGINEER_ID SITE_ID YYYY-MM-DD
python3 scripts/admin.py assign ENGINEER_ID SITE_ID YYYY-MM-DD --bulk N

# Supervisors
python3 scripts/admin.py supervisor add "Name" "+254XXXXXXXXX"
python3 scripts/admin.py supervisor list

# Reports
python3 scripts/admin.py report today       # print today's attendance to terminal
python3 scripts/admin.py summary            # trigger the WhatsApp summary now
```

---

## Running Tests

```bash
pip3 install pytest
python3 -m pytest tests/ -v
```

All 7 geofencing unit tests should pass.

---

## Scheduled Jobs

| Job | Default time | Purpose |
|---|---|---|
| checkout_reminder | 13:30 UTC (16:30 EAT) | Remind engineers still checked in |
| daily_summary | 14:00 UTC (17:00 EAT) | Send attendance summary to all supervisors |

Times are configurable via `CHECKOUT_REMINDER_TIME` and `DAILY_SUMMARY_TIME` in `.env`.

---

## Going Live (Production)

When ready to move from the Twilio sandbox to a real WhatsApp number:

1. Upgrade Twilio to Pay as you go
2. Complete Meta WhatsApp Business Account (WABA) verification via Twilio's onboarding. Allow 5-10 business days.
3. Purchase a dedicated Twilio WhatsApp-enabled number (~$1/month)
4. Get Meta approval for the message templates used for business-initiated messages (the checkout reminder). User-initiated conversations (engineer sends IN first) do not require templates.
5. Update `TWILIO_WHATSAPP_NUMBER` in Railway variables to the new number
6. Update the webhook URL in Twilio console to point to your Railway domain
7. Engineers no longer need to send a join code -- they can message directly

---

## Known Issues and Fixes Applied

### Python 3.9 Compatibility
The `X | Y` union type hint syntax requires Python 3.10+. On macOS with system Python 3.9, use plain `dict` and remove `-> Type | None` return annotations. Railway runs Python 3.12 and is not affected.

Files patched for 3.9 compatibility:
- `app/models/db.py` -- `hours_on_site(self)` return annotation removed
- `app/services/attendance.py` -- return annotations removed from three functions
- `app/services/messaging.py` -- `_client: Client | None = None` changed to `_client = None`
- `app/services/state.py` -- `dict[str, ConversationState]` changed to `dict`

### Missing python-multipart
FastAPI requires `python-multipart` to handle form data (which is how Twilio sends webhook payloads). This was missing from the original `requirements.txt` and caused a startup crash. Added as `python-multipart==0.0.20`.

### psycopg2-binary Version
Version 2.9.10 does not have a pre-built wheel for macOS ARM (Apple Silicon). Use `psycopg2-binary==2.9.12` which does. Railway (Linux x86_64) works with either version.

### DATABASE_URL Reference in Railway
Using the `${{DATABASE_URL}}` reference syntax in Railway Variables sometimes resolves to an empty string. Workaround: paste the PostgreSQL connection string directly as a plain value in the Raw Editor.

---

## Security Notes

- Rotate your Twilio Auth Token and Railway database password if they are ever exposed in logs, chat, or version control
- Never commit `.env` to GitHub (it is in `.gitignore`)
- The Twilio webhook does not currently validate the `X-Twilio-Signature` header (validation code is present in the original webhook.py but commented out for simplicity). Enable this in production to prevent spoofed webhook calls
- The conversation state machine is in-memory and resets if the Railway process restarts. Engineers would simply need to type IN or OUT again, which is low impact

---

## Future Enhancements

- Web dashboard for supervisors (read-only attendance log with map view)
- Integration with job scheduling / ERP system for automatic site assignments
- Twilio Signature validation on the webhook
- Redis-backed state store for multi-worker deployments
- Weekly / monthly attendance reports
- Export to CSV or Excel
