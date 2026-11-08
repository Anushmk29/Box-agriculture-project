from copy import deepcopy
from decimal import Decimal

import pymupdf
import pytest
from test_procurement_handoff import setup_procurement_batch
from test_simplified_workflow import headers

from app.models.audit import AgentHandoff, AuditLog
from app.models.master_data import Customer
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.models.procurement import MasterPurchasingList, PurchasingItem, PurchasingOrderSource
from app.models.user import UserRole
from app.modules.procurement.market_list import market_list, market_pdf

pytest_plugins = ["test_simplified_workflow"]


def purchases(db):
    buyer, _, order, batch, item = setup_procurement_batch(db)
    order.customer.client_code = "TETE"
    order.customer.company_name = "TETE"
    order.raw_file.main_customer_id = order.customer_id
    order.raw_file.file_name = "TETE order.xlsx"
    item.buffer_weight_kg = 1
    second = Customer(client_code="P-PUI", company_name="P-PUI", billing_email="pui@example.com",
                      route_group="P-PUI", destination_country="Thailand")
    db.add(second)
    db.flush()
    raw = RawOrderFile(file_name="P-PUI order.xlsx", storage_url="pui.xlsx", shipment_target_date="2026-09-11",
                       route_line="P-PUI", uploaded_by=buyer.id, main_customer_id=second.id,
                       status=OrderStatus.PURCHASING_CONSOLIDATED)
    db.add(raw)
    db.flush()
    other = ClientOrder(raw_order_file_id=raw.id, customer_id=second.id, order_ref_id="ORD-PUI",
                        status=OrderStatus.PURCHASING_CONSOLIDATED, total_ordered_items_count=6,
                        total_estimated_net_weight_kg=6)
    db.add(other)
    db.flush()
    db.add(ClientOrderItem(client_order_id=other.id, product_id=item.product_id, pack_size="1kg",
                          raw_produce_text="Herb", ordered_pack_count=6, estimated_net_weight_kg=6))
    other_batch = MasterPurchasingList(raw_order_file_id=raw.id, batch_reference="PUR-PUI",
        shipment_date="2026-09-11", route_code="P-PUI", total_skus_count=1,
        status=OrderStatus.PURCHASING_CONSOLIDATED)
    db.add(other_batch)
    db.flush()
    db.add_all([PurchasingOrderSource(purchasing_list_id=other_batch.id, raw_order_file_id=raw.id),
        PurchasingItem(purchasing_list_id=other_batch.id, product_id=item.product_id, pack_size="1kg",
            ordered_net_weight_kg=6, buffer_weight_kg=Decimal("0.6"), buffer_percentage=10,
            total_required_pack_count=7, total_required_weight_kg=Decimal("6.6"))])
    db.commit()
    db.expire_all()
    return buyer, order, other, batch, other_batch, item


def test_customer_sections_keep_same_product_separate_without_mutating_purchases(db_session):
    _, order, other, batch, other_batch, _ = purchases(db_session)
    report = market_list(db_session, [batch.id, other_batch.id, batch.id])
    sections = {section["customer_code"]: section for section in report["sections"]}
    assert set(sections) == {"TETE", "P-PUI"}
    assert sections["TETE"]["lines"][0]["target_kg"] == 11
    assert sections["P-PUI"]["lines"][0]["target_kg"] == 6.6
    assert report["total_target_kg"] == 17.6
    assert len(report["batch_ids"]) == 2
    assert db_session.query(MasterPurchasingList).count() == 2
    assert db_session.query(PurchasingOrderSource).count() == 2
    assert order.status == other.status == batch.status == other_batch.status == OrderStatus.PURCHASING_CONSOLIDATED
    assert db_session.query(AgentHandoff).count() == db_session.query(AuditLog).count() == 0
    assert not db_session.dirty and not db_session.new and not db_session.deleted


def test_combined_batch_retains_customer_sections_and_counts_shared_buffer_once(db_session):
    _, _, other, batch, other_batch, item = purchases(db_session)
    # Reproduce an existing combined batch without imposing its same-date creation rule on reports.
    db_session.add(PurchasingOrderSource(purchasing_list_id=batch.id, raw_order_file_id=other.raw_order_file_id))
    db_session.delete(other_batch)
    item.ordered_net_weight_kg = 16
    item.buffer_weight_kg = Decimal("1.6")
    item.total_required_weight_kg = Decimal("17.6")
    item.total_required_pack_count = 18
    item.confirmed_purchased_pack_count = 18
    item.confirmed_purchased_weight_kg = 18
    db_session.commit()
    db_session.expire_all()
    report = market_list(db_session, [batch.id])
    sections = {section["customer_code"]: section for section in report["sections"]}
    assert sections["TETE"]["lines"][0]["target_kg"] == 10
    assert sections["P-PUI"]["lines"][0]["target_kg"] == 6
    shared = sections["Shared"]["lines"][0]
    assert shared["target_kg"] == 1.6 and shared["target_packs"] == 2
    assert shared["actual_kg"] == 18
    assert sum(line["actual_kg"] or 0 for section in report["sections"] for line in section["lines"]) == 18
    assert sum(section["total_target_kg"] for section in report["sections"]) == 17.6


def test_overlapping_purchases_and_mismatched_sources_are_rejected(db_session):
    _, order, _, batch, other_batch, _ = purchases(db_session)
    db_session.add(PurchasingOrderSource(purchasing_list_id=other_batch.id, client_order_id=order.id))
    db_session.commit()
    with pytest.raises(ValueError, match="overlapping"):
        market_list(db_session, [batch.id, other_batch.id])


def test_missing_cancelled_and_changed_purchase_sources_are_rejected(db_session):
    _, order, _, batch, _, _ = purchases(db_session)
    with pytest.raises(LookupError):
        market_list(db_session, [batch.id, "missing"])
    batch.status = OrderStatus.CANCELLED
    with pytest.raises(ValueError, match="cancelled"):
        market_list(db_session, [batch.id])
    batch.status = OrderStatus.PURCHASING_CONSOLIDATED
    order.items[0].estimated_net_weight_kg = 20
    db_session.flush()
    with pytest.raises(ValueError, match="weights"):
        market_list(db_session, [batch.id])


def test_pack_sizes_and_unknown_pack_counts_stay_distinct(db_session):
    _, order, _, batch, _, item = purchases(db_session)
    db_session.add(ClientOrderItem(client_order_id=order.id, product_id=item.product_id, pack_size="500g",
        raw_produce_text="Herb by weight", ordered_pack_count=0, estimated_net_weight_kg=Decimal("2.5")))
    db_session.add(PurchasingItem(purchasing_list_id=batch.id, product_id=item.product_id, pack_size="500g",
        ordered_net_weight_kg=Decimal("2.5"), buffer_weight_kg=0,
        total_required_weight_kg=Decimal("2.5"), total_required_pack_count=0))
    db_session.commit()
    report = market_list(db_session, [batch.id])
    lines = {line["pack_size"]: line for line in report["sections"][0]["lines"]}
    assert set(lines) == {"500g", "1kg"}
    assert lines["500g"]["pack_count_pending"] is True
    assert lines["500g"]["target_kg"] == 2.5


def test_preview_and_download_use_purchase_access_and_validate_selection(db_session, transactional_client):
    buyer, _, _, batch, other_batch, _ = purchases(db_session)
    root = "/api/v3/procurement/master-purchase"
    body = {"batch_ids": [batch.id, other_batch.id]}
    assert transactional_client.post(root + "/preview", json=body).status_code == 401
    assert transactional_client.post(root + "/pdf", json=body).status_code == 401
    for selected in [[], [batch.id] * 51]:
        assert transactional_client.post(root + "/preview", json={"batch_ids": selected}, headers=headers(buyer)).status_code == 422
    assert transactional_client.post(root + "/pdf", json={"batch_ids": [batch.id, "missing"]}, headers=headers(buyer)).status_code == 404
    response = transactional_client.post(root + "/preview", json=body, headers=headers(buyer))
    assert response.status_code == 200, response.text
    assert {section["customer_code"] for section in response.json()["sections"]} == {"TETE", "P-PUI"}
    buyer.role = UserRole.AI_AGENT_2
    buyer.is_service_principal = True
    db_session.commit()
    assert transactional_client.post(root + "/pdf", json=body, headers=headers(buyer)).status_code == 403


def test_actual_pdf_contains_all_customers_and_saved_actuals(db_session, transactional_client):
    buyer, _, _, batch, other_batch, item = purchases(db_session)
    item.product.name_th = "ผักชี"
    item.product.name_vi = "Rau mùi"
    item.confirmed_purchased_weight_kg = 9
    item.confirmed_purchased_pack_count = 9
    item.shortage_reason = "Market shortage <not HTML>"
    db_session.commit()
    response = transactional_client.post("/api/v3/procurement/master-purchase/pdf",
        json={"batch_ids": [batch.id, other_batch.id]}, headers=headers(buyer))
    assert response.status_code == 200, response.text[:500]
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"%PDF-")
    with pymupdf.open(stream=response.content, filetype="pdf") as document:
        text = " ".join(" ".join(page.get_text() for page in document).split())
        assert len(document) == 2
        for expected in ["TETE", "P-PUI", "17.6", "Rau mùi", "ผักชี", "Market shortage <not HTML>", "9 packs", "TETE order.xlsx", "P-PUI order.xlsx"]:
            assert expected in text
    single = transactional_client.get(f"/api/v3/procurement/batches/{batch.id}/pdf", headers=headers(buyer))
    assert single.status_code == 200 and single.content.startswith(b"%PDF-")


def test_large_pdf_repeats_customer_and_column_headings_without_losing_lines(db_session):
    _, _, _, batch, _, _ = purchases(db_session)
    report = market_list(db_session, [batch.id])
    sample = report["sections"][0]["lines"][0]
    report["sections"][0]["lines"] = [{**deepcopy(sample), "product_code": f"ROW-{index:03}"} for index in range(85)]
    with pymupdf.open(stream=market_pdf(report), filetype="pdf") as document:
        assert len(document) > 2
        for page in document:
            text = page.get_text()
            assert "TETE" in text and "Market notes" in text and "Master Purchase List" in text
        text = " ".join(page.get_text() for page in document)
        for index in range(85):
            assert text.count(f"ROW-{index:03}") == 1


def test_long_names_and_filenames_fit_the_printable_page(db_session):
    _, _, _, batch, _, _ = purchases(db_session)
    report = market_list(db_session, [batch.id])
    line = report["sections"][0]["lines"][0]
    line["product_name"] = "LongProductName" * 17
    line["source_files"] = ["Order_Name_" * 23 + ".xlsx"]
    line["notes"] = "Market handling instructions " * 12
    with pymupdf.open(stream=market_pdf(report), filetype="pdf") as document:
        for page in document:
            assert all(block[2] <= page.rect.width - 25 for block in page.get_text("blocks"))
