from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, Column, DateTime, Enum, String

from app.core.database import Base, uuid_pk


class UserRole(StrEnum):
    GENERAL = "GENERAL"
    GENERAL_ACCOUNTING = "GENERAL_ACCOUNTING"
    OWNER_ADMIN = "OWNER_ADMIN"
    AI_AGENT_1 = "AI_AGENT_1"
    AI_AGENT_2 = "AI_AGENT_2"

    # Compatibility aliases for scripts and historical records during rollout.
    STAFF = "GENERAL"
    CUSTOMER_AGENT = "GENERAL"
    PROCUREMENT_BUYER = "GENERAL"
    FLOOR_STAFF = "GENERAL"
    OPERATIONS_MANAGER = "GENERAL"
    ACCOUNTING_OFFICER = "GENERAL_ACCOUNTING"
    SYSTEM_ADMIN = "OWNER_ADMIN"


HUMAN_ROLE_RANK = {
    UserRole.GENERAL: 1,
    UserRole.GENERAL_ACCOUNTING: 2,
    UserRole.OWNER_ADMIN: 3,
}
HUMAN_ROLES = frozenset(HUMAN_ROLE_RANK)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.GENERAL)
    is_service_principal = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
