"""
Database models.

Tables:
    engineers    — registered field technicians (identified by WhatsApp number)
    sites        — work sites with GPS coordinates and geofence radius
    assignments  — which engineer is assigned to which site on which date
    attendance   — check-in / check-out records with coordinates and timestamps
    supervisors  — WhatsApp numbers that receive the daily summary
    customers    — customer accounts linked to allocations
    agents       — dispatcher / supervisor web dashboard login accounts
    allocations  — new dispatch table (multiple techs per site per day)
    logs         — progress reports + material requests from the WhatsApp bot
    alerts       — late check-in and geofence breach records
"""

from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, Date, ForeignKey, Text, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from app.config import get_settings

Base = declarative_base()


class Engineer(Base):
    __tablename__ = "engineers"

    id              = Column(Integer, primary_key=True)
    name            = Column(String(120), nullable=False)
    whatsapp_number = Column(String(30), unique=True, nullable=False)  # whatsapp:+254...
    active          = Column(Boolean, default=True, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    assignments = relationship("Assignment", back_populates="engineer")
    attendance  = relationship("Attendance", back_populates="engineer")

    def __repr__(self):
        return f"<Engineer {self.name} ({self.whatsapp_number})>"


class Site(Base):
    __tablename__ = "sites"

    id                    = Column(Integer, primary_key=True)
    name                  = Column(String(120), nullable=False)
    address               = Column(Text, nullable=True)
    latitude              = Column(Float, nullable=False)
    longitude             = Column(Float, nullable=False)
    geofence_radius_meters = Column(Float, nullable=True)  # overrides global default if set
    active                = Column(Boolean, default=True, nullable=False)
    created_at            = Column(DateTime, default=datetime.utcnow, nullable=False)

    assignments = relationship("Assignment", back_populates="site")
    attendance  = relationship("Attendance", back_populates="site")

    def __repr__(self):
        return f"<Site {self.name} ({self.latitude}, {self.longitude})>"


class Assignment(Base):
    """Links an engineer to a site for a specific date."""
    __tablename__ = "assignments"

    id          = Column(Integer, primary_key=True)
    engineer_id = Column(Integer, ForeignKey("engineers.id"), nullable=False)
    site_id     = Column(Integer, ForeignKey("sites.id"), nullable=False)
    work_date   = Column(Date, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    engineer = relationship("Engineer", back_populates="assignments")
    site     = relationship("Site", back_populates="assignments")

    def __repr__(self):
        return f"<Assignment engineer={self.engineer_id} site={self.site_id} date={self.work_date}>"


class Attendance(Base):
    """
    One row per check-in event.  check_out_* fields are populated when
    the engineer checks out.  A NULL check_out_time means still on site.
    """
    __tablename__ = "attendance"

    id          = Column(Integer, primary_key=True)
    engineer_id = Column(Integer, ForeignKey("engineers.id"), nullable=False)
    site_id     = Column(Integer, ForeignKey("sites.id"), nullable=True)   # NULL if unrecognised site
    work_date   = Column(Date, nullable=False)

    # Check-in
    check_in_time      = Column(DateTime, nullable=False)
    check_in_latitude  = Column(Float, nullable=False)
    check_in_longitude = Column(Float, nullable=False)
    check_in_distance_m = Column(Float, nullable=True)  # distance from site centre at check-in
    check_in_within_geofence = Column(Boolean, nullable=False, default=False)

    # Check-out (populated later)
    check_out_time      = Column(DateTime, nullable=True)
    check_out_latitude  = Column(Float, nullable=True)
    check_out_longitude = Column(Float, nullable=True)
    check_out_distance_m = Column(Float, nullable=True)
    check_out_within_geofence = Column(Boolean, nullable=True)

    # Flags
    flagged         = Column(Boolean, default=False, nullable=False)  # True if either event was outside geofence
    flag_reason     = Column(Text, nullable=True)
    reminder_sent   = Column(Boolean, default=False, nullable=False)  # True if checkout reminder was sent

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    engineer = relationship("Engineer", back_populates="attendance")
    site     = relationship("Site", back_populates="attendance")

    @property
    def hours_on_site(self):
        if self.check_in_time and self.check_out_time:
            delta = self.check_out_time - self.check_in_time
            return round(delta.total_seconds() / 3600, 2)
        return None

    def __repr__(self):
        return f"<Attendance engineer={self.engineer_id} date={self.work_date} in={self.check_in_time}>"


class Supervisor(Base):
    __tablename__ = "supervisors"

    id              = Column(Integer, primary_key=True)
    name            = Column(String(120), nullable=False)
    whatsapp_number = Column(String(30), unique=True, nullable=False)
    active          = Column(Boolean, default=True, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Supervisor {self.name} ({self.whatsapp_number})>"


class Customer(Base):
    __tablename__ = "customers"

    id            = Column(Integer, primary_key=True)
    name          = Column(String(120), nullable=False)
    contact_name  = Column(String(120), nullable=True)
    contact_phone = Column(String(30), nullable=True)
    address       = Column(Text, nullable=True)
    active        = Column(Boolean, default=True, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)

    allocations = relationship("Allocation", back_populates="customer")

    def __repr__(self):
        return f"<Customer {self.name}>"


class Agent(Base):
    """Web dashboard login accounts for dispatchers and supervisors."""
    __tablename__ = "agents"

    id            = Column(Integer, primary_key=True)
    username      = Column(String(60), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    email         = Column(String(120), unique=True, nullable=True)
    role          = Column(String(20), nullable=False, default="dispatcher")
    # role values: dispatcher | supervisor | admin
    active        = Column(Boolean, default=True, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Agent {self.username} ({self.role})>"


class Allocation(Base):
    """
    New dispatch table that replaces direct use of assignments for scheduling.
    Supports multiple technicians per site per day.
    """
    __tablename__ = "allocations"

    id                    = Column(Integer, primary_key=True)
    engineer_id           = Column(Integer, ForeignKey("engineers.id"), nullable=False)
    site_id               = Column(Integer, ForeignKey("sites.id"), nullable=False)
    customer_id           = Column(Integer, ForeignKey("customers.id"), nullable=True)
    work_date             = Column(Date, nullable=False)
    scheduled_start_time  = Column(DateTime, nullable=True)   # UTC
    work_description      = Column(Text, nullable=True)
    notification_sent     = Column(Boolean, default=False, nullable=False)
    morning_reminder_sent = Column(Boolean, default=False, nullable=False)
    created_at            = Column(DateTime, default=datetime.utcnow, nullable=False)

    engineer = relationship("Engineer")
    site     = relationship("Site")
    customer = relationship("Customer", back_populates="allocations")
    logs     = relationship("Log", back_populates="allocation")
    alerts   = relationship("Alert", back_populates="allocation")

    def __repr__(self):
        return f"<Allocation engineer={self.engineer_id} site={self.site_id} date={self.work_date}>"


class Log(Base):
    """Progress reports and material requests submitted via the WhatsApp bot."""
    __tablename__ = "logs"

    id            = Column(Integer, primary_key=True)
    engineer_id   = Column(Integer, ForeignKey("engineers.id"), nullable=False)
    attendance_id = Column(Integer, ForeignKey("attendance.id"), nullable=True)
    allocation_id = Column(Integer, ForeignKey("allocations.id"), nullable=True)
    log_type      = Column(String(30), nullable=False)  # progress_report | material_request
    content       = Column(Text, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)

    engineer   = relationship("Engineer")
    attendance = relationship("Attendance")
    allocation = relationship("Allocation", back_populates="logs")

    def __repr__(self):
        return f"<Log engineer={self.engineer_id} type={self.log_type}>"


class Alert(Base):
    """Late check-in and geofence breach records."""
    __tablename__ = "alerts"

    id            = Column(Integer, primary_key=True)
    engineer_id   = Column(Integer, ForeignKey("engineers.id"), nullable=False)
    allocation_id = Column(Integer, ForeignKey("allocations.id"), nullable=True)
    attendance_id = Column(Integer, ForeignKey("attendance.id"), nullable=True)
    alert_type    = Column(String(30), nullable=False)  # late_checkin | geofence_breach
    message       = Column(Text, nullable=False)
    resolved      = Column(Boolean, default=False, nullable=False)
    whatsapp_sent = Column(Boolean, default=False, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)

    engineer   = relationship("Engineer")
    allocation = relationship("Allocation", back_populates="alerts")
    attendance = relationship("Attendance")

    def __repr__(self):
        return f"<Alert engineer={self.engineer_id} type={self.alert_type} resolved={self.resolved}>"


# ── Database session factory ──────────────────────────────────────────────────

def get_engine():
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True)


def get_session_factory():
    engine = get_engine()
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db():
    """Create all tables. Safe to call repeatedly (CREATE IF NOT EXISTS)."""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
