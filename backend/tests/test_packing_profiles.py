import pytest
from test_packing_lifecycle import setup_order
from test_simplified_workflow import headers

from app.api.packing_setup import product_out
from app.core.config import settings
from app.models.audit import AuditLog
from app.models.master_data import (
    BoxCatalogue,
    ControlStatus,
    Product,
    ProductBoxLimit,
    ProductPackProfile,
)
from app.models.order import OrderStatus
from app.models.packing import Box, BoxItem
from app.modules.packing.constraints import validate_contents
from app.modules.packing.floor import FloorPackingService
from app.modules.packing.planning import apply_replan, preview_replan
from app.modules.packing.profiles import pack_key, packing_details, stale_reasons
from app.modules.packing.service import FinalPackingService, PackingPlanningService

pytest_plugins = ["test_simplified_workflow"]


@pytest.fixture
def reviewed(db_session, monkeypatch):
    _, actor, _, order = setup_order(db_session)
    product = order.items[0].product
    product.firmness = "SOFT"
    product.packing_reviewed = True
    catalogue = db_session.query(BoxCatalogue).one()
    catalogue.packing_reviewed = True
    db_session.add(
        ProductPackProfile(
            product_id=product.id,
            pack_size="1kg",
            pack_key="1000g",
            net_weight_kg=1,
            volume_cm3=2000,
            reviewed=True,
        )
    )
    db_session.commit()
    monkeypatch.setattr(settings, "PACKING_PROFILES_ENABLED", True)
    return actor, order, product, catalogue


def prepare(db, reviewed, mode="EL"):
    actor, order, _, _ = reviewed
    PackingPlanningService(db).prepare_order(order_id=order.id, actor=actor, packing_mode=mode)
    return db.query(Box).filter_by(client_order_id=order.id).one()


def payload(product):
    data = product_out(product)
    return {**data, "expected_revision": product.packing_revision}


def test_reference_drafts_are_optional_audited_and_idempotent(
    db_session, reviewed, transactional_client
):
    actor, _, product, _ = reviewed
    product.name_th = "โหระพา"
    product.default_pack_sizes = ["100g bag", "100g tray", "1pc"]
    db_session.commit()
    url = "/api/v3/packing-setup/reference-drafts"
    preview = transactional_client.get(url, headers=headers(actor)).json()
    assert preview["pack_profiles"] >= 3 and preview["with_volume_estimate"] >= 2
    existing = product.packing_profiles[0].id
    response = transactional_client.post(
        url, headers=headers(actor), json={"expected_revision": preview["revision"]}
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()
    added = [p for p in product.packing_profiles if p.id != existing]
    assert all(not p.reviewed and p.volume_cm3 is None for p in added)
    assert {"100g bag", "100g tray", "1pc"} <= {p.pack_key for p in added}
    preview = transactional_client.get(url, headers=headers(actor)).json()
    assert preview["pack_profiles"] == 0
    assert db_session.query(ProductPackProfile).filter_by(id=existing).one().reviewed


def test_staff_can_save_incomplete_drafts_but_cannot_approve_them(
    db_session, reviewed, transactional_client
):
    actor, _, product, _ = reviewed
    body = payload(product)
    body["profiles"].append(
        {
            "pack_size": "100g tray",
            "net_weight_kg": 0.1,
            "volume_cm3": None,
            "reviewed": False,
            "evidence": {"weight_source": "ORDER_LABEL", "volume_source": "UNKNOWN"},
        }
    )
    url = f"/api/v3/packing-setup/products/{product.id}"
    response = transactional_client.put(url, headers=headers(actor), json=body)
    assert response.status_code == 200, response.text
    body = {**response.json(), "expected_revision": response.json()["packing_revision"]}
    body["profiles"][-1]["reviewed"] = True
    assert transactional_client.put(url, headers=headers(actor), json=body).status_code == 422


def test_approved_estimates_remain_visible_in_box_snapshot(
    db_session, reviewed, transactional_client
):
    actor, _, product, _ = reviewed
    body = payload(product)
    body["profiles"][0]["evidence"] = {
        "weight_source": "ORDER_LABEL",
        "volume_source": "PROXY",
        "source_url": "https://www.fao.org/",
        "source_note": "Reviewed comparator for this packing format",
    }
    response = transactional_client.put(
        f"/api/v3/packing-setup/products/{product.id}", headers=headers(actor), json=body
    )
    assert response.status_code == 200, response.text
    box = prepare(db_session, reviewed)
    assert packing_details(box)["layers"][0]["volume_source"] == "PROXY"
    assert next(iter(box.packing_snapshot["items"].values()))["evidence"]["source_note"]


def test_batch_measurement_is_calculated_on_server(db_session, reviewed, transactional_client):
    actor, _, product, _ = reviewed
    body = payload(product)
    body["profiles"][0]["volume_cm3"] = 123
    body["profiles"][0]["evidence"] = {
        "measurement": {"length_cm": 20, "width_cm": 10, "filled_height_cm": 5, "pack_count": 4}
    }
    response = transactional_client.put(
        f"/api/v3/packing-setup/products/{product.id}", headers=headers(actor), json=body
    )
    assert response.status_code == 200, response.text
    profile = response.json()["profiles"][0]
    assert profile["volume_cm3"] == 250
    assert profile["evidence"]["volume_source"] == "MEASURED"


def test_existing_draft_can_refresh_guidance_from_an_unsaved_source_choice(
    db_session, reviewed, transactional_client
):
    actor, _, product, _ = reviewed
    original = product.packing_source
    response = transactional_client.get(
        f"/api/v3/packing-setup/products/{product.id}/volume-guidance",
        headers=headers(actor),
        params={"pack_size": "100g bag", "net_weight_kg": 0.1, "source_rows": "7"},
    )
    assert response.status_code == 200, response.text
    evidence = response.json()["evidence"]
    assert evidence["estimate_high_cm3"] > 1000 and evidence["volume_source"] == "PROXY"
    assert evidence["research_rows"] == [7]
    assert product.packing_source == original
    assert float(product.packing_profiles[0].volume_cm3) == 2000


@pytest.mark.parametrize(
    "label,expected",
    [
        ("100Gr.", "100g"),
        ("100G", "100g"),
        ("1 kg", "1000g"),
        ("100g tray", "100g tray"),
        ("Pcs", "1pc"),
    ],
)
def test_pack_alias_normalization(label, expected):
    assert pack_key(label) == expected


def test_bare_pack_number_requires_a_unit():
    with pytest.raises(ValueError, match="unit"):
        pack_key("500")


def test_strict_prepare_needs_reviewed_profile_before_creating_boxes(db_session, reviewed):
    actor, order, product, _ = reviewed
    product.packing_profiles[0].reviewed = False
    with pytest.raises(ValueError, match="review pack weight and volume"):
        prepare(db_session, reviewed)
    assert db_session.query(Box).count() == 0
    assert order.status == OrderStatus.HANDOFF_01_SENT


def test_real_pack_volume_controls_box_count(db_session, reviewed):
    reviewed[2].packing_profiles[0].volume_cm3 = 12000
    actor, order, _, _ = reviewed
    PackingPlanningService(db_session).prepare_order(
        order_id=order.id, actor=actor, packing_mode="NON_EL"
    )
    assert db_session.query(Box).count() == 4
    assert all(box.packing_mode == "NON_EL" for box in db_session.query(Box))


def test_measurements_differ_by_pack_size(db_session, reviewed):
    product = reviewed[2]
    db_session.add(
        ProductPackProfile(
            product_id=product.id,
            pack_size="100g",
            pack_key="100g",
            net_weight_kg=0.1,
            volume_cm3=500,
            reviewed=True,
        )
    )
    db_session.flush()
    db_session.expire(product, ["packing_profiles"])
    from app.modules.packing.profiles import unit_volume

    assert unit_volume(product, "100Gr.") == 500
    assert unit_volume(product, "1kg") == 2000


@pytest.mark.parametrize("route", ["NGA", "TETE", "CT"])
def test_el_enforcement_is_staff_selected_not_country(db_session, reviewed, route):
    actor, order, product, _ = reviewed
    box = prepare(db_session, reviewed, "NON_EL")
    box.route_code = route
    other = Product(
        t_code="CONTROLLED",
        name_en="Controlled herb",
        name_vi="Controlled",
        name_th="Controlled",
        box_material=product.box_material,
        temperature=product.temperature,
        control_status=ControlStatus.EL,
        produce_type=product.produce_type,
        weight_class=product.weight_class,
        firmness="SOFT",
        packing_reviewed=True,
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(
        ProductPackProfile(
            product_id=other.id,
            pack_size="1kg",
            pack_key="1000g",
            net_weight_kg=1,
            volume_cm3=1000,
            reviewed=True,
        )
    )
    db_session.flush()
    rows = [(product, "1kg", 1, 1), (other, "1kg", 1, 1)]
    validate_contents(box, rows)
    box.packing_mode = "EL"
    with pytest.raises(ValueError, match="incompatible"):
        validate_contents(box, rows)


def test_reviewed_single_product_box_limit_is_enforced(db_session, reviewed):
    _, _, product, catalogue = reviewed
    db_session.add(
        ProductBoxLimit(
            product_id=product.id, box_catalogue_id=catalogue.id, max_net_weight_kg=2, reviewed=True
        )
    )
    db_session.flush()
    db_session.expire(product, ["box_limits"])
    actor, order, _, _ = reviewed
    PackingPlanningService(db_session).prepare_order(
        order_id=order.id, actor=actor, packing_mode="EL"
    )
    assert db_session.query(Box).count() == 2
    assert all(
        sum(float(i.final_net_weight_kg) for i in b.items) <= 2 for b in db_session.query(Box)
    )


def test_layers_are_ordered_hard_semi_hard_soft(db_session, reviewed):
    box = prepare(db_session, reviewed, "NON_EL")
    product = reviewed[2]
    for firmness in ["SEMI_HARD", "HARD"]:
        other = Product(
            t_code=firmness,
            name_en=firmness,
            name_vi=firmness,
            name_th=firmness,
            box_material=product.box_material,
            temperature=product.temperature,
            control_status=product.control_status,
            produce_type=product.produce_type,
            weight_class=product.weight_class,
            firmness=firmness,
            packing_reviewed=True,
        )
        db_session.add(other)
        db_session.flush()
        db_session.add(
            ProductPackProfile(
                product_id=other.id,
                pack_size="1kg",
                pack_key="1000g",
                net_weight_kg=0.1,
                volume_cm3=500,
                reviewed=True,
            )
        )
        db_session.flush()
        FloorPackingService(db_session).save_item(
            box.id,
            actor=reviewed[0],
            product_id=other.id,
            pack_size="1kg",
            quantity=1,
            net_weight_kg=0.1,
        )
    assert [row["layer"] for row in packing_details(box)["layers"]] == ["Bottom", "Middle", "Top"]


def test_profile_change_blocks_finalization_until_reviewed_replan(db_session, reviewed):
    actor, order, product, _ = reviewed
    box = prepare(db_session, reviewed)
    product.packing_revision += 1
    product.packing_profiles[0].volume_cm3 = 3000
    db_session.flush()
    assert stale_reasons(box)
    with pytest.raises(ValueError, match="replan"):
        FinalPackingService(db_session).finalize_order(order_id=order.id, actor=actor)
    preview = preview_replan(db_session, order, "NON_EL")
    old_id = box.id
    boxes = apply_replan(db_session, order, preview["preview_token"], actor)
    assert old_id not in {box.id for box in boxes}
    assert sum(item.final_packed_count for box in boxes for item in box.items) == 4
    assert sum(float(item.final_net_weight_kg) for box in boxes for item in box.items) == 4
    FinalPackingService(db_session).finalize_order(order_id=order.id, actor=actor)
    assert order.status == OrderStatus.HANDOFF_02_RETURNED


def test_stale_preview_never_discards_newer_floor_adjustments(db_session, reviewed):
    actor, order, product, _ = reviewed
    box = prepare(db_session, reviewed)
    preview = preview_replan(db_session, order, "EL")
    item = box.items[0]
    FloorPackingService(db_session).save_item(
        box.id,
        actor=actor,
        product_id=product.id,
        item_id=item.id,
        pack_size="1kg",
        quantity=3,
        net_weight_kg=3,
    )
    with pytest.raises(ValueError, match="changed"):
        apply_replan(db_session, order, preview["preview_token"], actor)
    assert db_session.query(Box).one().id == box.id
    assert db_session.query(BoxItem).one().final_packed_count == 3


def test_setup_api_audits_changes_and_rejects_stale_editor(
    db_session, reviewed, transactional_client
):
    actor, _, product, _ = reviewed
    body = payload(product)
    response = transactional_client.put(
        f"/api/v3/packing-setup/products/{product.id}", headers=headers(actor), json=body
    )
    assert response.status_code == 200, response.text
    assert response.json()["packing_revision"] == body["expected_revision"] + 1
    assert (
        transactional_client.put(
            f"/api/v3/packing-setup/products/{product.id}", headers=headers(actor), json=body
        ).status_code
        == 409
    )
    assert db_session.query(AuditLog).filter_by(event_type="PRODUCT_PACKING_UPDATED").count() == 1


def test_setup_rejects_overlapping_aliases_atomically(db_session, reviewed, transactional_client):
    actor, _, product, _ = reviewed
    body = payload(product)
    body["profiles"].append(
        {
            "pack_size": "200g",
            "aliases": ["1 kg"],
            "net_weight_kg": 0.2,
            "volume_cm3": 500,
            "reviewed": True,
        }
    )
    response = transactional_client.put(
        f"/api/v3/packing-setup/products/{product.id}", headers=headers(actor), json=body
    )
    assert response.status_code == 422
    assert product.packing_revision == body["expected_revision"]


def test_prepare_api_requires_staff_mode_selection(db_session, reviewed, transactional_client):
    actor, order, _, _ = reviewed
    assert (
        transactional_client.post(
            f"/api/v3/packing/orders/{order.id}/prepare", headers=headers(actor)
        ).status_code
        == 422
    )
    response = transactional_client.post(
        f"/api/v3/packing/orders/{order.id}/prepare",
        headers=headers(actor),
        json={"packing_mode": "NON_EL", "expected_revision": order.packing_revision},
    )
    assert response.status_code == 200, response.text
    packing = transactional_client.get(
        f"/api/v3/packing/orders/{order.id}/packing-list", headers=headers(actor)
    ).json()
    assert packing["boxes"][0]["packing_mode"] == "NON_EL"
    assert packing["boxes"][0]["volume_cm3"] == 8000


def test_locked_history_ignores_subsequent_profile_changes(db_session, reviewed):
    actor, order, product, _ = reviewed
    box = prepare(db_session, reviewed)
    _, handoff = FinalPackingService(db_session).finalize_order(order_id=order.id, actor=actor)
    snapshot = box.packing_snapshot
    product.packing_revision += 1
    product.packing_profiles[0].volume_cm3 = 9000
    db_session.flush()
    assert not stale_reasons(box)
    assert box.packing_snapshot == snapshot
    _, repeated = FinalPackingService(db_session).finalize_order(order_id=order.id, actor=actor)
    assert repeated.id == handoff.id


def test_coverage_includes_unmatched_skus_and_reference_is_read_only(
    db_session, reviewed, transactional_client
):
    actor, _, product, _ = reviewed
    response = transactional_client.get("/api/v3/packing-setup/products", headers=headers(actor))
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["total_skus"] == 1
    assert data["summary"]["reference_rows"] == 283
    assert data["items"][0]["id"] == product.id
    assert db_session.query(AuditLog).filter_by(event_type="PRODUCT_PACKING_UPDATED").count() == 0


def test_numeric_order_label_needs_an_explicit_reviewed_alias(db_session, reviewed):
    from app.modules.packing.profiles import profile_for

    product = reviewed[2]
    with pytest.raises(ValueError, match="review pack weight and volume"):
        profile_for(product, "500")
    profile = product.packing_profiles[0]
    profile.aliases = ["500"]
    db_session.flush()
    assert profile_for(product, "500").id == profile.id
    profile.reviewed = False
    with pytest.raises(ValueError, match="review pack weight and volume"):
        profile_for(product, "500")


def test_setup_denies_anonymous_and_service_principal_writes(
    db_session, reviewed, transactional_client
):
    from app.models.user import UserRole

    actor, _, product, _ = reviewed
    agent = db_session.query(type(actor)).filter_by(role=UserRole.AI_AGENT_2).one()
    url = f"/api/v3/packing-setup/products/{product.id}"
    assert transactional_client.put(url, json=payload(product)).status_code == 401
    assert (
        transactional_client.put(url, json=payload(product), headers=headers(agent)).status_code
        == 403
    )


def test_new_handoff_signs_packing_rules_and_detects_changes(db_session, reviewed):
    from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService

    actor, order, _, _ = reviewed
    box = prepare(db_session, reviewed, "NON_EL")
    _, handoff = FinalPackingService(db_session).finalize_order(order_id=order.id, actor=actor)
    assert handoff.payload_json["boxes"][0]["packing_mode"] == "NON_EL"
    OnBehalfInvoicingService._assert_packing_matches_handoff([box], handoff)
    box.packing_mode = "EL"
    with pytest.raises(ValueError, match="packing rules differ"):
        OnBehalfInvoicingService._assert_packing_matches_handoff([box], handoff)


def test_template_shortcut_requires_a_mode_and_uses_reviewed_capacity(db_session, reviewed):
    from app.models.user import UserRole

    actor, order, product, _ = reviewed
    actor.role = UserRole.OWNER_ADMIN
    product.packing_profiles[0].volume_cm3 = 12000
    db_session.flush()
    service = FloorPackingService(db_session)
    with pytest.raises(ValueError, match="Select EL or Non-EL"):
        service.fast_track_template_pack_order(order.id, actor)
    assert db_session.query(Box).count() == 0
    result = service.fast_track_template_pack_order(order.id, actor, packing_mode="NON_EL")
    assert result["boxes_count"] == 4
    assert all(box.is_locked and box.packing_mode == "NON_EL" for box in db_session.query(Box))
