"""Temperature choices must produce the workbook's material without hidden defaults."""

import pytest
from test_product_table import edit, setup
from test_simplified_workflow import headers

from app.models.master_data import BoxMaterial, TemperatureZone
from app.modules.packing.materials import material_for_temperature
from app.modules.packing.product_table import apply_reconciliation, reconciliation
from app.modules.packing.reference import reference

pytest_plugins = ["test_simplified_workflow"]


@pytest.mark.parametrize("temperature,material", [("COLD", "FOAM"), ("NORMAL", "CARDBOARD")])
def test_temperature_edit_assigns_material_atomically(db_session, transactional_client, temperature, material):
    actor, _, product = setup(db_session)
    product.packing_source = {"table": {"unresolved": ["temperature", "box_material"]}}
    db_session.commit()
    result = edit(transactional_client, actor, product, {"temperature": temperature})
    assert result["product"]["temperature"] == temperature
    assert result["product"]["box_material"] == material
    assert result["product"]["temperature_source"] == "STAFF"
    db_session.expire_all()
    assert product.temperature == TemperatureZone(temperature)
    assert product.box_material == BoxMaterial(material)
    assert {"temperature", "box_material"} <= set(product.packing_source["table"]["manual_fields"])


@pytest.mark.parametrize("temperature,material", [("COLD", "CARDBOARD"), ("NORMAL", "FOAM")])
def test_invalid_pair_rejects_the_entire_row(db_session, transactional_client, temperature, material):
    actor, _, product = setup(db_session)
    original = (product.temperature, product.box_material, product.packing_revision)
    result = edit(transactional_client, actor, product, {"temperature": temperature, "box_material": material, "volume_l_per_kg": 7})
    assert result["status"] == 422
    assert "Cold products require foam" in result["error"]
    db_session.expire_all()
    assert (product.temperature, product.box_material, product.packing_revision) == original
    assert product.packing_volume_l_per_kg is None


def test_material_only_cannot_resolve_an_unknown_temperature(db_session, transactional_client):
    actor, _, product = setup(db_session)
    product.packing_source = {"table": {"unresolved": ["temperature", "box_material"]}}
    db_session.commit()
    result = edit(transactional_client, actor, product, {"box_material": "FOAM"})
    assert result["status"] == 422
    assert "Choose the product temperature" in result["error"]


def test_confirmation_does_not_invent_a_weight_exception(db_session, transactional_client):
    actor, _, product = setup(db_session)
    product.t_code = "11-034"  # Previously received a hidden one-kilogram exception.
    db_session.commit()
    result = edit(transactional_client, actor, product, {"temperature": "NORMAL", "firmness": "HARD", "volume_l_per_kg": 4, "confirm": True})
    assert result["product"]["ready"]
    assert result["product"]["box_material"] == "CARDBOARD"
    assert result["product"]["foam_below_kg"] is None


def test_temperature_filter_counts_stay_global_and_do_not_expose_unknown_defaults(db_session, transactional_client):
    actor, _, product = setup(db_session)
    product.packing_source = {"table": {"unresolved": ["temperature", "box_material"]}}
    db_session.commit()
    url = "/api/v3/packing-setup/table"
    unknown = transactional_client.get(url + "?temperature=unknown", headers=headers(actor)).json()
    assert any(row["id"] == product.id and row["temperature"] is None for row in unknown["items"])
    assert unknown["summary"]["temperature_unknown"] >= 1
    cold = transactional_client.get(url + "?temperature=COLD", headers=headers(actor)).json()
    assert all(row["temperature"] == "COLD" for row in cold["items"])
    assert product.id not in {row["id"] for row in cold["items"]}
    assert cold["summary"] == unknown["summary"]


def test_reimport_derives_material_from_staff_temperature(db_session):
    _, _, product = setup(db_session)
    product.temperature = TemperatureZone.NORMAL
    product.box_material = BoxMaterial.FOAM
    product.packing_source = {"table": {"manual_fields": ["temperature", "box_material"]}}
    proposal = reconciliation(product, [])
    assert proposal["fields"]["box_material"] == "CARDBOARD"
    revision = product.packing_revision
    apply_reconciliation(db_session, product, [])
    assert product.box_material == BoxMaterial.CARDBOARD
    assert product.packing_revision > revision


def test_every_grouping_row_follows_the_temperature_rule():
    for row in reference()["entries"]:
        if row["temperature"] is not None:
            assert material_for_temperature(row["temperature"]).value == row["box_material"], row["row"]
