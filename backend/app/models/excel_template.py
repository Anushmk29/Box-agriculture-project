"""Immutable approved/reviewable versions of recurring customer Excel layouts."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, UniqueConstraint

from app.core.database import Base, uuid_pk


class ExcelTemplate(Base):
    __tablename__ = "excel_templates"
    __table_args__ = (
        UniqueConstraint(
            "account_key", "template_key", "version", name="uq_excel_template_version"
        ),
    )
    id = Column(String(36), primary_key=True, default=uuid_pk)
    account_key = Column(String(36), nullable=False, index=True)
    template_key = Column(String(128), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    definition = Column(JSON, nullable=False)
    confirmed = Column(Boolean, nullable=False, default=False)
    confirmed_by = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
