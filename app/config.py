from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str

    # Twilio
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_whatsapp_number: str          # e.g. whatsapp:+14155238886

    # Geofencing
    geofence_radius_meters: float = 200  # engineers must be within this distance

    # Scheduling — 24-hour format, server local time (UTC on Railway)
    daily_summary_time: str = "17:00"    # time to send the daily summary
    checkout_reminder_time: str = "16:30" # remind engineers who forgot to check out

    # Optional: supervisor WhatsApp numbers (comma-separated)
    # e.g. whatsapp:+254700000001,whatsapp:+254700000002
    supervisor_numbers: str = ""

    # Web dashboard session signing key — generate with:
    #   python3 -c "import secrets; print(secrets.token_hex(32))"
    # Must be set in .env; no default so it cannot accidentally be left empty.
    session_secret: str

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
