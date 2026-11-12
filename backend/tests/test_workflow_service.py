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


def test_bulk_order_transition_retains_revision_checks_and_audit_events(db_session):
    from test_procurement_handoff import setup_procurement_batch

    from app.models.order import ClientOrder

    buyer, _, first, _, _ = setup_procurement_batch(db_session)
    second = ClientOrder(raw_order_file_id=first.raw_order_file_id, customer_id=first.customer_id,
        order_ref_id="ORD-BULK", status=OrderStatus.PURCHASING_CONSOLIDATED)
    db_session.add(second)
    db_session.commit()
    before = {order.id: order.packing_revision for order in [first, second]}
    WorkflowService(db_session).transition_orders(orders=[first, second],
        to_status=OrderStatus.PROCUREMENT_CONFIRMED, actor=buyer)
    assert all(order.status == OrderStatus.PROCUREMENT_CONFIRMED for order in [first, second])
    assert all(order.packing_revision == before[order.id] + 1 for order in [first, second])
    events = db_session.query(AuditLog).filter_by(event_type="WORKFLOW_TRANSITION").all()
    assert {event.target_id for event in events} == set(before)
    assert all(event.before_state == {"status": "PURCHASING_CONSOLIDATED"} for event in events)
    assert all(event.after_state == {"status": "PROCUREMENT_CONFIRMED"} for event in events)


def test_bulk_order_transition_rejects_stale_revision_and_rolls_back(db_session):
    from sqlalchemy import update
    from sqlalchemy.orm.exc import StaleDataError
    from test_procurement_handoff import setup_procurement_batch

    from app.models.order import ClientOrder

    buyer, _, first, _, _ = setup_procurement_batch(db_session)
    second = ClientOrder(raw_order_file_id=first.raw_order_file_id, customer_id=first.customer_id,
        order_ref_id="ORD-STALE", status=OrderStatus.PURCHASING_CONSOLIDATED)
    db_session.add(second)
    db_session.commit()
    db_session.execute(update(ClientOrder).where(ClientOrder.id == second.id)
        .values(packing_revision=ClientOrder.packing_revision + 1)
        .execution_options(synchronize_session=False))
    with pytest.raises(StaleDataError, match="Orders changed"):
        WorkflowService(db_session).transition_orders(orders=[first, second],
            to_status=OrderStatus.PROCUREMENT_CONFIRMED, actor=buyer)
    db_session.rollback()
    assert first.status == second.status == OrderStatus.PURCHASING_CONSOLIDATED
    assert db_session.query(AuditLog).filter_by(event_type="WORKFLOW_TRANSITION").count() == 0
