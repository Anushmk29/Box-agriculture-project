import pytest

from app.models.audit import AuditLog
from app.models.order import OrderStatus, RawOrderFile
from app.models.user import User, UserRole
from app.modules.workflow.service import WorkflowService, WorkflowTransitionError


def test_transition_persists_state_and_audit_event(db_session):
    actor = User(
        email="agent1@example.com",
        password_hash="unused",
        full_name="Agent One",
        role=UserRole.AI_AGENT_1,
        is_service_principal=True,
    )
    db_session.add(actor)
    db_session.flush()
    raw_file = RawOrderFile(
        file_name="order.xlsx",
        storage_url="orders/order.xlsx",
        shipment_target_date="2026-09-10",
        route_line="TETE",
        uploaded_by=actor.id,
        status=OrderStatus.UPLOADED,
    )
    db_session.add(raw_file)
    db_session.commit()

    WorkflowService(db_session).transition(
        target=raw_file,
        to_status=OrderStatus.PARSED,
        actor=actor,
        target_table="raw_order_files",
    )
    db_session.commit()

    assert raw_file.status == OrderStatus.PARSED
    event = db_session.query(AuditLog).one()
    assert event.event_type == "WORKFLOW_TRANSITION"
    assert event.before_state == {"status": "UPLOADED"}
    assert event.after_state == {"status": "PARSED"}


def test_transition_rejects_skipping_required_human_gates(db_session):
    actor = User(
        email="agent1@example.com",
        password_hash="unused",
        full_name="Agent One",
        role=UserRole.AI_AGENT_1,
        is_service_principal=True,
    )
    db_session.add(actor)
    db_session.flush()
    raw_file = RawOrderFile(
        file_name="order.xlsx",
        storage_url="orders/order.xlsx",
        shipment_target_date="2026-09-10",
        route_line="TETE",
        uploaded_by=actor.id,
        status=OrderStatus.UPLOADED,
    )
    db_session.add(raw_file)
    db_session.commit()

    with pytest.raises(WorkflowTransitionError, match="UPLOADED -> PACKING_DESIGNED"):
        WorkflowService(db_session).transition(
            target=raw_file,
            to_status=OrderStatus.PACKING_DESIGNED,
            actor=actor,
            target_table="raw_order_files",
        )
