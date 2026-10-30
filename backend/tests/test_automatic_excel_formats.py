from copy import deepcopy

import openpyxl
from test_order_intake import _setup
from test_simplified_workflow import headers

from app.models.excel_template import ExcelTemplate
from app.models.user import User
from app.modules.orders.acknowledgment import order_drafts
from app.modules.orders.intake import OrderIntake
from app.modules.orders.templates import (
    recognize_saved_formats,
    template_issues,
    template_snapshots,
)
from app.services.excel_profiles import parse_profiles

pytest_plugins = ["test_simplified_workflow"]


def hungfat_file(tmp_path):
    path = tmp_path / "Order Hungfat next week.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["HUNGFAT"])
    ws.append(["code no", "Name Eng", None, None, "Packing", "Order-101", "Order -103", "Order-110", "Order-116"])
    ws.append(["11001", "Lemongrass", None, None, 80, 0.8, 0, 0, 0])
    wb.save(path)
    return path


def test_known_hungfat_layout_materializes_without_format_confirmation(db_session, tmp_path):
    raw, _ = _setup(db_session)
    path = hungfat_file(tmp_path)
    drafts = parse_profiles(path, template_snapshots(db_session, raw.main_customer_id))
    assert drafts[0].metadata["template"]["automatic"]
    assert not drafts[0].metadata["template"]["confirmed"]
    assert not template_issues(drafts)
    item = drafts[0].items[0]
    assert (item.quantity, item.unit, item.pack_size) == (0.8, "kg", "80g")
    intake = OrderIntake(db_session).materialize(raw_file=raw, drafts=drafts)
    assert len(intake.orders) == 1
    assert intake.orders[0].total_ordered_items_count == 10


def test_existing_pending_order_upgrades_automatically_and_retains_corrections(
    db_session, transactional_client, tmp_path
):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    drafts = parse_profiles(hungfat_file(tmp_path), template_snapshots(db_session, raw.main_customer_id))
    drafts[0].metadata["template"].pop("automatic")  # Old upload from before this change.
    drafts[0].items[0].quantity = 1.6
    drafts[0].items[0].source["reviewed_by"] = actor.id
    raw.acknowledgment_summary = {"drafts": [d.model_dump(mode="json") for d in drafts]}
    db_session.commit()
    before = deepcopy(order_drafts(raw)[0].items[0].model_dump())
    result = transactional_client.get(f"/api/v3/orders/{raw.id}/validate", headers=headers(actor))
    assert result.status_code == 200, result.text
    assert not result.json()["quantity_review"]["template_issues"]
    assert result.json()["can_split"]
    db_session.refresh(raw)
    saved = order_drafts(raw)[0]
    assert saved.metadata["template"]["automatic"]
    assert saved.items[0].model_dump() == before


def test_changed_mapping_and_foreign_account_do_not_get_automatic_approval(db_session, tmp_path):
    raw, _ = _setup(db_session)
    profiles = template_snapshots(db_session, raw.main_customer_id)
    profile = next(p for p in profiles if p["definition"]["key"] == "HUNGFAT-MATRIX")
    row = db_session.get(ExcelTemplate, profile["id"])
    definition = deepcopy(row.definition)
    definition["default_unit"] = "pack"
    row.definition = definition
    profiles = template_snapshots(db_session, raw.main_customer_id)
    drafts = parse_profiles(hungfat_file(tmp_path), profiles)
    assert not drafts[0].metadata["template"]["automatic"]
    recognize_saved_formats(db_session, raw, drafts)
    assert template_issues(drafts)
    definition["default_unit"] = "kg"
    row.definition = definition
    row.account_key = "OTHER-ACCOUNT"
    db_session.flush()
    recognize_saved_formats(db_session, raw, drafts)
    assert template_issues(drafts)


def test_optional_format_editor_rejects_a_template_from_another_account(
    db_session, transactional_client, tmp_path
):
    from app.modules.orders.quantity_review import draft_revision

    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    drafts = parse_profiles(hungfat_file(tmp_path), template_snapshots(db_session, raw.main_customer_id))
    raw.acknowledgment_summary = {"drafts": [d.model_dump(mode="json") for d in drafts]}
    other = next(p for p in template_snapshots(db_session, "OTHER-ACCOUNT")
                 if p["definition"]["key"] == "HUNGFAT-MATRIX")
    db_session.commit()
    response = transactional_client.post(f"/api/v3/order-templates/{raw.id}/confirm", headers=headers(actor), json={
        "template_id": other["id"], "expected_version": 1,
        "expected_revision": draft_revision(drafts), "default_unit": "kg",
    })
    assert response.status_code == 409
