"""Expand schema: add technician columns to engineers, create customers/agents/allocations/logs/alerts tables.

Revision ID: 0001
Revises:
Create Date: 2026-04-26

Safe to run against the live PoC database:
  - Does NOT drop or rename any existing table.
  - Does NOT touch: assignments, attendance, sites, supervisors.
  - Only ADDS columns (with server-side defaults) and CREATES new tables.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Add new columns to engineers ──────────────────────────────────────
    # All three columns are nullable so existing rows are unaffected.
    op.add_column(
        "engineers",
        sa.Column("technician_type", sa.String(30), nullable=True),
    )
    op.add_column(
        "engineers",
        sa.Column("skill_level", sa.String(30), nullable=True),
    )
    op.add_column(
        "engineers",
        sa.Column(
            "preferred_notification_time",
            sa.String(5),   # stores "HH:MM"
            nullable=True,
            comment="Preferred time to receive evening-before job notification (HH:MM, local EAT).",
        ),
    )

    # ── 2. customers ─────────────────────────────────────────────────────────
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("contact_name", sa.String(120), nullable=True),
        sa.Column("contact_phone", sa.String(30), nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ── 3. agents (dispatcher / supervisor web dashboard users) ───────────────
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(60), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("email", sa.String(120), nullable=True, unique=True),
        sa.Column(
            "role",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'dispatcher'"),
            comment="One of: dispatcher, supervisor, admin",
        ),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ── 4. allocations (new dispatch table — supports multiple techs per site per day) ──
    op.create_table(
        "allocations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "engineer_id",
            sa.Integer,
            sa.ForeignKey("engineers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            sa.Integer,
            sa.ForeignKey("sites.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.Integer,
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("work_date", sa.Date, nullable=False),
        sa.Column(
            "scheduled_start_time",
            sa.DateTime,
            nullable=True,
            comment="UTC datetime for the scheduled start of work.",
        ),
        sa.Column("work_description", sa.Text, nullable=True),
        sa.Column(
            "notification_sent",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
            comment="True once the evening-before WhatsApp notification has been sent.",
        ),
        sa.Column(
            "morning_reminder_sent",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
            comment="True once the 07:00 EAT morning reminder has been sent.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_allocations_work_date", "allocations", ["work_date"])
    op.create_index("ix_allocations_engineer_id", "allocations", ["engineer_id"])

    # ── 5. logs (progress reports + material requests from WhatsApp bot) ──────
    op.create_table(
        "logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "engineer_id",
            sa.Integer,
            sa.ForeignKey("engineers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "attendance_id",
            sa.Integer,
            sa.ForeignKey("attendance.id", ondelete="SET NULL"),
            nullable=True,
            comment="The attendance record this log is associated with.",
        ),
        sa.Column(
            "allocation_id",
            sa.Integer,
            sa.ForeignKey("allocations.id", ondelete="SET NULL"),
            nullable=True,
            comment="The allocation this log is associated with.",
        ),
        sa.Column(
            "log_type",
            sa.String(30),
            nullable=False,
            comment="One of: progress_report, material_request",
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_logs_engineer_id", "logs", ["engineer_id"])
    op.create_index("ix_logs_log_type", "logs", ["log_type"])

    # ── 6. alerts (late check-ins, geofence breaches) ─────────────────────────
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "engineer_id",
            sa.Integer,
            sa.ForeignKey("engineers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "allocation_id",
            sa.Integer,
            sa.ForeignKey("allocations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "attendance_id",
            sa.Integer,
            sa.ForeignKey("attendance.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "alert_type",
            sa.String(30),
            nullable=False,
            comment="One of: late_checkin, geofence_breach",
        ),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column(
            "resolved",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "whatsapp_sent",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
            comment="True once the supervisor WhatsApp alert has been sent.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_alerts_engineer_id", "alerts", ["engineer_id"])
    op.create_index("ix_alerts_alert_type", "alerts", ["alert_type"])
    op.create_index("ix_alerts_resolved", "alerts", ["resolved"])


def downgrade() -> None:
    # Reverse order of creation
    op.drop_table("alerts")
    op.drop_table("logs")
    op.drop_table("allocations")
    op.drop_table("agents")
    op.drop_table("customers")

    op.drop_column("engineers", "preferred_notification_time")
    op.drop_column("engineers", "skill_level")
    op.drop_column("engineers", "technician_type")
