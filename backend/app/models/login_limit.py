"""Shared, short-lived login attempt counters; no email addresses or IPs are stored."""

from sqlalchemy import BigInteger, CheckConstraint, Column, Integer, String

from app.core.database import Base


class LoginLimit(Base):
    __tablename__ = "login_limits"
    __table_args__ = (CheckConstraint("attempts > 0", name="ck_login_limit_attempts"),)

    key = Column(String(64), primary_key=True)
    window_start = Column(BigInteger, nullable=False, index=True)
    attempts = Column(Integer, nullable=False)
