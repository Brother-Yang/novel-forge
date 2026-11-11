import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app import models  # noqa: F401  注册全部表
from app.config import settings
from app.db_base import Base

config = context.config
# Explicit programmatic URLs win over environment configuration (tests, admin
# scripts). CLI invocations without an override use the application settings.
url = config.attributes.get("database_url") or config.get_main_option("sqlalchemy.url")
if not url or url == "sqlite:///./data/novel.db":
    url = settings.database_url
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    try:
        with connectable.connect() as connection:
            sqlite = connection.dialect.name == "sqlite"
            if sqlite:
                # SQLite batch operations rebuild referenced tables. Foreign
                # keys must be disabled outside a transaction or DROP TABLE can
                # cascade into existing manuscripts. App connections stay ON.
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
            try:
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    render_as_batch=True,
                )
                with context.begin_transaction():
                    context.run_migrations()
                connection.commit()
                if sqlite:
                    violations = connection.exec_driver_sql(
                        "PRAGMA foreign_key_check"
                    ).fetchall()
                    if violations:
                        raise RuntimeError(
                            f"Migration left invalid foreign keys: {violations!r}"
                        )
            finally:
                if sqlite:
                    if connection.in_transaction():
                        connection.rollback()
                    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    if (
                        connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
                        != 1
                    ):
                        raise RuntimeError(
                            "Could not enable SQLite foreign-key constraints"
                        )
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
