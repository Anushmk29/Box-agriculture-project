"""Versioned staff decisions and observed packing outcomes."""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from app.core.database import Base, uuid_pk


class PackingHistoryReview(Base):
    __tablename__ = "packing_history_reviews"
    __table_args__ = (
        UniqueConstraint("example_id", name="uq_packing_history_example"),
        CheckConstraint("revision > 0", name="ck_history_review_revision"),
        CheckConstraint("status IN ('APPROVED', 'REJECTED')", name="ck_history_review_status"),
    )
    id = Column(String(36), primary_key=True, default=uuid_pk)
    example_id = Column(String(64), nullable=False, index=True)
    source_version = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    definition = Column(JSON, nullable=False)
    note = Column(String(2048), nullable=False)
    reviewed_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class PackingFeedback(Base):
    __tablename__ = "packing_feedback"
    __table_args__ = (
        UniqueConstraint("box_id", "packing_revision", "outcome", name="uq_packing_feedback_box_revision_outcome"),
        CheckConstraint("outcome IN ('FIT', 'LARGER_BOX', 'EXCESS_SPACE', 'REPACKED', 'DAMAGE')", name="ck_packing_feedback_outcome"),
    )
    id = Column(String(36), primary_key=True, default=uuid_pk)
    box_id = Column(String(36), ForeignKey("boxes.id"), nullable=False, index=True)
    packing_revision = Column(Integer, nullable=False)
    outcome = Column(String(24), nullable=False)
    note = Column(String(2048), nullable=False)
    snapshot = Column(JSON, nullable=False)
    actor_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
