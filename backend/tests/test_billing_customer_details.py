import pytest
from fastapi.testclient import TestClient
from test_invoicing_invariant import build_order_with_box

from app.api.invoicing import render_on_behalf_invoice_html
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.main import app
from app.models import AuditLog, Invoice, InvoiceStatus, User, UserRole
from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService


@pytest.fixture
def contact_client(db_session):
    order = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).first()
    actor.role, actor.is_service_principal, actor.must_change_password = UserRole.GENERAL_ACCOUNTING, False, False
    invoice = OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(order.id, actor=actor)
    db_session.commit()
    def transaction():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise
    app.dependency_overrides[get_db] = transaction
    app.dependency_overrides[get_current_user] = lambda: actor
    try:
        yield TestClient(app), invoice, actor
    finally:
        app.dependency_overrides.clear()


def correction(details, buyer_id, **changes):
    return {"expected_revision": details["revision"], "customers": [
        {"customer_id": row["customer_id"],
         "expected": {key: row[key] for key in ("billing_email", "destination_country")},
         **{key: changes.get(key, row[key]) if row["customer_id"] == buyer_id else row[key]
            for key in ("billing_email", "destination_country")}}
        for row in details["customers"]]}


def test_missing_details_can_be_completed_and_invoice_issued(db_session, contact_client):
    client, invoice, _ = contact_client
    invoice.customer.billing_email = invoice.customer.destination_country = ""
    db_session.commit()
    base = f"/api/v3/invoicing/{invoice.id}"
    response = client.post(base + "/finalize", json={"expected_revision": invoice.revision})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MISSING_CUSTOMER_DETAILS"
    details = client.get(base + "/customer-details").json()
    payload = correction(details, invoice.customer_id, billing_email="orders@example.com", destination_country="Thailand")
    response = client.put(base + "/customer-details", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] > details["revision"]
    assert db_session.query(AuditLog).filter_by(event_type="CUSTOMER_BILLING_DETAILS_UPDATED").count() == 1
    assert client.post(base + "/finalize", json={"expected_revision": response.json()["revision"]}).status_code == 200


def test_contact_changes_do_not_rewrite_an_issued_invoice(db_session, contact_client):
    client, issued, actor = contact_client
    OnBehalfInvoicingService(db_session).approve_and_dispatch(invoice_id=issued.id, actor=actor)
    db_session.commit()
    before = render_on_behalf_invoice_html(issued.id, db=db_session).body
    draft = Invoice(invoice_number="CONTACT-CORRECTION", client_order_id=issued.client_order_id,
                    customer_id=issued.customer_id, seller_customer_id=issued.seller_customer_id,
                    final_packing_manifest_id="fixture", subtotal_eur=0, total_amount_eur=0)
    db_session.add(draft)
    db_session.commit()
    base = f"/api/v3/invoicing/{draft.id}/customer-details"
    for country in ("Thailand", "Germany"):
        details = client.get(base).json()
        response = client.put(base, json=correction(details, issued.customer_id,
            billing_email=f"orders@{country.lower()}.example", destination_country=country))
        assert response.status_code == 200, response.text
        assert render_on_behalf_invoice_html(issued.id, db=db_session).body == before
    assert issued.status == InvoiceStatus.INVOICE_SENT


@pytest.mark.parametrize("invalid", ["outsider", "duplicate", "bad_email", "blank_country", "stale_contact", "missing_revision"])
def test_contact_update_rejects_invalid_or_stale_changes(db_session, contact_client, invalid):
    client, invoice, _ = contact_client
    base = f"/api/v3/invoicing/{invoice.id}/customer-details"
    details = client.get(base).json()
    payload = correction(details, invoice.customer_id)
    expected_status = 422
    if invalid == "outsider":
        payload["customers"][0]["customer_id"] = "outsider"
    elif invalid == "duplicate":
        payload["customers"][1] = payload["customers"][0]
    elif invalid == "bad_email":
        payload["customers"][0]["billing_email"] = "invalid"
    elif invalid == "blank_country":
        payload["customers"][0]["destination_country"] = "   "
    elif invalid == "stale_contact":
        invoice.customer.billing_email = "changed@example.com"
        db_session.commit()
        expected_status = 409
    else:
        payload.pop("expected_revision")
        expected_status = 428
    response = client.put(base, json=payload)
    assert response.status_code == expected_status, response.text
    assert db_session.query(AuditLog).filter_by(event_type="CUSTOMER_BILLING_DETAILS_UPDATED").count() == 0


def test_general_staff_cannot_change_billing_contacts(contact_client):
    client, invoice, actor = contact_client
    actor.role = UserRole.GENERAL
    assert client.get(f"/api/v3/invoicing/{invoice.id}/customer-details").status_code == 403


def test_posted_invoice_cannot_change_contacts_directly(db_session, contact_client):
    client, invoice, actor = contact_client
    base = f"/api/v3/invoicing/{invoice.id}"
    payload = correction(client.get(base + "/customer-details").json(), invoice.customer_id)
    OnBehalfInvoicingService(db_session).approve_and_dispatch(invoice_id=invoice.id, actor=actor)
    db_session.commit()
    payload["expected_revision"] = invoice.revision
    assert client.get(base + "/customer-details").status_code == 409
    assert client.put(base + "/customer-details", json=payload).status_code == 409
