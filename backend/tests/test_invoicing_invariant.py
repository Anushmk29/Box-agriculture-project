import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.audit import AgentHandoff, AuditLog
from app.models.invoice import Invoice, InvoiceStatus, PaymentTransaction, Receipt
from app.models.master_data import (
    BoxCatalogue,
    BoxMaterial,
    ControlStatus,
    Customer,
    PriceList,
    ProduceType,
    Product,
    TemperatureZone,
    WeightClass,
)
from app.models.order import ClientOrder, OrderStatus, RawOrderFile
from app.models.packing import Box, BoxItem
from app.models.user import User, UserRole
from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService


def build_order_with_box(db_session, *, locked: bool, suffix: str = "", seller=None):
    tag = f"-{suffix}" if suffix else ""
    user = User(
        email=f"agent1{tag}@example.com",
        password_hash="unused",
        full_name="Agent One",
        role=UserRole.AI_AGENT_1,
        is_service_principal=True,
    )
    seller = seller or Customer(
        client_code="TONIO", company_name="Tonio", billing_email="seller@example.com",
        route_group="TETE", destination_country="France", tax_vat_id="FR-SELLER-001",
        is_main_customer=True,
    )
    buyer = Customer(
        client_code=f"BUYER-{suffix or '1'}",
        company_name=f"Buyer {suffix}".strip(),
        billing_email=f"buyer{tag}@example.com",
        route_group="TETE",
        destination_country="France",
        tax_vat_id=f"FR-BUYER-{suffix or '001'}",
        parent_customer=seller,
    )
    product = Product(
        t_code=f"T001{tag}",
        name_vi="Rau",
        name_en="Herb",
        name_th="Pak",
        box_material=BoxMaterial.FOAM,
        temperature=TemperatureZone.COLD,
        control_status=ControlStatus.COMBINABLE,
        produce_type=ProduceType.HERB,
        weight_class=WeightClass.LIGHT,
        default_pack_sizes=["1kg"],
    )
    catalogue = BoxCatalogue(
        box_type=BoxMaterial.FOAM,
        size_name="Small",
        length_cm=30,
        width_cm=20,
        height_cm=24,
        rated_weight_kg=5,
        volume_m3=0.0144,
        max_safe_net_weight_kg=5,
    )
    db_session.add_all([user, buyer, product, catalogue])
    if seller.id is None:
        db_session.add(seller)
    db_session.flush()
    db_session.add(
        PriceList(
            product_id=product.id,
            pack_size="1kg",
            unit_price_eur=2.5,
            valid_from="2026-01-01",
        )
    )
    raw_file = RawOrderFile(
        file_name=f"order{tag}.xlsx",
        storage_url=f"order{tag}.xlsx",
        shipment_target_date="2026-09-10",
        route_line="TETE",
        uploaded_by=user.id,
        main_customer_id=seller.id,
    )
    db_session.add(raw_file)
    db_session.flush()
    order = ClientOrder(
        raw_order_file_id=raw_file.id,
        customer_id=buyer.id,
        main_customer_id=seller.id,
        order_ref_id=f"ORD-20260910-BUYER{tag or '1'}",
        status=OrderStatus.HANDOFF_02_RETURNED if locked else OrderStatus.FLOOR_PACKING,
    )
    db_session.add(order)
    db_session.flush()
    box = Box(
        client_order_id=order.id,
        box_catalogue_id=catalogue.id,
        box_number=1,
        shipment_date="2026-09-10",
        route_code="TETE",
        qr_code_uid=f"BOX-1{tag}",
        actual_gross_weight_kg=2.2,
        actual_net_weight_kg=2.0,
        is_locked=locked,
    )
    db_session.add(box)
    db_session.flush()
    db_session.add(
        BoxItem(
            box_id=box.id,
            product_id=product.id,
            pack_size="1kg",
            final_packed_count=2,
            final_net_weight_kg=2.0,
        )
    )
    if locked:
        payload = {
            "final_packing_manifest_id": order.id,
            "boxes": [
                {
                    "box_uuid": box.id,
                    "box_number": box.box_number,
                    "actual_gross_weight_kg": float(box.actual_gross_weight_kg),
                    "actual_net_weight_kg": float(box.actual_net_weight_kg),
                    "items": [
                        {
                            "product_id": product.id,
                            "pack_size": "1kg",
                            "final_pack_count": 2,
                            "final_net_weight_kg": 2.0,
                        }
                    ],
                }
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        db_session.add(
            AgentHandoff(
                handoff_type="HANDOFF_02_FINAL_PACKING",
                sender_agent="AI_AGENT_2",
                recipient_agent="AI_AGENT_1",
                client_order_id=order.id,
                payload_json=payload,
                payload_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            )
        )
    db_session.commit()
    return order


def test_invoice_rejects_any_unlocked_packing_data(db_session):
    order = build_order_with_box(db_session, locked=False)
    actor = db_session.query(User).filter_by(email="agent1@example.com").one()

    with pytest.raises(ValueError, match="locked final packing"):
        OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(
            order.id,
            actor=actor,
        )

    assert db_session.query(Invoice).count() == 0


def test_invoice_uses_locked_final_packing_data(db_session):
    order = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).filter_by(email="agent1@example.com").one()

    invoice = OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(
        order.id,
        actor=actor,
    )

    assert len(invoice.items) == 1
    assert invoice.items[0].billed_pack_count == 2
    assert float(invoice.items[0].billed_weight_kg) == 2.0
    assert invoice.final_packing_manifest_id == db_session.query(AgentHandoff).one().payload_sha256
    assert order.status == OrderStatus.INVOICES_DRAFTED


@pytest.mark.parametrize("code_length", [10, 46, 64])
def test_individual_invoice_number_fits_schema_and_reuses_draft(db_session, code_length):
    order = build_order_with_box(db_session, locked=True)
    order.customer.client_code = "C" * code_length
    actor = db_session.query(User).filter_by(email="agent1@example.com").one()
    service = OnBehalfInvoicingService(db_session)

    invoice = service.generate_sub_customer_invoice(order.id, actor=actor)

    assert len(invoice.invoice_number) <= Invoice.invoice_number.type.length
    assert invoice.invoice_number.startswith("INV-20260910-")
    assert invoice.invoice_number.endswith(f"-{order.id[:8]}")
    if code_length == 10:
        assert invoice.invoice_number == f"INV-20260910-{'C' * 10}-{order.id[:8]}"
    assert order.customer.client_code == "C" * code_length
    assert service.generate_sub_customer_invoice(order.id, actor=actor).id == invoice.id
    assert db_session.query(Invoice).count() == 1


def test_missing_prices_identify_products_before_creating_invoice(db_session):
    order = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).filter_by(email="agent1@example.com").one()
    db_session.query(PriceList).delete()
    db_session.commit()

    with pytest.raises(ValueError, match=r"T001 Herb \(1kg\).*2026-09-10"):
        OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(order.id, actor=actor)

    assert db_session.query(Invoice).count() == 0
    assert order.status == OrderStatus.HANDOFF_02_RETURNED


@pytest.mark.parametrize("code_length", [5, 64])
def test_master_invoice_number_fits_schema(db_session, code_length):
    first = build_order_with_box(db_session, locked=True, suffix="A")
    second = build_order_with_box(db_session, locked=True, suffix="B", seller=first.main_customer)
    first.main_customer.client_code = "M" * code_length
    actor = db_session.query(User).filter_by(email="agent1-A@example.com").one()

    invoice = OnBehalfInvoicingService(db_session).generate_master_invoice(
        [first.id, second.id], actor=actor
    )

    assert len(invoice.invoice_number) <= Invoice.invoice_number.type.length
    assert invoice.invoice_number.startswith("MINV-20260910-")
    assert invoice.invoice_number.endswith(f"-{invoice.final_packing_manifest_id[:8]}")
    assert first.main_customer.client_code == "M" * code_length


def test_master_invoice_combines_and_tracks_every_source_order(db_session):
    first = build_order_with_box(db_session, locked=True, suffix="A")
    second = build_order_with_box(
        db_session, locked=True, suffix="B", seller=first.main_customer
    )
    agent = db_session.query(User).filter_by(email="agent1-A@example.com").one()
    accounting = User(
        email="master-accounting@example.com",
        password_hash="unused",
        full_name="Master Invoice Approver",
        role=UserRole.GENERAL_ACCOUNTING,
    )
    db_session.add(accounting)
    db_session.commit()

    service = OnBehalfInvoicingService(db_session)
    invoice = service.generate_master_invoice([first.id, second.id], actor=agent)

    assert invoice.invoice_type == "MASTER_SUMMARY"
    assert db_session.query(Invoice).count() == 1
    assert {item.source_order_id for item in invoice.items} == {first.id, second.id}
    assert float(invoice.total_amount_eur) == 10.0
    assert first.status == second.status == OrderStatus.INVOICES_DRAFTED

    service.approve_and_dispatch(invoice_id=invoice.id, actor=accounting)
    assert first.status == second.status == OrderStatus.INVOICES_SENT


def test_draft_invoice_prices_are_editable_until_dispatch(db_session):
    order = build_order_with_box(db_session, locked=True)
    agent = db_session.query(User).filter_by(email="agent1@example.com").one()
    accounting = User(
        email="edit@example.com",
        password_hash="unused",
        full_name="Draft Editor",
        role=UserRole.GENERAL_ACCOUNTING,
    )
    db_session.add(accounting)
    db_session.commit()
    service = OnBehalfInvoicingService(db_session)
    invoice = service.generate_sub_customer_invoice(order.id, actor=agent)

    updated = service.update_draft(
        invoice_id=invoice.id,
        line_prices={invoice.items[0].id: 3.0},
        internal_notes="Approved seasonal rate",
        actor=accounting,
    )

    assert float(updated.total_amount_eur) == 6.0
    assert updated.internal_notes == "Approved seasonal rate"
    service.approve_and_dispatch(invoice_id=invoice.id, actor=accounting)
    with pytest.raises(ValueError, match="Only a draft"):
        service.update_draft(
            invoice_id=invoice.id,
            line_prices={invoice.items[0].id: 4.0},
            internal_notes=None,
            actor=accounting,
        )


def test_accounting_lifecycle_is_gated_audited_and_issues_receipt(db_session):
    order = build_order_with_box(db_session, locked=True)
    agent = db_session.query(User).filter_by(email="agent1@example.com").one()
    accounting = User(
        email="accounting@example.com",
        password_hash="unused",
        full_name="Accounting Officer",
        role=UserRole.GENERAL_ACCOUNTING,
    )
    db_session.add(accounting)
    db_session.commit()
    service = OnBehalfInvoicingService(db_session)

    invoice = service.generate_sub_customer_invoice(order.id, actor=agent)
    service.approve_and_dispatch(invoice_id=invoice.id, actor=accounting)
    partial = service.record_payment_and_issue_receipt(
        invoice_id=invoice.id,
        amount_paid=2.0,
        payment_date="2026-09-11",
        bank_name="Bangkok Bank",
        bank_wire_reference="WIRE-PARTIAL-1",
        payment_mode="BANK_TRANSFER",
        actor=accounting,
    )
    final = service.record_payment_and_issue_receipt(
        invoice_id=invoice.id,
        amount_paid=3.0,
        payment_date="2026-09-12",
        bank_name="Bangkok Bank",
        bank_wire_reference="WIRE-FINAL-1",
        payment_mode="BANK_TRANSFER",
        actor=accounting,
    )

    assert partial["status"] == InvoiceStatus.PARTIALLY_PAID.value
    assert partial["receipt_issued"] is False
    assert final["status"] == InvoiceStatus.CONFIRMED_PAID.value
    assert final["receipt_issued"] is True
    assert order.status == OrderStatus.RECEIPTS_ISSUED
    closed = service.close_paid_order(invoice_id=invoice.id, actor=accounting)
    assert closed.status == OrderStatus.ORDER_CLOSED
    assert db_session.query(PaymentTransaction).count() == 2
    receipt = db_session.query(Receipt).one()
    assert len(receipt.payload_sha256) == 64
    receipt_event = db_session.query(AuditLog).filter_by(event_type="EVT_RECEIPT_ISSUED").one()
    assert receipt_event.actor_role == UserRole.AI_AGENT_1.value
    assert {
        "EVT_INVOICE_DRAFTED",
        "EVT_INVOICE_DISPATCH",
        "EVT_PAYMENT_CONFIRMED",
        "EVT_RECEIPT_ISSUED",
    }.issubset({event.event_type for event in db_session.query(AuditLog).all()})


def test_payment_requires_dispatched_invoice_and_accounting_role(db_session):
    order = build_order_with_box(db_session, locked=True)
    agent = db_session.query(User).filter_by(email="agent1@example.com").one()
    accounting = User(
        email="accounting@example.com",
        password_hash="unused",
        full_name="Accounting Officer",
        role=UserRole.GENERAL_ACCOUNTING,
    )
    floor_user = User(
        email="floor@example.com",
        password_hash="unused",
        full_name="Floor User",
        role=UserRole.GENERAL,
    )
    db_session.add_all([accounting, floor_user])
    db_session.commit()
    service = OnBehalfInvoicingService(db_session)
    invoice = service.generate_sub_customer_invoice(order.id, actor=agent)

    with pytest.raises(ValueError, match="dispatched"):
        service.record_payment_and_issue_receipt(
            invoice_id=invoice.id,
            amount_paid=1.0,
            payment_date="2026-09-11",
            bank_name="Bangkok Bank",
            bank_wire_reference="WIRE-1",
            payment_mode="BANK_TRANSFER",
            actor=accounting,
        )

    with pytest.raises(PermissionError, match="Accounting"):
        service.approve_and_dispatch(invoice_id=invoice.id, actor=floor_user)


def test_invoice_rejects_tampered_handoff_02(db_session):
    order = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).filter_by(email="agent1@example.com").one()
    handoff = db_session.query(AgentHandoff).one()
    handoff.payload_json = {**handoff.payload_json, "boxes": ["tampered"]}
    db_session.commit()

    with pytest.raises(ValueError, match="integrity verification"):
        OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(
            order.id,
            actor=actor,
        )


def test_invoice_rejects_live_packing_that_differs_from_signed_handoff(db_session):
    order = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).filter_by(email="agent1@example.com").one()
    box = db_session.query(Box).one()
    box.actual_net_weight_kg = 1.5
    db_session.commit()

    with pytest.raises(ValueError, match="do not match signed Handoff 02"):
        OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(
            order.id,
            actor=actor,
        )


def test_payment_rejects_amount_above_outstanding_balance(db_session):
    order = build_order_with_box(db_session, locked=True)
    agent = db_session.query(User).filter_by(email="agent1@example.com").one()
    accounting = User(
        email="accounting@example.com",
        password_hash="unused",
        full_name="Accounting Officer",
        role=UserRole.GENERAL_ACCOUNTING,
    )
    db_session.add(accounting)
    db_session.commit()
    service = OnBehalfInvoicingService(db_session)
    invoice = service.generate_sub_customer_invoice(order.id, actor=agent)
    service.approve_and_dispatch(invoice_id=invoice.id, actor=accounting)

    with pytest.raises(ValueError, match="outstanding balance"):
        service.record_payment_and_issue_receipt(
            invoice_id=invoice.id,
            amount_paid=5.01,
            payment_date="2026-09-11",
            bank_name="Bangkok Bank",
            bank_wire_reference="WIRE-OVERPAY-1",
            payment_mode="BANK_TRANSFER",
            actor=accounting,
        )

    assert db_session.query(PaymentTransaction).count() == 0


def test_printable_invoice_escapes_customer_and_product_content(db_session):
    order = build_order_with_box(db_session, locked=True)
    agent = db_session.query(User).filter_by(email="agent1@example.com").one()
    buyer = db_session.query(Customer).filter_by(client_code="BUYER-1").one()
    product = db_session.query(Product).filter_by(t_code="T001").one()
    buyer.company_name = "Buyer <script>alert('buyer')</script>"
    product.name_vi = "Rau <script>alert('produce')</script>"
    db_session.commit()
    invoice = OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(
        order.id,
        actor=agent,
    )

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    token = create_access_token(
        {
            "sub": agent.id,
            "email": agent.email,
            "role": agent.role.value,
            "is_service_principal": agent.is_service_principal,
        }
    )
    try:
        response = TestClient(app).get(
            f"/api/v3/invoicing/{invoice.id}/html",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "EU839201948" not in response.text
    assert "Bangkok Bank Public Company Ltd." not in response.text
    assert "BKKBTHBK" not in response.text
    assert "Settlement instructions will be supplied separately" in response.text
