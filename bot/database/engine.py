from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from bot.config import settings

engine = create_async_engine(settings.database_url, echo=False, connect_args={"check_same_thread": False})
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
