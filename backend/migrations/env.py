"""Alembic env. Migrations run as the schema owner (MIGRATION_DATABASE_URL), never as ft_app."""
from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings

url = context.config.attributes.get("url") or get_settings().owner_db_url

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
