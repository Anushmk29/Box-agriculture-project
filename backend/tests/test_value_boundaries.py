import json
from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_system_resilience_audit import audit_client as audit_client
from test_system_resilience_audit import ready_invoice

from app.api.admin import UserCreate
from app.api.invoicing import DraftInvoiceUpdateRequest, RecordPaymentRequest
from app.api.master_data import ProductCreate
from app.api.packing import PackingItemRequest
from app.api.packing_setup import PackInput
from app.api.procurement import ProcurementItemConfirmationRequest
from app.api.sku_mappings import SellingPriceCreate
from app.models.invoice import PaymentTransaction
from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService


def payment(**changes):
    return RecordPaymentRequest(amount_paid=changes.pop("amount_paid", "1.25"),
        payment_date=changes.pop("payment_date", "2026-10-26"),
        bank_name=changes.pop("bank_name", "Bank"),
        bank_wire_reference=changes.pop("bank_wire_reference", "REF"), **changes)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e10000", "100000000", "1.001", 0, -1])
def test_invalid_payment_amounts_never_reach_the_ledger(value):
    with pytest.raises(ValidationError):
        payment(amount_paid=value)


def test_payment_values_are_decimal_trimmed_and_canonical():
    value = payment(amount_paid="99999999.99", bank_name=" Bank ", bank_wire_reference=" " + "R" * 128 + " ",
                    payment_date="20261026")
    assert value.amount_paid == Decimal("99999999.99")
    assert isinstance(value.amount_paid, Decimal)
    assert value.bank_name == "Bank" and value.bank_wire_reference == "R" * 128
    assert value.payment_date == "2026-10-26"


@pytest.mark.parametrize("changes", [{"bank_name": " "}, {"bank_name": "B" * 65},
    {"bank_wire_reference": " "}, {"bank_wire_reference": "R" * 129}, {"payment_date": "2026-02-31"}])
def test_payment_text_and_date_boundaries(changes):
    with pytest.raises(ValidationError):
        payment(**changes)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "100000000", "1.001"])
def test_selling_price_and_draft_price_share_storage_boundaries(value):
    with pytest.raises(ValidationError):
        SellingPriceCreate(main_customer_id="main", product_id="herb", pack_size="100g", selling_price_eur=value)
    with pytest.raises(ValidationError):
        DraftInvoiceUpdateRequest(items=[{"line_item_id": "line", "unit_price_eur": value}])


def test_duplicate_invoice_lines_are_rejected_before_dictionary_conversion():
    with pytest.raises(ValidationError, match="[Dd]uplicate"):
        DraftInvoiceUpdateRequest(items=[{"line_item_id": "same", "unit_price_eur": 1},
                                         {"line_item_id": "same", "unit_price_eur": 2}])


@pytest.mark.parametrize("changes", [
    {"default_unit_volume_cm3": -1}, {"default_unit_volume_cm3": "Infinity"},
    {"default_unit_volume_cm3": "0.001"}, {"default_unit_volume_cm3": "100000000"},
    {"tare_weight_allowance_g": -1}, {"tare_weight_allowance_g": "NaN"},
    {"tare_weight_allowance_g": "1000000"}, {"default_buffer_percentage": "0.001"},
    {"name_en": "x" * 257}, {"barcode": "x" * 33},
    {"default_pack_sizes": [" "]}, {"name_en": "bad\x00name"},
])
def test_product_values_fit_their_physical_and_database_contracts(changes):
    with pytest.raises(ValidationError):
        ProductCreate(t_code="TEST", **changes)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_numbers_never_break_validation_response(audit_client, db_session, literal):
    client, _ = audit_client
    body = ('{"expected_revision":1,"items":[{"line_item_id":"missing","unit_price_eur":' + literal + '}]}')
    response = client.patch("/api/v3/invoicing/missing/draft", content=body,
                           headers={"Content-Type": "application/json"})
    assert response.status_code in {400, 422}, response.text
    assert db_session.query(PaymentTransaction).count() == 0
    if response.status_code == 422:
        assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])


@pytest.mark.parametrize("password", ["PRIVATE-PASSWORD-" * 20, "PRIVATE-PASSWORD-\ud800"])
def test_password_validation_errors_do_not_reflect_passwords(audit_client, password):
    client, _ = audit_client
    body = json.dumps({"current_password": "Audit-password-2026!", "new_password": password})
    response = client.post("/api/v3/auth/change-password", content=body,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "PRIVATE-PASSWORD" not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_invoice_total_overflow_does_not_partially_edit_draft(audit_client, db_session):
    _, actor = audit_client
    service, invoice = ready_invoice(db_session, actor)
    line = invoice.items[0]
    before = (line.unit_price_eur, line.line_total_eur, invoice.total_amount_eur)
    with pytest.raises(ValueError, match="supported range"):
        service.update_draft(invoice_id=invoice.id, line_prices={line.id: Decimal("99999999.99")},
                             internal_notes=None, actor=actor)
    assert (line.unit_price_eur, line.line_total_eur, invoice.total_amount_eur) == before


def test_trimmed_bank_reference_cannot_repeat_a_legacy_spaced_reference(audit_client, db_session):
    client, actor = audit_client
    service, invoice = ready_invoice(db_session, actor)
    service.approve_and_dispatch(invoice_id=invoice.id, actor=actor)
    service.record_payment_and_issue_receipt(invoice_id=invoice.id, amount_paid=1,
        payment_date="2026-10-26", bank_name="Bank", bank_wire_reference="REF",
        payment_mode="BANK_TRANSFER", actor=actor)
    db_session.query(PaymentTransaction).one().bank_wire_reference = " REF "
    db_session.commit()
    response = client.post(f"/api/v3/invoicing/{invoice.id}/record-payment", headers={"Idempotency-Key": "legacy-bank-ref-test-0001"}, json={
        "amount_paid": 1, "payment_date": "2026-10-26", "bank_name": "Bank", "bank_wire_reference": " REF "})
    assert response.status_code == 404  # Historical data survives; new payment writes are unavailable.
    assert db_session.query(PaymentTransaction).count() == 1


@pytest.mark.parametrize("changes", [{"amount_paid": "NaN"}, {"amount_paid": "1.001"},
    {"payment_date": "2026-02-31"}, {"bank_name": " "}, {"bank_wire_reference": "R" * 129}])
def test_internal_payment_calls_validate_before_database_queries(audit_client, changes):
    _, actor = audit_client

    class NoQueries:
        def query(self, *args):
            pytest.fail("Invalid payment reached a database query")

    payload = dict(invoice_id="missing", amount_paid=1, payment_date="2026-10-26", bank_name="Bank",
                   bank_wire_reference="REF", payment_mode="BANK_TRANSFER", actor=actor)
    payload.update(changes)
    with pytest.raises(ValueError):
        OnBehalfInvoicingService(NoQueries()).record_payment_and_issue_receipt(**payload)


@pytest.mark.parametrize("count,weight", [(2_147_483_648, 1), (True, 1), (1, "100000000"),
                                         (1, "0.00001"), (1, "Infinity")])
def test_procurement_counts_and_weights_fit_storage(count, weight):
    with pytest.raises(ValidationError):
        ProcurementItemConfirmationRequest(item_id="item", purchased_pack_count=count,
                                           purchased_weight_kg=weight)


def test_small_packed_and_purchased_weights_keep_four_decimal_places():
    purchased = ProcurementItemConfirmationRequest(item_id="item", purchased_pack_count=1,
                                                   purchased_weight_kg="0.125")
    packed = PackingItemRequest(product_id="herb", pack_size="125g", quantity=1, net_weight_kg="0.125")
    assert purchased.purchased_weight_kg == packed.net_weight_kg == Decimal("0.125")


def test_normalized_profile_keys_cannot_overflow_the_storage_column():
    with pytest.raises(ValidationError, match="64 characters"):
        PackInput(pack_size="9" * 62 + "kg", reviewed=False)


def test_invalid_unicode_in_extra_field_names_does_not_break_error_json(audit_client):
    client, _ = audit_client
    response = client.put("/api/v3/packing-setup/boxes/missing", content=json.dumps({"bad\ud800": "value"}),
                          headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize("name", [" ", "bad\x00name", "bad\ud800name"])
def test_user_names_are_nonblank_and_valid_database_text(name):
    with pytest.raises(ValidationError):
        UserCreate(email="person@example.invalid", full_name=name, role="GENERAL")
