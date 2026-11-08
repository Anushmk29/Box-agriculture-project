import copy
import unicodedata
from types import SimpleNamespace

import pytest
from test_packing_lifecycle import setup_order
from test_simplified_workflow import headers

from app.api import packing_history
from app.core.config import settings
from app.models.master_data import BoxCatalogue
from app.models.packing_evidence import PackingFeedback, PackingHistoryReview
from app.modules.packing import history
from app.modules.packing.constraints import validate_contents
from app.modules.packing.planning import generate_plan, persist_plan
from app.modules.packing.profiles import packing_details, stale_reasons, unit_volume
from app.services.product_identity import ProductIdentityIndex

pytest_plugins = ["test_simplified_workflow"]


def identity(number="one", **names):
    return SimpleNamespace(id=number, t_code=number, numeric_code=None, barcode=None, norway_code=None,
                           **{f"name_{lang}": names.get(lang, "") for lang in ("th", "vi", "en")})


@pytest.mark.parametrize("missing", ["en", "th", "vi"])
def test_match_uses_remaining_two_languages(missing):
    names = {"th": "ตะไคร้", "vi": "Sả", "en": "Lemongrass"}
    product = identity(**names)
    source = {f"name_{lang}": value if lang != missing else "   " for lang, value in names.items()}
    result = ProductIdentityIndex([product]).match(source)
    assert result.product is product
    assert set(result.matched_languages) == set(names) - {missing}
    assert result.missing_languages == [missing]


def test_vietnamese_unicode_equivalence_preserves_accents_and_product_form():
    product = identity(vi="Sả", en="Peeled garlic")
    index = ProductIdentityIndex([product])
    assert index.match({"name_vi": unicodedata.normalize("NFD", "Sả")}).product is product
    assert index.match({"name_vi": "Sa"}).product is None
    assert index.match({"name_vi": "Sả", "name_en": "Whole garlic"}).status == "CONFLICT"
    assert index.match({"name_th": " ", "name_en": None}).product is None


def test_language_conflict_and_duplicate_name_do_not_choose_first_match():
    first = identity("one", th="ตะไคร้", vi="Sả", en="Lemongrass")
    second = identity("two", th="ข่า", vi="Riềng", en="Lemongrass")
    index = ProductIdentityIndex([first, second])
    assert index.match({"name_en": "Lemongrass"}).status == "CONFLICT"
    assert index.match({"name_en": "Lemongrass", "name_th": "ตะไคร้"}).product is first
    assert index.match({"name_th": "ตะไคร้", "name_vi": "Riềng"}).status == "CONFLICT"
    assert index.match({"name_th": "ข่า"}, raw_code="one").status == "CONFLICT"


def test_real_corpus_keeps_continuations_loose_units_and_conflicts():
    data = history.history_data()
    assert data["summary"]["label_pages"] == 1188
    assert data["summary"]["items"] == 3861
    assert not data["extraction_issues"]
    example = next(e for e in data["examples"] if e["shipment"] == "DTD 31.08.26" and e["subcustomer"] == "DTD124" and e["box_number"] == 2)
    assert len(example["pages"]) == 2
    assert len(example["items"]) == 13
    assert next(e for e in data["examples"] if e["shipment"] == "CD 14.09.26")["delivery_date"] == "2026-09-04"
    loose = next(item for e in data["examples"] for item in e["items"] if item["quantity_kind"] == "LOOSE_WEIGHT")
    assert loose["pack_count"] is None
    piece = next(item for e in data["examples"] for item in e["items"] if item["quantity_kind"] == "PIECES")
    assert piece["net_weight_kg"] is None


@pytest.fixture
def scenario(db_session, monkeypatch):
    _, actor, _, order = setup_order(db_session)
    product = order.items[0].product
    product.firmness, product.packing_reviewed = "HARD", True
    product.packing_source = {"table": {"unresolved": [], "planning_basis": "HISTORY"}}
    catalogue = db_session.query(BoxCatalogue).one()
    catalogue.packing_reviewed = True
    db_session.commit()
    example = {"id": "e" * 64, "source": "shipment/labels.pdf", "pages": [1],
               "shipment": "TETE 10.09.26", "subcustomer": "BUYER-1", "customer": "Buyer",
               "box_number": 1, "box_name": "ฟ.เล็ก", "issues": [],
               "items": [{"code": product.t_code, "name_th": product.name_th,
                          "name_vi": product.name_vi, "name_en": product.name_en,
                          "pack_size": "1kg", "pack_count": 2, "net_weight_kg": 2,
                          "issues": []}]}
    data = {"version": "v" * 64, "examples": [example], "summary": {"examples": 1}}
    monkeypatch.setattr(history, "history_data", lambda: data)
    monkeypatch.setattr(packing_history, "history_data", lambda: data)
    monkeypatch.setattr(settings, "PACKING_PROFILES_ENABLED", True)
    payload = {"source_version": data["version"], "expected_revision": 0, "status": "APPROVED",
               "box_catalogue_id": catalogue.id, "route_code": "TETE", "packing_mode": "NON_EL",
               "verified_fit": True, "source_issues_acknowledged": False,
               "note": "Packing staff checked this complete combination",
               "items": [{"product_id": product.id, "pack_size": "1kg", "pack_count": 2, "net_weight_kg": 2}]}
    return actor, order, product, catalogue, example, payload


def approve(client, scenario, **updates):
    actor, _, _, _, example, payload = scenario
    return client.put(f"/api/v3/packing-history/{example['id']}/review", headers=headers(actor), json={**payload, **updates})


def test_history_needs_explicit_fit_review_and_exact_current_inputs(db_session, transactional_client, scenario):
    actor, order, product, catalogue, example, payload = scenario
    response = approve(transactional_client, scenario, verified_fit=False)
    assert response.status_code == 422, response.text
    assert db_session.query(PackingHistoryReview).count() == 0
    response = approve(transactional_client, scenario)
    assert response.status_code == 200, response.text
    plan = generate_plan(db_session, order, "NON_EL")
    assert len(plan.boxes) == 2
    assert sum(b.total_net_weight_kg for b in plan.boxes) == 4
    assert all(b.total_volume_cm3 is None for b in plan.boxes)
    boxes = persist_plan(db_session, order, plan, "NON_EL")
    assert all(not stale_reasons(box) for box in boxes)
    for box in boxes:
        validate_contents(box)
        assert packing_details(box)["volume_cm3"] is None
        assert box.packing_snapshot["history_evidence"]["basis"] == "HISTORICAL_VERIFIED"
        assert box.packing_snapshot["initial_plan"]["items"][0]["pack_count"] == 2
    with pytest.raises(ValueError, match="historical template"):
        unit_volume(product, "1kg", 1)
    product.packing_revision += 1
    db_session.flush()
    assert stale_reasons(boxes[0])


def test_review_conflicts_and_uncovered_remainders_are_not_silently_approved(db_session, transactional_client, scenario):
    _, order, _, _, _, payload = scenario
    assert approve(transactional_client, scenario).status_code == 200
    assert approve(transactional_client, scenario).status_code == 409
    order.items[0].confirmed_pack_count = 3
    order.items[0].confirmed_net_weight_kg = 3
    with pytest.raises(ValueError, match="remaining packs"):
        generate_plan(db_session, order, "NON_EL")


def test_rejection_revokes_current_boxes_but_preserves_locked_history(db_session, transactional_client, scenario):
    assert approve(transactional_client, scenario).status_code == 200
    _, order, *_ = scenario
    boxes = persist_plan(db_session, order, generate_plan(db_session, order, "NON_EL"), "NON_EL")
    original = copy.deepcopy(boxes[0].packing_snapshot)
    boxes[0].is_locked = True
    db_session.commit()
    response = approve(transactional_client, scenario, expected_revision=1, status="REJECTED", note="Withdrawn after checking physical packing")
    assert response.status_code == 200, response.text
    assert not stale_reasons(boxes[0])
    assert boxes[0].packing_snapshot == original
    assert stale_reasons(boxes[1])


def test_feedback_preserves_suggestion_and_retries_without_duplicate(db_session, transactional_client, scenario):
    actor, order, *_ = scenario
    assert approve(transactional_client, scenario).status_code == 200
    box = persist_plan(db_session, order, generate_plan(db_session, order, "NON_EL"), "NON_EL")[0]
    db_session.commit()
    path = f"/api/v3/packing-history/boxes/{box.id}/feedback"
    body = {"expected_revision": order.packing_revision, "outcome": "FIT", "note": ""}
    for _ in range(2):
        response = transactional_client.post(path, headers=headers(actor), json=body)
        assert response.status_code == 200, response.text
    assert db_session.query(PackingFeedback).count() == 1
    assert db_session.query(PackingFeedback).one().snapshot["proposed"]["items"][0]["pack_count"] == 2
    response = transactional_client.post(path, headers=headers(actor), json={**body, "outcome": "DAMAGE", "note": "Product arrived crushed"})
    assert response.status_code == 200
    assert db_session.query(PackingFeedback).count() == 2


def test_approval_rejects_weight_or_material_violations(transactional_client, scenario):
    actor, order, product, catalogue, example, payload = scenario
    payload["items"][0]["net_weight_kg"] = 6
    response = approve(transactional_client, scenario)
    assert response.status_code == 422 and "safe weight" in response.text


def test_history_basis_can_confirm_without_volume_and_missing_names_stay_missing(transactional_client, db_session, scenario):
    actor, _, product, *_ = scenario
    product.packing_reviewed = False
    db_session.commit()
    response = transactional_client.post("/api/v3/packing-setup/table/bulk", headers=headers(actor), json={"edits": [{
        "id": product.id, "expected_revision": product.packing_revision,
        "changes": {"planning_basis": "HISTORY", "confirm": True},
    }]})
    row = response.json()["results"][0]
    assert row["product"]["ready"], row
    assert row["product"]["volume_l_per_kg"] is None
    response = transactional_client.post("/api/v3/packing-setup/table", headers=headers(actor), json={
        "t_code": "THAI-ONLY", "name_th": "ตะไคร้", "name_en": "", "name_vi": "",
    })
    assert response.status_code == 201, response.text
    assert response.json()["name_en"] == ""
    assert response.json()["name_vi"] == ""


def test_extending_corpus_preserves_unchanged_approval_and_changed_settings_need_review(transactional_client, db_session, scenario):
    actor, _, product, *_ = scenario
    assert approve(transactional_client, scenario).status_code == 200
    history.history_data()["version"] = "new corpus"
    response = transactional_client.get("/api/v3/packing-history?status=APPROVED", headers=headers(actor))
    assert response.status_code == 200
    assert response.json()["summary"]["approved"] == 1
    product.packing_revision += 1
    db_session.commit()
    response = transactional_client.get("/api/v3/packing-history?status=unreviewed", headers=headers(actor))
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["review"]["approval_issues"]


def test_duplicate_demand_lines_preserve_counts_and_weight(db_session, transactional_client, scenario):
    from app.modules.packing.history import allocate_history
    from app.modules.packing.optimizer import PackingOption

    assert approve(transactional_client, scenario).status_code == 200
    _, order, product, catalogue, *_ = scenario
    options = [PackingOption(catalogue.id, catalogue.box_type, 5, 14400)]
    boxes, remaining = allocate_history(db_session, [(product, "1kg", 2, 2), (product, "1000g", 2, 2)],
                                        options, route_code="TETE", mode="NON_EL", order=order)
    assert not remaining
    assert len(boxes) == 2
    assert sum(item.pack_count for box in boxes for item in box.items) == 4
    assert sum(box.total_net_weight_kg for box in boxes) == 4


def test_history_approval_requires_physical_pack_unit_and_confirmed_settings(transactional_client, scenario):
    _, _, product, _, _, payload = scenario
    payload["items"][0]["pack_size"] = "KG"
    response = approve(transactional_client, scenario)
    assert response.status_code == 422 and "physical pack format" in response.text
    payload["items"][0]["pack_size"] = "1kg"
    product.packing_reviewed = False
    response = approve(transactional_client, scenario)
    assert response.status_code == 422 and "confirm product settings" in response.text


def test_order_adapter_preserves_available_thai_and_vietnamese_without_english(db_session, scenario):
    from app.modules.orders.intake import OrderIntake, matrix_to_drafts

    _, _, product, *_ = scenario
    parsed = {"clients_data": {"BUYER": [{"t_code": "", "name_th": product.name_th,
               "name_vi": product.name_vi, "name_en": "", "pack_size": "1kg", "quantity": 2}]}}
    item = matrix_to_drafts(parsed, "fixture.xlsx")[0].items[0]
    assert {item.product_name, *item.alt_names} == {product.name_th, product.name_vi}
    assert OrderIntake(db_session).resolve(item, None).id == product.id
    parsed["clients_data"]["BUYER"][0]["name_vi"] = ""
    item = matrix_to_drafts(parsed, "fixture.xlsx")[0].items[0]
    assert item.product_name == product.name_th and not item.alt_names
    assert OrderIntake(db_session).resolve(item, None).id == product.id


def test_cached_review_is_refreshed_before_planning_and_fit_checks(db_session, transactional_client, scenario):
    import sqlalchemy as sa

    assert approve(transactional_client, scenario).status_code == 200
    _, order, *_ = scenario
    cached = db_session.query(PackingHistoryReview).one()
    db_session.execute(sa.update(PackingHistoryReview).values(revision=2), execution_options={"synchronize_session": False})
    plan = generate_plan(db_session, order, "NON_EL")
    assert plan.boxes[0].history_evidence["revision"] == 2
    boxes = persist_plan(db_session, order, plan, "NON_EL")
    db_session.execute(sa.update(PackingHistoryReview).values(status="REJECTED", revision=3), execution_options={"synchronize_session": False})
    assert cached.status == "APPROVED"  # Deliberately stale identity-map state.
    assert stale_reasons(boxes[0])


def test_history_search_includes_master_vietnamese_name_when_source_name_is_missing(transactional_client, scenario):
    actor, _, product, *_ = scenario
    history.history_data()["examples"][0]["items"][0]["name_vi"] = ""
    response = transactional_client.get("/api/v3/packing-history", params={"q": product.name_vi}, headers=headers(actor))
    assert response.status_code == 200
    assert response.json()["total"] == 1
