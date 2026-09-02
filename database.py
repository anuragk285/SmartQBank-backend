from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
import os

class Base(DeclarativeBase):
    pass

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///smart-question-bank-database.db")

engine = create_async_engine(SQLALCHEMY_DATABASE_URL, echo=True, 
                            pool_pre_ping=True,      
                            pool_recycle=1800,
                            pool_timeout=30)
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

async def get_db(): 
    async with SessionLocal() as db:
        yield db

