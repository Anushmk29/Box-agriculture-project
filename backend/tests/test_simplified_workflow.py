import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from test_packing_lifecycle import setup_order
from test_procurement_handoff import setup_procurement_batch

from app.api.packing import packing_approval_queue
from app.core.database import get_db
from app.core.read_models import LegacyPage
from app.core.security import create_access_token
from app.main import app
from app.models.audit import AgentHandoff, AuditLog
from app.models.order import OrderStatus
from app.modules.handoffs.service import HandoffService
from app.modules.packing.service import FinalPackingService, PackingPlanningService
from app.modules.procurement.service import ProcurementConfirmation, ProcurementConfirmationService


@pytest.fixture
def transactional_client(db_session):
    def transaction():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = transaction
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.clear()


def headers(actor):
    return {"Authorization": f"Bearer {create_access_token({'sub': actor.id, 'role': actor.role.value})}"}


def test_staff_confirm_and_dispatch_is_atomic_and_retryable(db_session, transactional_client):
    buyer, _, order, batch, item = setup_procurement_batch(db_session)
    body = {"items": [{"item_id": item.id, "purchased_pack_count": 11, "purchased_weight_kg": 11}]}
    for _ in range(2):
        response = transactional_client.put(f"/api/v3/procurement/batches/{batch.id}/confirm", json=body, headers=headers(buyer))
        assert response.status_code == 200
        assert response.json()["status"] == "HANDOFF_01_SENT"
    assert order.status == OrderStatus.HANDOFF_01_SENT
    assert db_session.query(AgentHandoff).count() == 1
    assert db_session.query(AuditLog).filter_by(event_type="PROCUREMENT_CONFIRMED").count() == 1


def test_confirmed_batch_can_resume_handoff(db_session):
    buyer, _, _, batch, item = setup_procurement_batch(db_session)
    service = ProcurementConfirmationService(db_session)
    service.confirm_batch(batch_id=batch.id, confirmations=[ProcurementConfirmation(item.id, 11, 11)], actor=buyer)
    service.confirm_and_dispatch(batch_id=batch.id, confirmations=[], actor=buyer)
    assert batch.status == OrderStatus.HANDOFF_01_SENT
    assert db_session.query(AgentHandoff).count() == 1


def test_confirmed_purchase_retry_cannot_silently_change_actuals(db_session):
    buyer, _, _, batch, item = setup_procurement_batch(db_session)
    service = ProcurementConfirmationService(db_session)
    service.confirm_and_dispatch(batch_id=batch.id, confirmations=[ProcurementConfirmation(item.id, 11, 11)], actor=buyer)
    with pytest.raises(ValueError, match="different values"):
        service.confirm_and_dispatch(batch_id=batch.id, confirmations=[ProcurementConfirmation(item.id, 10, 10)], actor=buyer)
    assert item.confirmed_purchased_pack_count == 11
    assert db_session.query(AgentHandoff).count() == 1


def test_failed_handoff_rolls_back_purchases(db_session, transactional_client, monkeypatch):
    buyer, _, order, batch, item = setup_procurement_batch(db_session)
    def fail(*args, **kwargs):
        raise ValueError("handoff failed")
    monkeypatch.setattr(HandoffService, "seal_handoff_01", fail)
    response = transactional_client.put(f"/api/v3/procurement/batches/{batch.id}/confirm", headers=headers(buyer),
        json={"items": [{"item_id": item.id, "purchased_pack_count": 11, "purchased_weight_kg": 11}]})
    assert response.status_code == 400
    db_session.refresh(batch)
    db_session.refresh(item)
    assert batch.status == OrderStatus.PURCHASING_CONSOLIDATED
    assert order.status == OrderStatus.PURCHASING_CONSOLIDATED
    assert item.confirmed_purchased_pack_count is None
    assert db_session.query(AgentHandoff).count() == 0
    assert db_session.query(AuditLog).count() == 0


def prepared(db):
    _, packer, _, order = setup_order(db)
    service = PackingPlanningService(db)
    service.prepare_order(order_id=order.id, actor=packer)
    box = order_box(db, order)
    return packer, order, box


def order_box(db, order):
    from app.models.packing import Box
    return db.query(Box).filter_by(client_order_id=order.id).one()


def test_prepare_resumes_without_regenerating_contents(db_session):
    packer, order, box = prepared(db_session)
    item_id = box.items[0].id
    for _ in range(2):
        PackingPlanningService(db_session).prepare_order(order_id=order.id, actor=packer)
    assert order_box(db_session, order).id == box.id
    assert box.items[0].id == item_id
    assert order.status == OrderStatus.FLOOR_PACKING


@pytest.mark.parametrize("invalid_net", [-1, 100])
def test_finalization_rejects_invalid_packed_contents(db_session, invalid_net):
    packer, order, box = prepared(db_session)
    box.items[0].final_net_weight_kg = invalid_net
    db_session.flush()
    with pytest.raises(ValueError):
        FinalPackingService(db_session).finalize_order(order_id=order.id, actor=packer)
    assert not box.is_locked
    assert box.actual_net_weight_kg is None
    assert order.status == OrderStatus.FLOOR_PACKING


def test_finalization_validates_all_boxes_before_locking(db_session):
    from app.modules.packing.boxes import new_box
    packer, order, box = prepared(db_session)
    empty = new_box(order, box.box_catalogue_id, 2, route_code=box.route_code)
    db_session.add(empty)
    db_session.flush()
    with pytest.raises(ValueError, match="has no packed contents"):
        FinalPackingService(db_session).finalize_order(order_id=order.id, actor=packer)
    assert not box.is_locked and not empty.is_locked
    assert box.actual_net_weight_kg is None
    assert db_session.query(AgentHandoff).count() == 0


@pytest.mark.parametrize("approval", ["NONE", "PENDING", "REJECTED"])
def test_staff_can_finalize_and_retry_preserving_approval_history(db_session, approval):
    packer, order, box = prepared(db_session)
    order.packing_approval_status = approval
    order.packing_review_note = "Historical note"
    service = FinalPackingService(db_session)
    _, handoff = service.finalize_order(order_id=order.id, actor=packer)
    _, retry = service.finalize_order(order_id=order.id, actor=packer)
    assert retry.id == handoff.id
    assert order.status == OrderStatus.HANDOFF_02_RETURNED
    assert box.is_locked and box.packed_by_staff == packer.id
    assert order.packing_approval_status == approval
    assert order.packing_review_note == "Historical note"
    assert db_session.query(AgentHandoff).count() == 1
    with pytest.raises(ValueError):
        FinalPackingService(db_session).lock_order(order_id=order.id, actor=packer)
    assert packing_approval_queue(Response(), LegacyPage(offset=None, limit=None), db_session, packer) == []


def test_failed_final_handoff_rolls_back_lock(db_session, transactional_client, monkeypatch):
    packer, order, box = prepared(db_session)
    db_session.commit()
    def fail(*args, **kwargs):
        raise ValueError("handoff failed")
    monkeypatch.setattr(HandoffService, "seal_handoff_02", fail)
    response = transactional_client.post(f"/api/v3/packing/orders/{order.id}/finalize", headers=headers(packer), json={"expected_revision": order.packing_revision})
    assert response.status_code == 409
    db_session.refresh(order)
    db_session.refresh(box)
    assert order.status == OrderStatus.FLOOR_PACKING and not box.is_locked
    assert box.actual_net_weight_kg is None
    assert db_session.query(AuditLog).filter_by(event_type="PACKING_FINALIZED").count() == 0


@pytest.mark.parametrize("method,path", [("put", "/orders/{order_id}/weights"), ("patch", "/boxes/{box_id}/weights")])
def test_manual_weight_endpoints_are_removed(db_session, transactional_client, method, path):
    packer, order, box = prepared(db_session)
    response = getattr(transactional_client, method)(
        "/api/v3/packing" + path.format(order_id=order.id, box_id=box.id),
        headers=headers(packer), json={"actual_net_weight_kg": 99, "actual_gross_weight_kg": 100})
    assert response.status_code == 404
    assert box.actual_net_weight_kg is None


def test_new_operations_require_authenticated_humans(db_session, transactional_client):
    agent, _, _, order = setup_order(db_session)
    assert transactional_client.post(f"/api/v3/packing/orders/{order.id}/finalize").status_code == 401
    assert transactional_client.post(f"/api/v3/packing/orders/{order.id}/finalize", headers=headers(agent)).status_code == 403


def test_finalize_without_scale_entry_completes_for_staff(db_session, transactional_client):
    packer, order, box = prepared(db_session)
    db_session.commit()
    first = transactional_client.post(f"/api/v3/packing/orders/{order.id}/finalize", headers=headers(packer), json={"expected_revision": order.packing_revision})
    latest = transactional_client.get(f"/api/v3/packing/orders/{order.id}/packing-list", headers=headers(packer)).json()
    second = transactional_client.post(f"/api/v3/packing/orders/{order.id}/finalize", headers=headers(packer), json={"expected_revision": latest["packing_revision"]})
    assert first.status_code == second.status_code == 200
    assert first.json()["handoff_id"] == second.json()["handoff_id"]
    assert second.json()["status"] == "HANDOFF_02_RETURNED"
    assert float(box.actual_net_weight_kg) == 4
    assert float(box.actual_gross_weight_kg) == 4.5
    handoff = db_session.query(AgentHandoff).one()
    assert handoff.payload_json["total_shipment_net_weight_kg"] == 4
    assert handoff.payload_json["total_shipment_gross_weight_kg"] == 4.5
    assert transactional_client.get(f"/api/v3/packing/scan/{box.qr_code_uid}", headers=headers(packer)).json()["can_edit"] is False
