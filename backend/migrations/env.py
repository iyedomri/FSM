import asyncio
from alembic import context
from db import Base, engine


def apply(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    async with engine.connect() as connection:
        await connection.run_sync(apply)
    await engine.dispose()


if context.config.attributes.get("connection") is not None:
    apply(context.config.attributes["connection"])
elif context.is_offline_mode():
    from config import settings
    context.configure(url=settings.database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
