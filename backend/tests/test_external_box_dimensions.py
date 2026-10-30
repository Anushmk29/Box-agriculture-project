import hashlib
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_packing_lifecycle import setup_order
from test_simplified_workflow import headers

from app.api.packing_setup import box_out
from app.core.database import Base
from app.models.master_data import BoxCatalogue, BoxMaterial
from app.modules.packing.catalogue import dimension_reference, external_volume_cm3
from app.modules.packing.profiles import box_volume

pytest_plugins = ["test_simplified_workflow"]


def box_payload(box):
    return {
        key: value
        for key, value in box_out(box).items()
        if key
        in {
            "size_name",
            "box_type",
            "length_cm",
            "width_cm",
            "height_cm",
            "max_safe_net_weight_kg",
            "packing_reviewed",
            "is_active",
        }
    } | {"expected_revision": box.packing_revision}


def test_reference_matches_the_supplied_workbook():
    import openpyxl

    reference = dimension_reference()
    assert len(reference["rows"]) == 15
    workbook = Path(__file__).resolve().parents[2] / "documents/box size" / reference["source_file"]
    if not workbook.exists():
        pytest.skip("Original workbook is not part of the deployment")
    assert hashlib.sha256(workbook.read_bytes()).hexdigest() == reference["source_sha256"]
    sheet = openpyxl.load_workbook(workbook, data_only=True)[reference["sheet"]]
    for row in reference["rows"]:
        assert [row[f"{axis}_cm"] for axis in ["length", "width", "height"]] == [
            sheet.cell(row["row"], column).value for column in [3, 5, 7]
        ]


def test_external_volume_ignores_legacy_rounded_cubic_metres(db_session):
    *_, order = setup_order(db_session)
    catalogue = db_session.query(BoxCatalogue).one()
    catalogue.volume_m3 = 0.0001
    assert external_volume_cm3(catalogue) == 14400
    assert box_volume(catalogue) == 14400
    assert box_out(catalogue)["usable_volume_cm3"] == 13248
    assert box_out(catalogue)["dimension_source"]["basis"] == "EXTERNAL"
    assert not any(key.startswith("internal_") for key in box_out(catalogue))


def test_box_api_saves_capacity_without_internal_measurements_and_rejects_other_sizes(
    db_session, transactional_client
):
    _, actor, _, _ = setup_order(db_session)
    catalogue = db_session.query(BoxCatalogue).one()
    body = box_payload(catalogue)
    body["packing_reviewed"] = True
    url = f"/api/v3/packing-setup/boxes/{catalogue.id}"
    response = transactional_client.put(url, headers=headers(actor), json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["usable_volume_cm3"] == 13248
    assert data["dimension_source"]["row"] == 10
    body["expected_revision"] = data["packing_revision"]
    body["length_cm"] = 31
    assert transactional_client.put(url, headers=headers(actor), json=body).status_code == 422
    body["length_cm"] = 30
    body["internal_length_cm"] = 25
    assert transactional_client.put(url, headers=headers(actor), json=body).status_code == 422


def test_new_boxes_must_be_missing_workbook_sizes_not_custom_or_duplicate(
    db_session, transactional_client
):
    _, actor, _, _ = setup_order(db_session)
    catalogue = db_session.query(BoxCatalogue).one()
    body = box_payload(catalogue)
    url = "/api/v3/packing-setup/boxes"
    assert transactional_client.post(url, headers=headers(actor), json=body).status_code == 409
    body.update(size_name="Workbook Flat", length_cm=54, width_cm=42, height_cm=21)
    response = transactional_client.post(url, headers=headers(actor), json=body)
    assert response.status_code == 201, response.text
    assert response.json()["external_volume_cm3"] == 47628
    body["length_cm"] = 55
    assert transactional_client.post(url, headers=headers(actor), json=body).status_code == 422


def test_unlisted_dimensions_are_hidden_and_cannot_supply_packing_volume(
    db_session, transactional_client
):
    _, actor, _, _ = setup_order(db_session)
    unsupported = BoxCatalogue(
        box_type=BoxMaterial.FOAM,
        size_name="Not in workbook",
        length_cm=100,
        width_cm=100,
        height_cm=100,
        max_safe_net_weight_kg=10,
        volume_m3=1,
        rated_weight_kg=10,
        is_active=True,
    )
    db_session.add(unsupported)
    db_session.commit()
    for url in ["/api/v3/packing-setup/boxes", "/api/v3/master-data/boxes"]:
        response = transactional_client.get(url, headers=headers(actor))
        assert response.status_code == 200, response.text
        assert all(row["id"] != unsupported.id for row in response.json())
    with pytest.raises(ValueError, match="workbook|xlsx"):
        box_volume(unsupported)


@pytest.mark.parametrize("fresh", [False, True])
def test_migration_removes_internal_fields_and_preserves_existing_records(fresh):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20261025_0014_external_box_dimensions.py"
    )
    spec = importlib.util.spec_from_file_location("external_box_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        if fresh:
            Base.metadata.create_all(connection)
        else:
            connection.exec_driver_sql(
                "CREATE TABLE box_catalogue (id TEXT PRIMARY KEY, length_cm NUMERIC, width_cm NUMERIC, height_cm NUMERIC, volume_m3 NUMERIC(8,4), internal_length_cm NUMERIC, internal_width_cm NUMERIC, internal_height_cm NUMERIC)"
            )
            connection.exec_driver_sql(
                "INSERT INTO box_catalogue VALUES ('flat',54,42,21,0.0476,50,38,18)"
            )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert not any(
            c["name"].startswith("internal_")
            for c in sa.inspect(connection).get_columns("box_catalogue")
        )
        if not fresh:
            row = connection.execute(
                sa.text("SELECT id,length_cm,width_cm,height_cm,volume_m3 FROM box_catalogue")
            ).one()
            assert tuple(row[:4]) == ("flat", 54, 42, 21)
            assert float(row[4]) == pytest.approx(0.047628)
    engine.dispose()
