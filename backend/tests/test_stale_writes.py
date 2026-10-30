"""Old screens must never overwrite a newer invoice or packing aggregate."""

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from test_invoicing_invariant import build_order_with_box
from test_packing_lifecycle import setup_order
from test_system_resilience_audit import audit_client as audit_client
from test_system_resilience_audit import ready_invoice

from app.core.revisions import RevisionConflict, lock_invoice
from app.models.audit import AuditLog
from app.models.invoice import Invoice
from app.models.order import ClientOrder
from app.models.packing import Box
from app.modules.packing.floor import FloorPackingService
from app.modules.packing.service import PackingPlanningService


def draft_body(invoice, *, revision=None, price=3):
    body = {"items": [{"line_item_id": item.id, "unit_price_eur": price} for item in invoice.items]}
    if revision is not None:
        body["expected_revision"] = revision
    return body


def test_invoice_requires_observed_revision(audit_client, db_session):
    client, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    before = float(invoice.total_amount_eur)
    response = client.patch(f"/api/v3/invoicing/{invoice.id}/draft", json=draft_body(invoice))
    assert response.status_code == 428, response.text
    assert response.json()["detail"]["code"] == "REVISION_REQUIRED"
    db_session.refresh(invoice)
    assert float(invoice.total_amount_eur) == before


def test_two_invoice_editors_preserve_first_save(audit_client, db_session):
    client, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    url = f"/api/v3/invoicing/{invoice.id}"
    first = client.get(url).json()
    second = client.get(url).json()
    assert isinstance(first.get("revision"), int), first
    saved = client.patch(url + "/draft", json=draft_body(invoice, revision=first["revision"], price=3))
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] > first["revision"]
    audits = db_session.query(AuditLog).count()
    stale = client.patch(url + "/draft", json=draft_body(invoice, revision=second["revision"], price=4))
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "STALE_REVISION"
    assert stale.json()["detail"]["current_revision"] == saved.json()["revision"]
    current = client.get(url).json()
    assert current["items"][0]["unit_price_eur"] == 3
    assert db_session.query(AuditLog).count() == audits


def test_note_update_invalidates_stale_draft_and_approval(audit_client, db_session):
    client, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    url = f"/api/v3/invoicing/{invoice.id}"
    revision = client.get(url).json().get("revision", 1)
    note = client.put(url + "/internal-notes", json={"expected_revision": revision, "internal_notes": "Reviewed by A"})
    assert note.status_code == 200, note.text
    draft = client.patch(url + "/draft", json=draft_body(invoice, revision=revision))
    assert draft.status_code == 409, draft.text
    approve = client.post(url + "/approve-dispatch", json={"expected_revision": revision})
    assert approve.status_code == 409, approve.text
    current = client.get(url).json()
    assert current["internal_notes"] == "Reviewed by A"
    assert current["status"] == "INVOICE_DRAFT"
    approved = client.post(url + "/approve-dispatch", json={"expected_revision": current["revision"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["revision"] > current["revision"]


def test_bulk_approval_requires_each_reviewed_revision(audit_client, db_session):
    client, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    response = client.post("/api/v3/invoicing/approve-bulk", json={"invoice_ids": [invoice.id]})
    assert response.status_code == 428, response.text
    db_session.refresh(invoice)
    assert invoice.status.value == "INVOICE_DRAFT"


def test_bulk_stale_selection_does_not_issue_other_invoices(audit_client, db_session):
    client, actor = audit_client
    service, first = ready_invoice(db_session, actor)
    order = build_order_with_box(db_session, locked=True, suffix="SECOND", seller=first.seller_customer)
    second = service.generate_sub_customer_invoice(order.id, actor=actor)
    db_session.commit()
    revisions = {invoice.id: invoice.revision for invoice in (first, second)}
    first.internal_notes = "Changed after selection"
    db_session.commit()
    response = client.post("/api/v3/invoicing/approve-bulk", json={
        "invoice_ids": [second.id, first.id], "expected_revisions": revisions})
    assert response.status_code == 409, response.text
    for invoice in (first, second):
        db_session.refresh(invoice)
        assert invoice.status.value == "INVOICE_DRAFT"


def packing_context(client, db_session):
    agent, packer, _, order = setup_order(db_session)
    PackingPlanningService(db_session).prepare_order(order_id=order.id, actor=packer)
    db_session.commit()
    url = f"/api/v3/packing/orders/{order.id}/packing-list"
    return order, url, client.get(url).json()


def test_packing_revision_covers_every_box(audit_client, db_session):
    client, _ = audit_client
    order, url, packing = packing_context(client, db_session)
    assert isinstance(packing.get("packing_revision"), int), packing
    revision = packing["packing_revision"]
    box = packing["boxes"][0]
    item = box["items"][0]
    body = {"expected_revision": revision, "item_id": item["item_id"], "product_id": item["product_id"],
            "pack_size": item["pack_size"], "quantity": item["packed_count"], "net_weight_kg": item["net_weight_kg"]}
    item_url = f"/api/v3/packing/boxes/{box['box_id']}/items"
    missing = client.put(item_url, json={key: value for key, value in body.items() if key != "expected_revision"})
    assert missing.status_code == 428, missing.text
    saved = client.put(item_url, json=body)
    assert saved.status_code == 200, saved.text
    current = client.get(url).json()
    assert current["packing_revision"] > revision
    stale = client.put(item_url, json=body)
    assert stale.status_code == 409, stale.text
    finalized = client.post(f"/api/v3/packing/orders/{order.id}/finalize", json={"expected_revision": revision})
    assert finalized.status_code == 409, finalized.text
    assert all(not box["is_locked"] for box in client.get(url).json()["boxes"])


def test_stale_size_change_cannot_mutate_box(audit_client, db_session):
    client, _ = audit_client
    _, url, packing = packing_context(client, db_session)
    revision = packing.get("packing_revision", 1)
    box = packing["boxes"][0]
    endpoint = f"/api/v3/packing/boxes/{box['box_id']}/override-size"
    body = {"expected_revision": revision, "new_catalogue_id": box["box_catalogue_id"], "override_reason": "First review"}
    assert client.put(endpoint, json=body).status_code == 200
    stale = client.put(endpoint, json={**body, "override_reason": "Old review"})
    assert stale.status_code == 409, stale.text
    assert client.get(url).json()["packing_revision"] > revision


def test_editing_another_box_invalidates_the_first_box_screen(audit_client, db_session):
    client, actor = audit_client
    order, url, packing = packing_context(client, db_session)
    source = packing["boxes"][0]
    target = FloorPackingService(db_session).add_extra_box_to_order(order.id, source["box_catalogue_id"], actor=actor)
    db_session.commit()
    revision = client.get(url).json()["packing_revision"]
    item = source["items"][0]
    body = {"expected_revision": revision, "product_id": item["product_id"], "pack_size": item["pack_size"],
            "quantity": 1, "net_weight_kg": 1}
    assert client.put(f"/api/v3/packing/boxes/{target.id}/items", json=body).status_code == 200
    stale = client.put(f"/api/v3/packing/boxes/{source['box_id']}/items", json={**body, "item_id": item["item_id"]})
    assert stale.status_code == 409, stale.text
    db_session.refresh(order)
    assert order.packing_revision > revision


@pytest.mark.parametrize("revision", [0, -1, True, "1", 1.5])
def test_revision_must_be_positive_strict_integer(audit_client, db_session, revision):
    client, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    response = client.patch(f"/api/v3/invoicing/{invoice.id}/draft", json=draft_body(invoice, revision=revision))
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("command", ["prepare", "finalize", "start-floor-packing", "confirm-final-pack",
    "request-approval", "reject-approval", "fast-track-pack", "solve-design", "packing-mode",
    "override-size", "add-box", "move-items", "transfer-contents", "group-fast-track"])
def test_every_packing_write_path_checks_the_observed_revision(audit_client, db_session, command):
    client, _ = audit_client
    order, _, packing = packing_context(client, db_session)
    box = packing["boxes"][0]
    body = {"expected_revision": packing["packing_revision"]}
    # Change another property on the aggregate, then submit an old command.
    order.packing_review_note = "Newer review"
    db_session.commit()
    method, path = "post", f"/api/v3/packing/orders/{order.id}/{command}"
    if command in {"prepare", "fast-track-pack", "packing-mode"}:
        body["packing_mode"] = "EL"
    if command == "reject-approval":
        body["note"] = "Old review"
    if command in {"packing-mode", "override-size"}:
        method, path = "put", f"/api/v3/packing/boxes/{box['box_id']}/{command}"
    if command == "add-box":
        body["box_catalogue_id"] = box["box_catalogue_id"]
    if command in {"move-items", "transfer-contents"}:
        path = f"/api/v3/packing/boxes/{command}"
        body.update(source_box_id=box["box_id"], target_box_id=box["box_id"])
        if command == "move-items":
            body.update(product_id=box["items"][0]["product_id"], pack_size="1kg", quantity=1)
    if command == "solve-design":
        path = "/api/v3/packing/solve-design"
        body.update(order_id=order.id, packing_mode="EL")
    if command == "group-fast-track":
        path = "/api/v3/packing/groups/ALL/fast-track-pack"
        body = {"expected_revisions": {order.id: packing["packing_revision"]}, "packing_mode": "EL"}
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "STALE_REVISION"
    assert db_session.get(ClientOrder, order.id).packing_review_note == "Newer review"


def test_finalization_invalidates_pending_item_edit(audit_client, db_session):
    client, _ = audit_client
    order, url, packing = packing_context(client, db_session)
    box = packing["boxes"][0]
    item = box["items"][0]
    revision = packing["packing_revision"]
    finalized = client.post(f"/api/v3/packing/orders/{order.id}/finalize", json={"expected_revision": revision})
    assert finalized.status_code == 200, finalized.text
    stale = client.put(f"/api/v3/packing/boxes/{box['box_id']}/items", json={"expected_revision": revision,
        "item_id": item["item_id"], "product_id": item["product_id"], "pack_size": item["pack_size"],
        "quantity": 1, "net_weight_kg": 1})
    assert stale.status_code == 409, stale.text
    final_box = client.get(url).json()["boxes"][0]
    assert final_box["is_locked"] and final_box["items"][0]["packed_count"] == item["packed_count"]


def test_internal_box_add_and_remove_bump_whole_order(audit_client, db_session):
    client, actor = audit_client
    order, _, packing = packing_context(client, db_session)
    floor = FloorPackingService(db_session)
    revision = order.packing_revision
    box = floor.add_extra_box_to_order(order.id, packing["boxes"][0]["box_catalogue_id"], actor=actor)
    assert order.packing_revision > revision
    source = db_session.get(Box, packing["boxes"][0]["box_id"])
    item = source.items[0]
    revision = order.packing_revision
    floor.save_item(source.id, actor=actor, product_id=item.product_id, pack_size=item.pack_size,
                    item_id=item.id, quantity=0, net_weight_kg=0)
    assert order.packing_revision > revision
    assert not box.is_locked


@pytest.mark.parametrize("kind", ["invoice", "packing"])
def test_mapper_rejects_independent_stale_session_even_without_http_guard(audit_client, db_session, kind):
    _, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    model, identity, field = (Invoice, invoice.id, "internal_notes") if kind == "invoice" else (
        ClientOrder, invoice.client_order_id, "packing_review_note")
    with Session(db_session.get_bind(), expire_on_commit=False) as other:
        stale = other.get(model, identity)
        current = db_session.get(model, identity)
        setattr(current, field, "First editor")
        db_session.commit()
        setattr(stale, field, "Stale editor")
        with pytest.raises(StaleDataError):
            other.commit()
        other.rollback()
    db_session.refresh(current)
    assert getattr(current, field) == "First editor"


def test_lock_refreshes_a_cached_invoice_before_comparing_revision(audit_client, db_session):
    _, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    old_revision = invoice.revision
    with Session(db_session.get_bind(), expire_on_commit=False) as other:
        current = other.get(Invoice, invoice.id)
        current.internal_notes = "Changed in another session"
        other.commit()
    assert invoice.revision == old_revision
    with pytest.raises(RevisionConflict):
        lock_invoice(db_session, invoice.id, old_revision)
    assert invoice.internal_notes == "Changed in another session"
