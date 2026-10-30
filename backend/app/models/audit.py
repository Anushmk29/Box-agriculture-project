from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, String

from app.core.database import Base, uuid_pk


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    event_type = Column(String(64), nullable=False, index=True)
    actor_id = Column(String(36), nullable=False, index=True)
    actor_role = Column(String(32), nullable=False)
    target_table = Column(String(64), nullable=False)
    target_id = Column(String(36), nullable=False)
    before_state = Column(JSON, nullable=True)
    after_state = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class AgentHandoff(Base):
    __tablename__ = "agent_handoffs"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    handoff_type = Column(
        String(32), nullable=False, index=True
    )  # 'HANDOFF_01_ORDER_DATA' | 'HANDOFF_02_FINAL_PACKING'
    sender_agent = Column(String(32), nullable=False)
    recipient_agent = Column(String(32), nullable=False)
    client_order_id = Column(
        String(36), ForeignKey("client_orders.id"), nullable=True, index=True
    )  # set for HANDOFF_02 (one order per contract)
    payload_json = Column(JSON, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    is_acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
