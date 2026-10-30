from test_order_intake import _draft, _item, _setup
from test_simplified_workflow import headers

from app.models.audit import AuditLog
from app.models.order import ClientOrderItem
from app.models.user import User
from app.modules.orders.acknowledgment import order_drafts
from app.modules.orders.intake import OrderIntake
from app.modules.orders.validation import OrderValidationService

pytest_plugins = ["test_simplified_workflow"]


def save_draft(db, raw, draft):
    raw.acknowledgment_summary = {"drafts": [draft.model_dump(mode="json")]}
    db.commit()


def test_quantity_review_corrects_fractional_input_and_retains_source(
    db_session, transactional_client
):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    item = _item(1.05, unit="kg", pack="100g")
    item.source = {"raw_pack_size": 100, "raw_quantity": 1.05, "raw_unit": "KG", "row": 13}
    save_draft(db_session, raw, _draft(item))
    result = OrderValidationService(db_session).validate(raw_file_id=raw.id, actor=actor)
    assert not result.can_split and len(result.quantity_issues) == 1
    url = f"/api/v3/orders/{raw.id}/quantity-review"
    review = transactional_client.get(url, headers=headers(actor)).json()
    response = transactional_client.put(
        url + "/0:0",
        headers=headers(actor),
        json={
            "expected_revision": review["revision"],
            "quantity": 1,
            "unit": "kg",
            "pack_size": "100g",
            "note": "Confirmed one kilogram",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["issue_count"] == 0
    assert response.json()["items"][0]["computed"] == {"pack_count": 10, "net_weight_kg": 1}
    assert response.json()["items"][0]["source"]["original"]["quantity"] == 1.05
    assert (
        transactional_client.put(
            url + "/0:0",
            headers=headers(actor),
            json={
                "expected_revision": review["revision"],
                "quantity": 2,
                "unit": "kg",
                "pack_size": "100g",
                "note": "Stale edit",
            },
        ).status_code
        == 409
    )
    from app.modules.orders.acknowledgment import order_drafts

    OrderIntake(db_session).materialize(raw_file=raw, drafts=order_drafts(raw))
    line = db_session.query(ClientOrderItem).one()
    assert line.ordered_pack_count == 10 and float(line.estimated_net_weight_kg) == 1
    assert line.quantity_source["lines"][0]["original"]["quantity"] == 1.05
    assert db_session.query(AuditLog).filter_by(event_type="ORDER_QUANTITY_CORRECTED").count() == 1


def test_unknown_matrix_unit_needs_explicit_choice_and_cannot_change_printed_units(
    db_session, transactional_client
):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    save_draft(db_session, raw, _draft(_item(1, unit="unknown"), _item(1, unit="kg", pack="200g")))
    url = f"/api/v3/orders/{raw.id}/quantity-review"
    review = transactional_client.get(url, headers=headers(actor)).json()
    assert review["unknown_unit_count"] == 1 and review["issue_count"] == 1
    response = transactional_client.post(
        f"/api/v3/orders/{raw.id}/quantity-unit",
        headers=headers(actor),
        json={"expected_revision": review["revision"], "unit": "pack"},
    )
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert rows[0]["computed"]["pack_count"] == 1
    assert rows[1]["unit"] == "kg" and rows[1]["computed"]["pack_count"] == 5


def test_issue_filter_pages_problems_and_validation_reuses_the_same_results(db_session, transactional_client):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    save_draft(db_session, raw, _draft(*[_item(1) for _ in range(55)], _item(1.05), _item(2.05)))
    url = f"/api/v3/orders/{raw.id}"
    review = transactional_client.get(url + "/quantity-review?issues_only=true&limit=1", headers=headers(actor)).json()
    assert review["total"] == 57 and review["filtered_total"] == 2
    assert review["items"][0]["row_id"] == "0:55" and review["has_more"]
    next_page = transactional_client.get(url + "/quantity-review?issues_only=true&limit=1&offset=1", headers=headers(actor)).json()
    assert next_page["items"][0]["row_id"] == "0:56" and not next_page["has_more"]
    validation = transactional_client.get(url + "/validate", headers=headers(actor)).json()
    assert validation["quantity_issue_count"] == validation["quantity_review"]["issue_count"] == 2
    assert [row["row_id"] for row in validation["quantity_review"]["items"]] == ["0:55", "0:56"]


def test_bulk_preview_saves_once_and_keeps_each_shop_amount_and_source(db_session, transactional_client):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    drafts = [_draft(_item(1, unit="kg", pack="100g+"), client="SHOP-A"), _draft(_item(2, unit="kg", pack="100g+"), client="SHOP-B")]
    raw.acknowledgment_summary = {"drafts": [draft.model_dump(mode="json") for draft in drafts]}
    db_session.commit()
    url = f"/api/v3/orders/{raw.id}/quantity-review"
    review = transactional_client.get(url, headers=headers(actor)).json()
    payload = {"expected_revision": review["revision"], "row_ids": ["0:0", "1:0"],
               "pack_size": "100g", "note": "Customer confirmed the physical bag size", "preview_only": True}
    preview = transactional_client.post(url + "/bulk", headers=headers(actor), json=payload)
    assert preview.status_code == 200, preview.text
    assert [row["computed"]["pack_count"] for row in preview.json()["items"]] == [10, 20]
    assert [draft.items[0].pack_size for draft in order_drafts(raw)] == ["100g+", "100g+"]
    assert db_session.query(AuditLog).filter_by(event_type="ORDER_QUANTITIES_BULK_CORRECTED").count() == 0
    response = transactional_client.post(url + "/bulk", headers=headers(actor), json={**payload, "preview_only": False})
    assert response.status_code == 200, response.text
    assert response.json()["changed_count"] == 2 and response.json()["issue_count"] == 0
    assert response.json()["items"] == []  # Issues-only response after correction.
    saved = order_drafts(raw)
    assert [draft.items[0].quantity for draft in saved] == [1, 2]
    assert all(draft.items[0].source["original"]["pack_size"] == "100g+" for draft in saved)
    assert db_session.query(AuditLog).filter_by(event_type="ORDER_QUANTITIES_BULK_CORRECTED").count() == 1
    assert transactional_client.post(url + "/bulk", headers=headers(actor), json=payload).status_code == 409


def test_bulk_rejects_partial_invalid_or_duplicate_edits_without_saving(db_session, transactional_client):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    save_draft(db_session, raw, _draft(_item(1, pack="100g+"), _item(1.05, pack="100g+")))
    url = f"/api/v3/orders/{raw.id}/quantity-review"
    review = transactional_client.get(url, headers=headers(actor)).json()
    payload = {"expected_revision": review["revision"], "row_ids": ["0:0", "0:1"],
               "pack_size": "100g", "note": "Confirmed packing"}
    for update in ({}, {"row_ids": ["0:0", "0:0"]}, {"row_ids": ["0:0", "00:0"]},
                   {"row_ids": ["0:0", "9:9"]}, {"row_ids": ["0:0"], "unit": "invalid"}):
        response = transactional_client.post(url + "/bulk", headers=headers(actor), json={**payload, **update})
        assert response.status_code == 422, response.text
    assert transactional_client.get(url, headers=headers(actor)).json()["revision"] == review["revision"]
    assert db_session.query(AuditLog).filter_by(event_type="ORDER_QUANTITIES_BULK_CORRECTED").count() == 0
