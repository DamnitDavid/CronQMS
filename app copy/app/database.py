"""Database configuration and session management."""

from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.config import get_settings

# Get settings
settings = get_settings()

# Create engine with pool configuration for production
engine = create_engine(
    settings.database_url,
    echo=settings.database_echo,
    # Using StaticPool for SQLite in tests, NullPool for production
    pool_pre_ping=True,  # Verify connections before using them
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for all models
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Get database session for dependency injection.

    Yields:
        Session: SQLAlchemy database session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initialize database by creating all tables.

    Should be called on application startup.
    """
    Base.metadata.create_all(bind=engine)


def drop_db() -> None:
    """Drop all tables from database.

    Warning: This is destructive and should only be used in development.
    """
    Base.metadata.drop_all(bind=engine)
