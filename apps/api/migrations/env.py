"""Alembic environment. Uses the application settings and the complete metadata."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from terrax.config import get_settings
from terrax.db import Base
from terrax import models  # noqa: F401  (registers every table)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata

_OUR_TABLES = set(Base.metadata.tables)


def include_name(name, type_, parent_names):
    """Never touch PostGIS extension tables that share the public schema."""
    if type_ == "schema":
        return name in (None, "public")
    if type_ == "table":
        return name in _OUR_TABLES
    return True


def include_object(object_, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in _OUR_TABLES
    if type_ == "index" and reflected and name and name.startswith(
        ("idx_", "tige_", "spatial_ref_sys")
    ):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=include_name,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_name=include_name,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
