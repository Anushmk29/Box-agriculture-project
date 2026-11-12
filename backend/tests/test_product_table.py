import copy

import pytest
from test_packing_lifecycle import setup_order
from test_simplified_workflow import headers

from app.core.config import settings
from app.models.master_data import ControlStatus, ProductPackProfile
from app.models.order import OrderStatus
from app.models.packing import Box
from app.modules.packing.planning import generate_plan
from app.modules.packing.product_table import (
    apply_reconciliation,
    parse_limits,
    reconciliation,
    row_out,
)
from app.modules.packing.profiles import profile_for, unit_volume
from app.modules.packing.service import PackingPlanningService

pytest_plugins = ["test_simplified_workflow"]


def setup(db):
    _, actor, _, order = setup_order(db)
    product = order.items[0].product
    product.packing_source = {
        "table": {"manual_fields": [], "unresolved": []},
        "legacy_note": "retain",
    }
    db.add(
        ProductPackProfile(
            product=product,
            pack_size="1kg",
            pack_key="1000g",
            volume_cm3=99999,
            net_weight_kg=1,
            reviewed=False,
            aliases=["1 KG"],
            evidence={"source_note": "keep"},
        )
    )
    db.commit()
    return actor, order, product


def edit(client, actor, product, changes, revision=None):
    return client.post(
        "/api/v3/packing-setup/table/bulk",
        headers=headers(actor),
        json={
            "edits": [
                {
                    "id": product.id,
                    "expected_revision": revision or product.packing_revision,
                    "changes": changes,
                }
            ]
        },
    ).json()["results"][0]


def test_partial_save_retains_hidden_data_and_rejects_concurrent_write(
    db_session, transactional_client
):
    actor, _, product = setup(db_session)
    before = (
        product.packing_profiles[0].id,
        product.default_pack_sizes[:],
        product.packing_profiles[0].evidence.copy(),
    )
    revision = product.packing_revision
    result = edit(transactional_client, actor, product, {"volume_l_per_kg": 6})
    assert result["product"]["volume_l_per_kg"] == 6
    db_session.expire_all()
    assert not product.packing_volume_reviewed
    assert (
        product.packing_profiles[0].id,
        product.default_pack_sizes,
        product.packing_profiles[0].evidence,
    ) == before
    assert product.packing_source["legacy_note"] == "retain"
    result = edit(transactional_client, actor, product, {"volume_l_per_kg": 9}, revision)
    assert result["status"] == 409
    assert float(product.packing_volume_l_per_kg) == 6


@pytest.mark.parametrize("withdrawn", [False, True])
def test_reimport_does_not_restore_deliberately_cleared_volume(db_session, withdrawn):
    _, _, product = setup(db_session)
    product.packing_volume_l_per_kg = None
    source = copy.deepcopy(product.packing_source)
    if withdrawn:
        source["volume_web_research"] = {
            "status": "WITHDRAWN_MISMATCH",
            "reason": "Historical profile belongs to a different commodity.",
        }
    else:
        source["table"]["manual_fields"] = ["volume"]
    product.packing_source = source
    for _ in range(2):
        apply_reconciliation(db_session, product, [])
        db_session.flush()
        assert product.packing_volume_l_per_kg is None
    # The historical profile remains available as history, not a new estimate.
    assert float(product.packing_profiles[0].volume_cm3) == 99999
    if withdrawn:
        assert product.packing_source["volume_web_research"] == source["volume_web_research"]


@pytest.mark.parametrize("volume_state", ["missing", "draft", "withdrawn", "reviewed_profile"])
def test_testing_mode_can_prepare_packing_without_confirmed_product_volume(
    db_session, monkeypatch, volume_state
):
    actor, order, product = setup(db_session)
    monkeypatch.setattr(settings, "PACKING_PROFILES_ENABLED", False)
    product.packing_volume_l_per_kg = 6 if volume_state == "draft" else None
    product.packing_volume_reviewed = False
    product.packing_reviewed = False
    product.firmness = None
    product.default_unit_volume_cm3 = 1500
    profile = product.packing_profiles[0]
    profile.reviewed = volume_state == "reviewed_profile"
    profile.volume_cm3 = 2500
    if volume_state == "withdrawn":
        product.packing_source = {
            **product.packing_source,
            "volume_web_research": {"status": "WITHDRAWN_MISMATCH"},
        }
    db_session.commit()

    PackingPlanningService(db_session).prepare_order(
        order_id=order.id, actor=actor, packing_mode="NON_EL"
    )

    boxes = db_session.query(Box).filter_by(client_order_id=order.id).all()
    assert boxes and order.status == OrderStatus.FLOOR_PACKING
    expected = 2500 if volume_state == "reviewed_profile" else 1500
    for box in boxes:
        assert next(iter(box.packing_snapshot["items"].values()))["unit_volume_cm3"] == expected
    assert not product.packing_volume_reviewed


def test_accept_litres_per_kg_ignores_drafts_and_uses_any_order_pack_size(
    db_session, transactional_client, monkeypatch
):
    actor, order, product = setup(db_session)
    result = edit(
        transactional_client,
        actor,
        product,
        {"volume_l_per_kg": 6, "firmness": "SOFT", "confirm": True},
    )
    assert result.get("product", {}).get("ready"), result
    db_session.expire_all()
    product.packing_profiles[0].aliases = ["100g"]
    # Even a conflicting draft alias must not block accepted product volume.
    db_session.add(
        ProductPackProfile(
            product=product,
            pack_size="100g",
            pack_key="100g",
            net_weight_kg=0.1,
            volume_cm3=999999,
            reviewed=False,
        )
    )
    db_session.flush()
    assert profile_for(product, "100g") is None
    assert unit_volume(product, "100g", 0.1) == 600
    assert unit_volume(product, "different weight label", 1) == 6000
    assert unit_volume(product, "2kg", 2) == 12000
    monkeypatch.setattr(settings, "PACKING_PROFILES_ENABLED", True)
    assert profile_for(product, "never seen before") is None
    with pytest.raises(ValueError, match="order weight"):
        unit_volume(product, "1pc")
    from app.models.master_data import BoxCatalogue

    db_session.query(BoxCatalogue).one().packing_reviewed = True
    for item in order.items:
        item.confirmed_pack_count = item.ordered_pack_count
        item.confirmed_net_weight_kg = item.estimated_net_weight_kg
    plan = generate_plan(db_session, order, "NON_EL")
    assert sum(b.total_volume_cm3 for b in plan.boxes) == 36000


def test_missing_el_is_not_non_el_and_cannot_be_confirmed(db_session, transactional_client):
    actor, _, product = setup(db_session)
    product.packing_source = {"table": {"unresolved": ["control_status"], "manual_fields": []}}
    db_session.commit()
    assert row_out(product)["control_status"] is None
    result = edit(
        transactional_client,
        actor,
        product,
        {"volume_l_per_kg": 5, "firmness": "HARD", "confirm": True},
    )
    assert result["status"] == 422
    db_session.expire_all()
    assert product.packing_volume_l_per_kg is None  # Entire failed row rolled back.
    assert edit(
        transactional_client,
        actor,
        product,
        {"control_status": "COMBINABLE", "volume_l_per_kg": 5, "firmness": "HARD", "confirm": True},
    )["product"]["ready"]


def test_unknown_el_cannot_be_silently_packed_as_non_el(db_session):
    from app.modules.packing.profiles import require_known_el
    _, _, product = setup(db_session)
    product.packing_source = {"table": {"unresolved": ["control_status"]}}
    with pytest.raises(ValueError, match="resolve the product EL classification"):
        require_known_el(product, "EL")
    require_known_el(product, "NON_EL")  # Explicit staff choice relaxes EL separation.


def test_grouping_el_wins_and_manual_corrections_survive_reimport(db_session):
    _, _, product = setup(db_session)
    product.t_code = "11-007"
    product.name_th = "คะน้า"
    proposal = reconciliation(product, [])
    assert proposal["fields"]["control_status"] == "COMBINABLE"
    assert proposal["code_rows"][0]["control_status"] == "EL"
    product.control_status = ControlStatus.EL
    product.packing_source = {"table": {"manual_fields": ["control_status"], "unresolved": []}}
    apply_reconciliation(db_session, product, [])
    assert product.control_status == ControlStatus.EL
    before = copy.deepcopy(product.packing_source)
    revision = product.packing_revision
    apply_reconciliation(db_session, product, [])
    assert product.packing_source == before
    assert product.packing_revision == revision


def test_capacity_ranges_preserve_bounds_for_review_and_non_weight_notes(db_session):
    from app.models.master_data import BoxCatalogue, BoxMaterial

    boxes = [
        BoxCatalogue(
            id="flat",
            box_type=BoxMaterial.FOAM,
            size_name="Flat",
            length_cm=54,
            width_cm=42,
            height_cm=21,
        ),
    ]
    limits, issues = parse_limits("โฟมแบน 2.5 - 3.5 กก ฟ.เล็กพิเศษ 3 กก ก.สูง 1 ลูก", boxes)
    assert limits == {}
    assert len(issues) == 3
    assert issues[0]["range_kg"] == [2.5, 3.5]
    assert issues[0]["max_net_weight_kg"] is None
    assert issues[-1]["max_net_weight_kg"] is None


def test_bulk_can_save_valid_row_when_other_row_conflicts(db_session, transactional_client):
    actor, _, product = setup(db_session)
    response = transactional_client.post(
        "/api/v3/packing-setup/table/bulk",
        headers=headers(actor),
        json={
            "edits": [
                {"id": "not-found", "expected_revision": 1, "changes": {"volume_l_per_kg": 3}},
                {
                    "id": product.id,
                    "expected_revision": product.packing_revision,
                    "changes": {"volume_l_per_kg": 2},
                },
            ]
        },
    )
    assert response.status_code == 200
    assert len([r for r in response.json()["results"] if "product" in r]) == 1


def test_inline_new_product_draft_duplicate_rejected(db_session, transactional_client):
    actor, _, _ = setup(db_session)
    body = {"t_code": "NEW-1", "name_en": "New vegetable", "changes": {"volume_l_per_kg": 3}}
    url = "/api/v3/packing-setup/table"
    result = transactional_client.post(url, headers=headers(actor), json=body)
    assert result.status_code == 201, result.text
    assert result.json()["control_status"] is None
    assert not result.json()["ready"]
    assert transactional_client.post(url, headers=headers(actor), json=body).status_code == 409


def test_reimport_preserves_confirmed_staff_settings(db_session, transactional_client):
    actor, _, product = setup(db_session)
    apply_reconciliation(db_session, product, [])
    db_session.commit()
    result = edit(
        transactional_client,
        actor,
        product,
        {
            "box_material": "FOAM",
            "temperature": "COLD",
            "control_status": "EL",
            "weight_class": "HEAVY",
            "firmness": "HARD",
            "volume_l_per_kg": 4,
            "confirm": True,
        },
    )
    assert result["product"]["ready"], result
    db_session.expire_all()
    revision = product.packing_revision
    source = copy.deepcopy(product.packing_source)
    apply_reconciliation(db_session, product, [])
    assert product.packing_source == source
    assert product.packing_revision == revision
    assert product.packing_volume_reviewed and product.packing_reviewed


def test_bad_value_is_a_row_error_without_losing_valid_bulk_edit(db_session, transactional_client):
    from app.models.master_data import Product

    actor, _, product = setup(db_session)
    other = Product(
        t_code="OTHER",
        name_en="Other",
        name_th="Other",
        name_vi="Other",
        box_material=product.box_material,
        temperature=product.temperature,
        control_status=product.control_status,
        weight_class=product.weight_class,
        produce_type=product.produce_type,
    )
    db_session.add(other)
    db_session.commit()
    response = transactional_client.post(
        "/api/v3/packing-setup/table/bulk",
        headers=headers(actor),
        json={
            "edits": [
                {
                    "id": product.id,
                    "expected_revision": product.packing_revision,
                    "changes": {"volume_l_per_kg": -1},
                },
                {
                    "id": other.id,
                    "expected_revision": other.packing_revision,
                    "changes": {"volume_l_per_kg": 2},
                },
            ]
        },
    )
    assert response.status_code == 200
    results = {r["id"]: r for r in response.json()["results"]}
    assert results[product.id]["status"] == 422
    assert results[other.id]["product"]["volume_l_per_kg"] == 2


def test_every_ambiguous_box_note_requires_resolution(db_session, transactional_client):
    from app.models.master_data import BoxCatalogue

    actor, _, product = setup(db_session)
    box = db_session.query(BoxCatalogue).one()
    product.packing_source = {
        "table": {
            "unresolved": ["limits"],
            "manual_fields": [],
            "limit_issues": [
                {"note": "first ambiguous note", "max_net_weight_kg": 2},
                {"note": "piece limit", "max_net_weight_kg": None},
            ],
        }
    }
    db_session.commit()
    result = edit(
        transactional_client,
        actor,
        product,
        {
            "limit_resolutions": {"first ambiguous note": box.id},
            "volume_l_per_kg": 4,
            "firmness": "SOFT",
            "confirm": True,
        },
    )
    assert result["status"] == 422
    result = edit(
        transactional_client,
        actor,
        product,
        {
            "limit_resolutions": {"first ambiguous note": box.id},
            "limits_guidance_only": True,
            "volume_l_per_kg": 4,
            "firmness": "SOFT",
            "confirm": True,
        },
    )
    assert result["product"]["ready"], result
    db_session.expire_all()
    assert product.box_limits[0].reviewed
    assert float(product.box_limits[0].max_net_weight_kg) == 2
