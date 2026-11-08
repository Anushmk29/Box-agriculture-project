import pytest
from test_order_intake import _draft, _item, _setup
from test_simplified_workflow import headers

from app.models.audit import AuditLog
from app.models.order import ClientOrder, ClientOrderItem
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


def test_keep_as_ordered_records_review_without_changing_values_or_requiring_reason(db_session, transactional_client):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    item = _item(10, unit="kg", pack="600g+")
    item.source = {"sheet": "Orders", "row": 33, "raw_pack_size": "600g+"}
    save_draft(db_session, raw, _draft(item))
    url = f"/api/v3/orders/{raw.id}/quantity-review"
    review = transactional_client.get(url, headers=headers(actor)).json()
    assert review["items"][0]["can_keep_as_ordered"]
    payload = {"expected_revision": review["revision"], "row_ids": ["0:0"]}
    response = transactional_client.post(url + "/keep", headers=headers(actor), json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["issue_count"] == 0 and saved["reviewed_count"] == 1
    row = saved["changed_rows"][0]
    assert row["computed"] == {"pack_count": None, "net_weight_kg": 10}
    assert row["reviewed_unchanged"] and row["quantity"] == 10 and row["pack_size"] == "600g+"
    current = order_drafts(raw)[0].items[0]
    assert current.model_dump(exclude={"source"}) == item.model_dump(exclude={"source"})
    assert current.source["raw_pack_size"] == "600g+" and "review_note" not in current.source
    assert current.source["quantity_review"]["reviewed_by"] == actor.id
    assert db_session.query(AuditLog).filter_by(event_type="ORDER_QUANTITIES_REVIEWED").count() == 1
    assert db_session.query(AuditLog).filter_by(event_type="ORDER_QUANTITY_CORRECTED").count() == 0
    assert transactional_client.post(url + "/keep", headers=headers(actor), json=payload).status_code == 409
    assert OrderValidationService(db_session).validate(raw_file_id=raw.id, actor=actor).can_split


@pytest.mark.parametrize("item", [
    _item(1, unit="unknown", pack="loose"),
    _item(1.5, unit="pack", pack="100g"),
    _item(1, unit="kg", pack="loose").model_copy(update={"net_weight_kg": 2}),
    _item(1, unit="kg", pack="loose").model_copy(update={"quantity_issues": ["Conflicting source columns"]}),
])
def test_keep_cannot_override_invalid_quantities_and_is_atomic(db_session, transactional_client, item):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    save_draft(db_session, raw, _draft(_item(10, unit="kg", pack="loose"), item))
    url = f"/api/v3/orders/{raw.id}/quantity-review"
    review = transactional_client.get(url, headers=headers(actor)).json()
    assert not review["items"][1]["can_keep_as_ordered"]
    response = transactional_client.post(url + "/keep", headers=headers(actor), json={
        "expected_revision": review["revision"], "row_ids": ["0:0", "0:1"],
    })
    assert response.status_code == 422, response.text
    assert transactional_client.get(url, headers=headers(actor)).json()["revision"] == review["revision"]
    assert db_session.query(AuditLog).filter_by(event_type="ORDER_QUANTITIES_REVIEWED").count() == 0


def test_keep_review_rejects_invalid_selection_and_corrections_still_need_reason(db_session, transactional_client):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    save_draft(db_session, raw, _draft(_item(10, unit="kg", pack="loose")))
    url = f"/api/v3/orders/{raw.id}/quantity-review"
    revision = transactional_client.get(url, headers=headers(actor)).json()["revision"]
    for row_ids in [[], ["0:0", "0:0"], ["00:0"], ["-1:0"], ["0:5"]]:
        assert transactional_client.post(url + "/keep", headers=headers(actor), json={
            "expected_revision": revision, "row_ids": row_ids,
        }).status_code == 422
    assert transactional_client.put(url + "/0:0", headers=headers(actor), json={
        "expected_revision": revision, "quantity": 1, "unit": "kg", "pack_size": "100g", "note": "",
    }).status_code == 422
    assert transactional_client.get(url, headers=headers(actor)).json()["revision"] == revision


def test_keep_all_reviews_every_page_and_preserves_values_and_invalid_lines(db_session, transactional_client):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    eligible = [_item(index + 1, unit="kg", pack="600g+") for index in range(205)]
    for index, item in enumerate(eligible):
        item.source = {"row": index + 1, "raw_pack_size": "600g+"}
    valid = _item(2, unit="kg", pack="100g")
    invalid = [_item(1, unit="unknown", pack="loose"), _item(1.5, unit="pack", pack="100g")]
    drafts = [_draft(*eligible, valid, client="SHOP-A"), _draft(*invalid, client="SHOP-B")]
    original = [[item.model_dump(mode="json") for item in draft.items] for draft in drafts]
    raw.acknowledgment_summary = {"drafts": [draft.model_dump(mode="json") for draft in drafts]}
    db_session.commit()
    url = f"/api/v3/orders/{raw.id}/quantity-review"
    review = transactional_client.get(url + "?issues_only=true&limit=1", headers=headers(actor)).json()
    assert review["keepable_issue_count"] == 205 and review["issue_count"] == 207
    assert len(review["items"]) == 1 and review["has_more"]
    response = transactional_client.post(url + "/keep", headers=headers(actor), json={
        "expected_revision": review["revision"], "all_eligible": True,
    })
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["changed_count"] == saved["reviewed_count"] == 205
    assert saved["issue_count"] == 2 and saved["keepable_issue_count"] == 0
    assert {row["row_id"] for row in saved["items"]} == {"1:0", "1:1"}
    current = order_drafts(raw)
    for d_index, draft in enumerate(current):
        for index, item in enumerate(draft.items):
            expected = dict(original[d_index][index])
            source = expected.pop("source")
            assert item.model_dump(mode="json", exclude={"source"}) == expected
            assert {key: item.source[key] for key in source} == source
    assert "quantity_review" not in current[0].items[-1].source
    assert all("quantity_review" not in item.source for item in current[1].items)
    assert all(item.source["quantity_review"]["reviewed_by"] == actor.id for item in current[0].items[:-1])
    assert db_session.query(AuditLog).filter_by(event_type="ORDER_QUANTITIES_REVIEWED").count() == 1
    assert transactional_client.post(url + "/keep", headers=headers(actor), json={
        "expected_revision": review["revision"], "all_eligible": True,
    }).status_code == 409
    # A fresh retry with no eligible issues makes no further changes or audit entries.
    repeat = transactional_client.post(url + "/keep", headers=headers(actor), json={
        "expected_revision": saved["revision"], "all_eligible": True,
    })
    assert repeat.status_code == 200 and repeat.json()["changed_count"] == 0
    assert repeat.json()["revision"] == saved["revision"]
    assert db_session.query(AuditLog).filter_by(event_type="ORDER_QUANTITIES_REVIEWED").count() == 1


def test_keep_all_clears_review_and_cannot_mix_selection_modes(db_session, transactional_client):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    save_draft(db_session, raw, _draft(_item(10, unit="kg", pack="loose")))
    url = f"/api/v3/orders/{raw.id}/quantity-review"
    revision = transactional_client.get(url, headers=headers(actor)).json()["revision"]
    for selection in [{}, {"all_eligible": False}, {"all_eligible": True, "row_ids": ["0:0"]}]:
        assert transactional_client.post(url + "/keep", headers=headers(actor), json={
            "expected_revision": revision, **selection,
        }).status_code == 422
    response = transactional_client.post(url + "/keep?issues_only=false", headers=headers(actor), json={
        "expected_revision": revision, "all_eligible": True,
    })
    assert response.status_code == 200, response.text
    assert response.json()["issue_count"] == response.json()["keepable_issue_count"] == 0
    assert response.json()["items"][0]["computed"] == {"pack_count": None, "net_weight_kg": 10}
    assert OrderValidationService(db_session).validate(raw_file_id=raw.id, actor=actor).can_split


@pytest.mark.parametrize("amount, unit", [(15, "kg"), (15000, "g")])
def test_keep_fifteen_kg_in_eighty_gram_packs_preserves_weight(db_session, transactional_client, amount, unit):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    item = _item(amount, unit=unit, pack="80g")
    save_draft(db_session, raw, _draft(item))
    url = f"/api/v3/orders/{raw.id}/quantity-review"
    review = transactional_client.get(url, headers=headers(actor)).json()
    assert review["keepable_issue_count"] == 1 and review["items"][0]["can_keep_as_ordered"]
    response = transactional_client.post(url + "/keep", headers=headers(actor), json={
        "expected_revision": review["revision"], "all_eligible": True,
    })
    assert response.status_code == 200, response.text
    assert response.json()["issue_count"] == 0
    assert response.json()["changed_rows"][0]["computed"] == {"pack_count": None, "net_weight_kg": 15}
    current = order_drafts(raw)[0].items[0]
    assert current.model_dump(exclude={"source"}) == item.model_dump(exclude={"source"})
    assert OrderValidationService(db_session).validate(raw_file_id=raw.id, actor=actor).can_split
    OrderIntake(db_session).materialize(raw_file=raw, drafts=order_drafts(raw))
    line = db_session.query(ClientOrderItem).one()
    assert line.ordered_pack_count == 0 and float(line.estimated_net_weight_kg) == 15
    assert line.pack_size == "80g"


def test_unchanged_review_expires_when_values_change_and_does_not_infer_variable_pack_counts(monkeypatch):
    from app.modules.orders import quantities

    item = _item(10, unit="kg", pack="600g+")
    item.source["quantity_review"] = {"decision": "KEEP_AS_ORDERED", "reviewed_by": "staff",
                                      "values": quantities.quantity_snapshot(item)}
    # Keeping a variable-weight order must not consult an assumed piece weight.
    monkeypatch.setattr(quantities, "profile_for", lambda *args, **kwargs: pytest.fail("No profile conversion for weight-only review"))
    result = quantities.derive_quantity(item, product=object())
    assert result.packs == 0 and result.net_kg == 10
    item.quantity = 12
    assert not quantities.kept_as_ordered(item)
    with pytest.raises(ValueError, match="physical pack size"):
        quantities.derive_quantity(item)


@pytest.mark.parametrize("pack_size", ["loose", "90g"])
@pytest.mark.parametrize("packs, kg, allocated_kg", [(6, 3, 3), (6, 2.9999, 2.9999), (6, 4, 3), (0, 0, 0), (1, 1, .6667)])
def test_kept_weight_orders_reach_purchasing_handoff_and_packing(db_session, transactional_client, packs, kg, allocated_kg, pack_size):
    from app.models.master_data import BoxCatalogue
    from app.modules.handoffs.service import HandoffService
    from app.modules.packing.catalogue import dimension_reference
    from app.modules.packing.planning import generate_plan
    from app.modules.procurement.batch import PurchasingBatchService
    from app.modules.procurement.service import (
        ProcurementConfirmation,
        ProcurementConfirmationService,
    )

    raw, product = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    raw.acknowledgment_summary = {"drafts": [d.model_dump(mode="json") for d in [
        _draft(_item(1, unit="kg", pack=pack_size), client="SHOP-A"),
        _draft(_item(2, unit="kg", pack=pack_size), client="SHOP-B"),
    ]]}
    db_session.commit()
    url = f"/api/v3/orders/{raw.id}/quantity-review"
    revision = transactional_client.get(url, headers=headers(actor)).json()["revision"]
    response = transactional_client.post(url + "/keep", headers=headers(actor), json={
        "expected_revision": revision, "row_ids": ["0:0", "1:0"],
    })
    assert response.status_code == 200, response.text
    validation = transactional_client.get(f"/api/v3/orders/{raw.id}/validate", headers=headers(actor))
    assert validation.json()["can_split"]
    split = transactional_client.post(f"/api/v3/orders/{raw.id}/split", headers=headers(actor))
    assert split.status_code == 200, split.text
    orders = db_session.query(ClientOrder).filter_by(raw_order_file_id=raw.id).all()
    assert len(orders) == 2
    assert all(order.items[0].ordered_pack_count == 0 for order in orders)
    batch = PurchasingBatchService(db_session).get(split.json()["batch_id"])
    purchase = batch.items[0]
    assert float(purchase.ordered_net_weight_kg) == 3 and purchase.total_required_pack_count == 0
    ProcurementConfirmationService(db_session).confirm_batch(batch_id=batch.id, actor=actor,
        confirmations=[ProcurementConfirmation(purchase.id, packs, kg)])
    handoff = HandoffService(db_session).seal_handoff_01(batch=batch, actor=actor)
    lines = [row for order in handoff.payload_json["confirmed_orders"] for row in order["items"]]
    assert sum(line["confirmed_pack_count"] for line in lines) == packs
    assert sum(line["confirmed_net_weight_kg"] for line in lines) == pytest.approx(allocated_kg)
    assert all(line["quantity_basis"] == "WEIGHT" for line in lines)
    dimensions = next(row for row in dimension_reference()["rows"] if row["box_type"] == product.box_material.value)
    db_session.add(BoxCatalogue(box_type=product.box_material, size_name="Reviewed box",
        **{f"{axis}_cm": dimensions[f"{axis}_cm"] for axis in ["length", "width", "height"]},
        rated_weight_kg=10, max_safe_net_weight_kg=10, volume_m3=.036))
    db_session.flush()
    for order in orders:
        if not order.items[0].confirmed_pack_count:
            continue
        plan = generate_plan(db_session, order, "NON_EL")
        assert sum(line.pack_count for box in plan.boxes for line in box.items) == order.items[0].confirmed_pack_count
