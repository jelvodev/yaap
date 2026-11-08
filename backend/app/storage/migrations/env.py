from alembic import context
from sqlalchemy import create_engine

from app.storage.models import Base

config = context.config


def run_migrations_online() -> None:
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    # No connection stays open after the migrations, so the database file can be replaced (a restore).
    engine.dispose()


run_migrations_online()
