"""
Alembic environment configuration.

Reads DATABASE_URL from the project .env file (via python-dotenv) so that
`alembic upgrade head --sql` and `alembic upgrade head` both work locally
without any extra environment setup.

Target metadata is wired to the SQLAlchemy models in app/models/db.py so that
autogenerate support works correctly.
"""

import os
from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from alembic import context

# ── Load .env so DATABASE_URL is available ────────────────────────────────────
# Walk up from this file's directory until we find a .env file (handles running
# alembic from different working directories).
_here = Path(__file__).resolve().parent
for _candidate in [_here, _here.parent, _here.parent.parent]:
    _env_file = _candidate / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
        break

# ── Alembic Config object ─────────────────────────────────────────────────────
config = context.config

# Override the placeholder URL in alembic.ini with the real DATABASE_URL.
_db_url = os.environ.get("DATABASE_URL")
if not _db_url:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Ensure your .env file is present and contains DATABASE_URL."
    )
config.set_main_option("sqlalchemy.url", _db_url)

# ── Logging ───────────────────────────────────────────────────────────────────
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Model metadata (for autogenerate support) ─────────────────────────────────
# Import Base AFTER the path is set so app/ is importable.
import sys
sys.path.insert(0, str(_here.parent))

from app.models.db import Base  # noqa: E402
target_metadata = Base.metadata


# ── Migration runners ─────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode (generates SQL without a live DB connection).
    Used by:  alembic upgrade head --sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode (connects to the database and applies changes).
    Used by:  alembic upgrade head
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
