from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_order_intake import _setup as intake_setup
from test_request_transaction import request_sessions as request_sessions
from test_system_resilience_audit import audit_client as audit_client
from test_system_resilience_audit import ready_invoice

from app.core import database
from app.core.idempotency import run_once
from app.core.security import create_access_token
from app.main import app
from app.models.invoice import PaymentTransaction, Receipt
from app.models.order import ClientOrder, ClientOrderItem
from app.models.request_result import RequestResult


def adhoc_body(product):
    return {"client_code": "CT-A", "items": [{"t_code": product.t_code, "pack_count": 2}]}


def payment_body(reference="BANK-ONE"):
    return {"amount_paid": 1, "payment_date": "2026-10-27", "bank_name": "Bank",
            "bank_wire_reference": reference}


def test_session_advertises_replay_protocol(audit_client):
    client, actor = audit_client
    session = client.get("/api/v3/auth/me").json()
    assert session["user_id"] == actor.id
    assert session["request_replay_supported"] is True


def test_adhoc_receipt_survives_new_session_and_new_key_is_new_intent(audit_client, db_session):
    client, _ = audit_client
    raw, product = intake_setup(db_session)
    path = f"/api/v3/orders/{raw.id}/add-adhoc-order"
    headers = {"Idempotency-Key": str(uuid4())}
    first = client.post(path, headers=headers, json=adhoc_body(product))
    assert first.status_code == 200, first.text
    db_session.expunge_all()
    repeated = client.post(path, headers=headers, json=adhoc_body(product))
    assert repeated.json() == first.json()
    assert db_session.query(ClientOrder).one().total_ordered_items_count == 2
    assert db_session.query(ClientOrderItem).count() == 1
    # Another explicitly submitted identical addition is allowed.
    next_result = client.post(path, headers={"Idempotency-Key": str(uuid4())}, json=adhoc_body(product))
    assert next_result.status_code == 200, next_result.text
    assert db_session.query(ClientOrder).one().total_ordered_items_count == 4
    assert db_session.query(RequestResult).count() == 2


@pytest.mark.parametrize("change_target", [False, True])
def test_same_key_cannot_change_payload_or_target(audit_client, db_session, change_target):
    client, _ = audit_client
    raw, product = intake_setup(db_session)
    path = f"/api/v3/orders/{raw.id}/add-adhoc-order"
    headers = {"Idempotency-Key": str(uuid4())}
    body = adhoc_body(product)
    assert client.post(path, headers=headers, json=body).status_code == 200
    if change_target:
        path = "/api/v3/orders/another-file/add-adhoc-order"
    else:
        body["items"][0]["pack_count"] = 3
    result = client.post(path, headers=headers, json=body)
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "REQUEST_KEY_REUSED"
    assert db_session.query(ClientOrder).one().total_ordered_items_count == 2


@pytest.mark.parametrize("key,status", [(None, 428), ("short", 422), ("x" * 129, 422), ("a b" * 8, 422)])
def test_request_keys_required_before_mutations(audit_client, db_session, key, status):
    client, _ = audit_client
    raw, product = intake_setup(db_session)
    headers = {} if key is None else {"Idempotency-Key": key}
    result = client.post(f"/api/v3/orders/{raw.id}/add-adhoc-order", headers=headers, json=adhoc_body(product))
    assert result.status_code == status
    assert db_session.query(RequestResult).count() == 0
    assert db_session.query(ClientOrder).count() == 0


def test_historical_payment_receipt_replay_remains_immutable_but_http_route_is_retired(audit_client, db_session):
    client, actor = audit_client
    service, invoice = ready_invoice(db_session, actor)
    service.approve_and_dispatch(invoice_id=invoice.id, actor=actor)
    key = str(uuid4())
    body = payment_body()
    def historical_write():
        return service.record_payment_and_issue_receipt(invoice_id=invoice.id,
            payment_mode="BANK_TRANSFER", actor=actor, **body)
    original = run_once(db_session, actor_id=actor.id, operation="invoices.record_payment.v1", key=key,
        payload=body, execute=historical_write)
    service.record_payment_and_issue_receipt(invoice_id=invoice.id, payment_mode="BANK_TRANSFER", actor=actor,
        **payment_body("BANK-TWO"))
    repeated = run_once(db_session, actor_id=actor.id, operation="invoices.record_payment.v1", key=key,
        payload=body, execute=lambda: pytest.fail("Historical write replayed"))
    assert repeated == original
    db_session.commit()
    assert client.post(f"/api/v3/invoicing/{invoice.id}/record-payment",
        headers={"Idempotency-Key":key}, json=body).status_code == 404
    assert db_session.query(PaymentTransaction).count() == 2


def test_historical_full_payment_is_preserved_when_old_ui_retries_retired_route(audit_client, db_session):
    client, actor = audit_client
    service, invoice = ready_invoice(db_session, actor)
    service.approve_and_dispatch(invoice_id=invoice.id, actor=actor)
    body = payment_body()
    body["amount_paid"] = float(invoice.total_amount_eur)
    service.record_payment_and_issue_receipt(invoice_id=invoice.id, payment_mode="BANK_TRANSFER", actor=actor, **body)
    db_session.commit()
    for _ in range(2):
        assert client.post(f"/api/v3/invoicing/{invoice.id}/record-payment", json=body).status_code == 404
    assert db_session.query(PaymentTransaction).count() == db_session.query(Receipt).count() == 1


def test_retired_payment_write_never_reserves_receipt_before_or_after_invoice_finalization(audit_client, db_session):
    client, actor = audit_client
    service, invoice = ready_invoice(db_session, actor)
    path = f"/api/v3/invoicing/{invoice.id}/record-payment"
    headers = {"Idempotency-Key":str(uuid4())}
    assert client.post(path, headers=headers, json=payment_body()).status_code == 404
    service.approve_and_dispatch(invoice_id=invoice.id, actor=actor)
    db_session.commit()
    assert client.post(path, headers=headers, json=payment_body()).status_code == 404
    assert db_session.query(RequestResult).count() == db_session.query(PaymentTransaction).count() == 0


def test_old_receipts_do_not_expire_into_repeatable_writes(db_session):
    key = str(uuid4())
    original = run_once(db_session, actor_id="actor", operation="op", key=key,
        payload={"a": 1, "b": 2}, execute=lambda: {"original": True})
    db_session.query(RequestResult).one().created_at = datetime(2000, 1, 1, tzinfo=UTC)
    db_session.commit()
    db_session.expunge_all()
    def unexpected():
        pytest.fail("An old completed request must not execute again")
    assert run_once(db_session, actor_id="actor", operation="op", key=key,
        payload={"b": 2, "a": 1}, execute=unexpected) == original


def test_actor_and_operation_have_separate_receipts_and_bad_receipts_fail_closed(db_session):
    key = str(uuid4())
    calls = []
    def apply():
        calls.append(1)
        return {"applied": len(calls)}
    for actor, operation in [("actor1", "op1"), ("actor2", "op1"), ("actor1", "op2")]:
        run_once(db_session, actor_id=actor, operation=operation, key=key, payload={"x": 1}, execute=apply)
        db_session.commit()
    assert len(calls) == 3
    row = db_session.query(RequestResult).filter_by(actor_id="actor1", operation="op1").one()
    row.response_json = None
    db_session.commit()
    with pytest.raises(HTTPException) as error:
        run_once(db_session, actor_id="actor1", operation="op1", key=key, payload={"x": 1}, execute=apply)
    assert error.value.status_code == 409
    assert len(calls) == 3


@pytest.mark.parametrize("commit_fails", [False, True])
def test_lost_response_retry_is_durable_or_reapplies_after_rollback(request_sessions, monkeypatch, commit_fails):
    with request_sessions() as seed:
        raw, product = intake_setup(seed)
        actor_id = raw.uploaded_by
        path = f"/api/v3/orders/{raw.id}/add-adhoc-order"
        body = adhoc_body(product)
    headers = {"Authorization": "Bearer " + create_access_token({"sub": actor_id}), "Idempotency-Key": str(uuid4())}
    if commit_fails:
        def failing_session():
            db = request_sessions()
            def fail():
                raise RuntimeError("Simulated uncertain commit outcome")
            monkeypatch.setattr(db, "commit", fail)
            return db
        with monkeypatch.context() as patch:
            patch.setattr(database, "SessionLocal", failing_session)
            assert TestClient(app, raise_server_exceptions=False).post(path, headers=headers, json=body).status_code == 500
        with request_sessions() as observer:
            assert observer.query(RequestResult).count() == observer.query(ClientOrder).count() == 0
    else:
        async def disconnected(scope, receive, send):
            async def fail_send(message):
                if message["type"] == "http.response.start":
                    raise ConnectionError("Client disconnected after commit")
                await send(message)
            await app(scope, receive, fail_send)
        with pytest.raises(ConnectionError, match="disconnected"):
            TestClient(disconnected).post(path, headers=headers, json=body)
    retry = TestClient(app).post(path, headers=headers, json=body)
    assert retry.status_code == 200, retry.text
    with request_sessions() as observer:
        assert observer.query(ClientOrder).one().total_ordered_items_count == 2
        assert observer.query(RequestResult).count() == observer.query(ClientOrderItem).count() == 1
