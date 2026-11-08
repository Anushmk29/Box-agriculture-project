from pathlib import Path

import openpyxl
import pytest
from test_order_intake import _setup
from test_simplified_workflow import headers

from app.core.config import settings
from app.models.excel_template import ExcelTemplate
from app.models.order import ClientOrder
from app.models.user import User
from app.modules.orders.acknowledgment import order_drafts
from app.modules.orders.intake import OrderIntake
from app.modules.orders.quantities import derive_quantity
from app.modules.orders.templates import template_issues, template_snapshots
from app.services.canonical_schema import CanonicalDraftItem
from app.services.excel_profiles import parse_profiles, references

pytest_plugins = ["test_simplified_workflow"]
SAMPLES = Path(__file__).resolve().parents[2] / "documents/new input format"


@pytest.mark.parametrize("definition", references(), ids=lambda d: d["key"])
def test_every_reference_has_a_matching_profile_and_source_cells(definition):
    path = SAMPLES.parent / definition.get("sample_directory", "new input format") / definition["sample"]
    if not path.exists():
        pytest.skip("Local client reference workbook unavailable")
    drafts = parse_profiles(str(path), references())
    assert drafts and all(d.metadata["template"].get("key") == definition["key"] for d in drafts)
    assert any(d.items for d in drafts)
    for draft in drafts:
        assert not draft.client_code.isdigit()  # Prices must never become customer identities.
        for item in draft.items:
            assert item.source["quantity_cell"] and item.source["sheet"]
            assert "raw_quantity" in item.source and item.source["template_version"] == 1
            assert item.quantity >= 0
            assert item.product_name or "Order entry has no product description" in item.quantity_issues


def test_inventory_covers_exactly_all_supplied_excel_workbooks():
    assert {p.name for p in SAMPLES.iterdir() if p.suffix in {".xlsx", ".xls"}} == {
        d["sample"] for d in references() if not d.get("sample_directory")
    }


def test_19t_reads_actual_product_code_and_future_quantities_after_blank_rows(tmp_path):
    wb = openpyxl.load_workbook(SAMPLES / "19T.xlsx")
    ws = wb.active
    ws["K5"] = 0.8
    ws.insert_rows(1, 2)
    path = tmp_path / "new delivery October.xlsx"
    wb.save(path)
    draft = parse_profiles(str(path), references())[0]
    assert draft.client_code == "19T"
    first = draft.items[0]
    assert (first.product_code, first.quantity, first.unit, first.pack_size) == (
        "11001",
        0.8,
        "kg",
        "80g",
    )
    assert derive_quantity(first).packs == 10
    assert first.source["quantity_cell"] == "K7"


def test_conflicting_customer_and_missing_column_require_review(tmp_path):
    path = SAMPLES / "19T.xlsx"
    draft = parse_profiles(str(path), references(), original_name="68H.xlsx")[0]
    assert draft.metadata["template"]["issues"]
    resolved = parse_profiles(
        str(path), references(), original_name="68H.xlsx", selected_key="19T"
    )[0]
    assert not resolved.metadata["template"]["issues"]
    wb = openpyxl.load_workbook(path)
    wb.active["K4"] = "unexpected field"
    changed = tmp_path / "19T.xlsx"
    wb.save(changed)
    assert (
        parse_profiles(str(changed), references())[0].metadata["template"]["status"]
        == "REVIEW_REQUIRED"
    )


def test_931l_uses_unlabelled_unit_column_and_explicit_thai_pack_weight():
    item = parse_profiles(str(SAMPLES / "931L (1).xlsx"), references())[0].items[0]
    assert (item.product_code, item.quantity, item.unit, item.pack_size) == (
        "11001-080",
        0.4,
        "kg",
        "80g",
    )
    assert derive_quantity(item).packs == 5


def test_unknown_marker_and_uncached_formula_remain_visible(tmp_path):
    wb = openpyxl.load_workbook(SAMPLES / "19T.xlsx")
    ws = wb.active
    ws["K5"] = "x"
    ws["K6"] = "=1+1"
    path = tmp_path / "19T.xlsx"
    wb.save(path)
    rows = parse_profiles(str(path), references())[0].items
    assert rows[0].quantity == 0 and rows[0].quantity_issues
    assert rows[1].source["formula"] == "=1+1" and rows[1].quantity_issues


def test_missing_pack_is_not_satisfied_by_a_catalog_default():
    item = CanonicalDraftItem(
        raw_text="missing pack",
        quantity=1,
        unit="kg",
        pack_size="100g",
        source={"requires_packing_review": True},
    )
    with pytest.raises(ValueError, match="staff must complete"):
        derive_quantity(item)
    item.source["reviewed_by"] = "staff"
    assert derive_quantity(item).packs == 10


def test_paya_kg_is_weight_even_when_packing_is_pieces():
    item = CanonicalDraftItem(raw_text="paya", quantity=7, unit="KG", pack_size="Pcs")
    with pytest.raises(ValueError, match="physical pack size"):
        derive_quantity(item)
    item.pack_size = "500g"
    result = derive_quantity(item)
    assert result.net_kg == 7 and result.packs == 14


def test_confirm_unit_once_persists_version_and_reuses_it(
    db_session, transactional_client, tmp_path, monkeypatch
):
    raw, herb = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    raw.main_customer.client_code = "NGA-MAIN"
    path = tmp_path / "77H.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Customer No.: 003477"])
    ws.append(["Product No.", "Product Name", None, "Order"])
    ws.append(["11001", "Lemongrass", None, 1])
    wb.save(path)
    raw.storage_url, raw.file_name = str(path), path.name
    snapshots = template_snapshots(db_session, raw.main_customer_id)
    drafts = parse_profiles(str(path), snapshots)
    raw.acknowledgment_summary = {"drafts": [d.model_dump(mode="json") for d in drafts]}
    db_session.commit()
    # The known layout is automatic; its missing unit remains a quantity issue.
    assert not template_issues(drafts)
    assert drafts[0].items[0].unit == "unknown"
    url = f"/api/v3/order-templates/{raw.id}"
    review = transactional_client.get(url, headers=headers(actor)).json()
    profile = next(f for f in review["formats"] if f["definition"]["key"] == "77H")
    payload = {
        "template_id": profile["id"],
        "expected_version": profile["version"],
        "expected_revision": review["revision"],
        "default_unit": "kg",
    }
    response = transactional_client.post(url + "/confirm", headers=headers(actor), json=payload)
    assert response.status_code == 200, response.text
    assert (
        transactional_client.post(
            url + "/confirm", headers=headers(actor), json=payload
        ).status_code
        == 409
    )
    versions = (
        db_session.query(ExcelTemplate)
        .filter_by(template_key="77H")
        .order_by(ExcelTemplate.version)
        .all()
    )
    assert len(versions) == 2 and not versions[0].confirmed and versions[1].confirmed_by == actor.id
    latest = template_snapshots(db_session, raw.main_customer_id)
    repeated = parse_profiles(str(path), latest)[0]
    assert repeated.items[0].unit == "kg" and repeated.metadata["template"]["confirmed"]
    # Missing physical packing remains a separate procurement gate.
    with pytest.raises(ValueError, match="Packing details required"):
        OrderIntake(db_session).materialize(raw_file=raw, drafts=order_drafts(raw))
    review_url = f"/api/v3/orders/{raw.id}/quantity-review"
    revision = transactional_client.get(review_url, headers=headers(actor)).json()["revision"]
    correction = transactional_client.put(
        review_url + "/0:0",
        headers=headers(actor),
        json={
            "expected_revision": revision,
            "quantity": 1,
            "unit": "kg",
            "pack_size": "100g",
            "note": "Confirmed staff packing check",
        },
    )
    assert correction.status_code == 200, correction.text
    validation = transactional_client.get(
        f"/api/v3/orders/{raw.id}/validate", headers=headers(actor)
    )
    assert validation.json()["can_split"], validation.text
    split = transactional_client.post(f"/api/v3/orders/{raw.id}/split", headers=headers(actor))
    assert split.status_code == 200, split.text
    assert db_session.query(ClientOrder).count() == 1
    assert (
        transactional_client.post(
            url + "/confirm", headers=headers(actor), json=payload
        ).status_code
        == 409
    )
    assert db_session.query(ClientOrder).count() == 1

    # Follow this imported order through the existing operational workflow.
    from app.models.master_data import BoxCatalogue, BoxMaterial, PriceList
    from app.models.procurement import MasterPurchasingList
    from app.models.user import UserRole
    from app.modules.packing.catalogue import dimension_reference
    from app.modules.packing.service import FinalPackingService, PackingPlanningService
    from app.modules.procurement.service import (
        ProcurementConfirmation,
        ProcurementConfirmationService,
    )
    from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService

    order = db_session.query(ClientOrder).one()
    batch = db_session.get(MasterPurchasingList, split.json()["batch_id"])
    purchase = batch.items[0]
    ProcurementConfirmationService(db_session).confirm_and_dispatch(
        batch_id=batch.id, confirmations=[ProcurementConfirmation(purchase.id, 10, 1)], actor=actor
    )
    entry = max(
        (r for r in dimension_reference()["rows"] if r["box_type"] == "CARDBOARD"),
        key=lambda r: r["length_cm"] * r["width_cm"] * r["height_cm"],
    )
    db_session.add(
        BoxCatalogue(
            box_type=BoxMaterial.CARDBOARD,
            size_name=entry["name_th"],
            length_cm=entry["length_cm"],
            width_cm=entry["width_cm"],
            height_cm=entry["height_cm"],
            rated_weight_kg=10,
            max_safe_net_weight_kg=10,
            volume_m3=0.1,
        )
    )
    db_session.add(
        PriceList(product_id=herb.id, pack_size="100g", unit_price_eur=2, valid_from="2026-01-01")
    )
    finance = User(
        email="finance-format@example.com",
        password_hash="x",
        full_name="Finance",
        role=UserRole.GENERAL_ACCOUNTING,
    )
    db_session.add(finance)
    db_session.flush()
    herb.foam_below_kg = 1
    PackingPlanningService(db_session).prepare_order(order_id=order.id, actor=actor)
    FinalPackingService(db_session).finalize_order(order_id=order.id, actor=actor)
    db_session.commit()
    packing = transactional_client.get(
        f"/api/v3/packing/orders/{order.id}/packing-list", headers=headers(actor)
    )
    assert packing.status_code == 200, packing.text
    assert packing.json()["boxes"][0]["sticker_label"]
    assert packing.json()["boxes"][0]["actual_net_weight_kg"] == 1
    invoice = OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(
        order.id, actor=finance
    )
    assert float(invoice.total_amount_eur) == 20 and invoice.items[0].billed_pack_count == 10


def test_new_upload_uses_profiles_in_isolated_worker(db_session, tmp_path, monkeypatch):
    from app.services.adaptive_order_orchestrator import AdaptiveOrderOrchestrator

    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    raw.main_customer.client_code = "NGA"
    db_session.commit()
    monkeypatch.setattr(settings, "EXCEL_TEMPLATE_PROFILES_ENABLED", True)
    result = AdaptiveOrderOrchestrator(db_session).ingest_file(
        str(SAMPLES / "19T.xlsx"), "2026-10-27", actor, main_customer_id=raw.main_customer_id
    )
    from app.models.order import RawOrderFile

    uploaded = db_session.get(RawOrderFile, result["raw_file_id"])
    assert order_drafts(uploaded)[0].items[0].product_code == "11001"
    assert order_drafts(uploaded)[0].metadata["template"]["key"] == "19T"


def test_staff_can_exclude_a_marker_without_inventing_packing(db_session, transactional_client):
    from test_order_intake import _draft

    from app.modules.orders.quantity_review import draft_revision

    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    draft = _draft(
        CanonicalDraftItem(
            raw_text="marker x",
            product_name="Lemongrass",
            quantity=0,
            unit="unknown",
            pack_size=None,
            quantity_issues=["Order amount x requires review"],
            source={"raw_quantity": "x", "requires_packing_review": True},
        )
    )
    raw.acknowledgment_summary = {"drafts": [draft.model_dump(mode="json")]}
    db_session.commit()
    response = transactional_client.put(
        f"/api/v3/orders/{raw.id}/quantity-review/0:0",
        headers=headers(actor),
        json={
            "expected_revision": draft_revision([draft]),
            "quantity": 0,
            "unit": "unknown",
            "note": "Staff confirmed x is not an order",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["issue_count"] == 0
    assert order_drafts(raw)[0].items[0].action.value == "CANCEL"
    assert order_drafts(raw)[0].items[0].source["raw_quantity"] == "x"


@pytest.mark.parametrize(
    "fields",
    [
        {"name": [2], "quantity": 4},
        {"name": 2, "quantity": 513},
        {"name": 2, "quantity": 4, "other": 6},
    ],
)
def test_invalid_mapping_columns_are_rejected(fields):
    from fastapi import HTTPException

    from app.api.order_templates import validate_definition

    definition = references()[0]
    definition["sheets"][0]["fields"] = fields
    with pytest.raises(HTTPException) as exc:
        validate_definition(definition)
    assert exc.value.status_code == 422
