"""Durable, backend-only write receipts. Never expire keys into reusable intents."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, String

from app.core.database import Base


class RequestResult(Base):
    __tablename__ = "request_results"

    # No cascading FK: deleting an account must not erase its replay protection.
    actor_id = Column(String(36), primary_key=True)
    operation = Column(String(64), primary_key=True)
    request_key = Column(String(128), primary_key=True)
    request_sha256 = Column(String(64), nullable=False)
    response_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
