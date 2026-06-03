"""
Database initialisation and session management.

Uses SQLite by default (DATABASE_URL env var overrides to Postgres etc.).
All tables are created on startup via create_all().
"""

import os

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./store_intelligence.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

class DBEvent(Base):
    __tablename__ = "events"

    event_id = Column(String, primary_key=True, index=True)
    store_id = Column(String, index=True, nullable=False)
    camera_id = Column(String, nullable=False)
    visitor_id = Column(String, index=True, nullable=False)
    event_type = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), index=True, nullable=False)
    zone_id = Column(String, nullable=True)
    dwell_ms = Column(Integer, default=0)
    is_staff = Column(Boolean, default=False)
    confidence = Column(Float, nullable=False)
    # metadata stored as JSON text
    meta_queue_depth = Column(Integer, nullable=True)
    meta_sku_zone = Column(String, nullable=True)
    meta_session_seq = Column(Integer, nullable=True)


class DBTransaction(Base):
    """POS transaction records loaded from pos_transactions.csv."""

    __tablename__ = "pos_transactions"

    transaction_id = Column(String, primary_key=True)
    store_id = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime(timezone=True), index=True, nullable=False)
    basket_value_inr = Column(Float, nullable=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session, closes on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
