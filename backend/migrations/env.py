from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

import app.extraction.models  # noqa: F401
import app.fpl.models  # noqa: F401
import app.retrieval.models  # noqa: F401
import app.tweets.models  # noqa: F401
import app.worker.models  # noqa: F401
from app.core.settings import load_settings, normalize_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def get_url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("url") or config.get_main_option(
        "sqlalchemy.url"
    )
    if not url:
        url = load_settings().database_url
    return normalize_database_url(url)


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
