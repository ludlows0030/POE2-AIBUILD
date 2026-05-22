"""数据库连接与会话管理。"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.postgres_url,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=20,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI 依赖：获取异步数据库会话。"""
    async with async_session_factory() as session:
        yield session
